"""Pricing math: no-vig fair odds, and moving a fair probability from one spread/total to a nearby one.

Spreads: P(team margin = m | team line C) is proportional to the historical frequency of |m|
(which carries the key numbers 3, 7, 6, 10, 14) times a normal curve centred on the line.
Checked against 2015-2025 games: a 3-point favourite wins by exactly 3 ~10% of the time
(history 9.4%), a 7-point favourite by exactly 7 ~7% (history 7.3%).
Totals: same idea, with each total's historical frequency relative to a smooth curve.
"""
import numpy as np
import pandas as pd

SIGMA_MARGIN = 13.0
SIGMA_TOTAL = 13.2
MAX_SHIFT = 1.5  # only move a fair price this many points; further is too model-dependent


def american_to_decimal(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def implied(a):
    return 1 / american_to_decimal(a)


def no_vig(price_a, price_b):
    """Fair probability of side A from a two-way market (multiplicative method)."""
    pa, pb = implied(price_a), implied(price_b)
    return pa / (pa + pb)


class Shifter:
    def __init__(self, games):
        g = games[(games.season >= 2015) & (games.game_type == "REG") & games.result.notna()]
        h = g.result.abs().value_counts(normalize=True)
        self.ks = np.arange(-80, 81)
        hk = np.array([h.get(abs(k), 0) / (1 if k == 0 else 2) for k in self.ks])
        self.hk = np.maximum(hk, 1e-5)
        tot = g.total.value_counts(normalize=True)
        self.ts = np.arange(0, 121)
        mu, sd = g.total.mean(), g.total.std()
        smooth = np.exp(-0.5 * ((self.ts - mu) / sd) ** 2)
        smooth /= smooth.sum()
        emp = np.array([tot.get(t, 0) for t in self.ts])
        # shrink each total's key factor toward 1 so thin cells don't dominate
        n = len(g)
        self.kt = (emp * n + 20 * smooth) / (smooth * n + 20 * smooth)

    def margin_mass(self, C):
        w = self.hk * np.exp(-0.5 * ((self.ks + C) / SIGMA_MARGIN) ** 2)
        return dict(zip(self.ks, w / w.sum()))

    def total_mass(self, T):
        w = self.kt * np.exp(-0.5 * ((self.ts - T) / SIGMA_TOTAL) ** 2)
        return dict(zip(self.ts, w / w.sum()))

    def spread_probs(self, L, C, q):
        """(p_win, p_push) for a team getting L points, given fair no-push win prob q at line C."""
        mass = self.margin_mass(C)
        push_c = mass.get(int(-C), 0) if float(C).is_integer() else 0.0
        p_win = q * (1 - push_c)
        lo, hi = sorted((-L, -C))
        between = sum(v for m, v in mass.items() if lo < m <= hi)
        p_win = p_win + between if L > C else p_win - between
        p_push = mass.get(int(-L), 0) if float(L).is_integer() else 0.0
        return max(p_win, 0.0), p_push

    def total_probs(self, side, X, T, q):
        """(p_win, p_push) for Over/Under X, given fair no-push prob q for the same side at total T."""
        mass = self.total_mass(T)
        push_t = mass.get(int(T), 0) if float(T).is_integer() else 0.0
        p_win = q * (1 - push_t)
        if side == "Over":   # wins if total > X
            lo, hi = sorted((X, T))
            between = sum(v for t, v in mass.items() if lo < t <= hi)
            p_win = p_win + between if X < T else p_win - between
        else:                # Under wins if total < X
            lo, hi = sorted((X, T))
            between = sum(v for t, v in mass.items() if lo <= t < hi)
            p_win = p_win + between if X > T else p_win - between
        p_push = mass.get(int(X), 0) if float(X).is_integer() else 0.0
        return max(p_win, 0.0), p_push


def ev(price, p_win, p_push):
    """Expected profit per 1 unit staked."""
    return p_win * (american_to_decimal(price) - 1) - (1 - p_win - p_push)


def fair_lines(odds, book="Pinnacle (sharp reference)"):
    """Fair (no-vig) probabilities from one book's two-way markets.
    Returns rows: away, home, market, side, point, fair_q."""
    s = odds[odds.book == book]
    out = []
    for (away, home, market), g in s.groupby(["away", "home", "market"]):
        if len(g) != 2:
            continue
        a, b = g.iloc[0], g.iloc[1]
        qa = no_vig(a.price, b.price)
        out.append({"away": away, "home": home, "market": market, "side": a.side, "point": a.point, "fair_q": qa})
        out.append({"away": away, "home": home, "market": market, "side": b.side, "point": b.point, "fair_q": 1 - qa})
    return pd.DataFrame(out)


def price_vs_fair(shifter, market, side, point, price, fair_row):
    """EV of a bet at (point, price) against a fair row for the same side. None if too far to shift."""
    q, fp = fair_row.fair_q, fair_row.point
    if market == "h2h":
        return ev(price, q, 0.0), 0.0
    if pd.isna(point) or pd.isna(fp) or abs(point - fp) > MAX_SHIFT:
        return None
    if market == "spreads":
        pw, pp = shifter.spread_probs(point, fp, q)
    else:
        pw, pp = shifter.total_probs(side, point, fp, q)
    # line value in points, from the bettor's side
    line_gain = (point - fp) if market == "spreads" else ((fp - point) if side == "Over" else (point - fp))
    return ev(price, pw, pp), line_gain
