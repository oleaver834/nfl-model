"""Bet tracker: grades your bets and measures closing line value (CLV) against Pinnacle's closing line.

Log bets in data/bets.csv (one row per bet):
  date,book,game,market,pick,line,price,stake
  2026-10-17,DraftKings,NYJ@NE,spread,NE,-6.5,-120,10
  2026-10-17,FanDuel,CHI@ATL,total,Under,49.5,-110,10
  2026-10-17,BetMGM,NO@NYG,moneyline,NYG,,+105,10
market: spread | total | moneyline. pick: team abbreviation, or Over / Under. line: blank for moneyline.
"""
import glob
import os

import numpy as np
import pandas as pd

from edge import Shifter, fair_lines, price_vs_fair, american_to_decimal

SHARP = "Pinnacle (sharp reference)"
MKT = {"spread": "spreads", "total": "totals", "moneyline": "h2h"}


def load_history(hist_dir):
    files = sorted(glob.glob(os.path.join(hist_dir, "*.csv.gz")))
    if not files:
        return pd.DataFrame()
    h = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    h["pulled_utc"] = pd.to_datetime(h.pulled_utc, utc=True, format="ISO8601")
    h["commence"] = pd.to_datetime(h.commence_time_utc, utc=True, format="ISO8601")
    return h


def closing_snapshot(hist, away, home):
    """Last pull before kickoff for this game (the closing line)."""
    g = hist[(hist.away == away) & (hist.home == home) & (hist.pulled_utc < hist.commence)]
    # the close only exists once the game has kicked off
    if g.empty or g.commence.max() > pd.Timestamp.now(tz="UTC"):
        return None, None
    last = g.pulled_utc.max()
    return g[g.pulled_utc == last], last


def grade(bet, games):
    away, home = bet.game.split("@")
    g = games[(games.away_team == away) & (games.home_team == home) & games.home_score.notna()]
    if bet.get("date"):
        g = g[pd.to_datetime(g.gameday) >= pd.to_datetime(bet.date) - pd.Timedelta(days=7)]
    if g.empty:
        return None
    g = g.sort_values("gameday").iloc[0]
    hs, as_ = g.home_score, g.away_score
    if bet.market == "total":
        t = hs + as_
        d = (t - bet.line) if bet.pick == "Over" else (bet.line - t)
    else:
        margin = (hs - as_) if bet.pick == home else (as_ - hs)
        d = margin + (bet.line if bet.market == "spread" else 0)
    return "Win" if d > 0 else ("Loss" if d < 0 else "Push")


def track(bets_path, hist_dir, games):
    if not os.path.exists(bets_path):
        return None, None
    bets = pd.read_csv(bets_path, comment="#").dropna(how="all")
    if bets.empty:
        return bets, "No bets logged yet."
    bets["market"] = bets.market.str.lower().str.strip()
    bets["pick"] = bets.pick.astype(str).str.strip()
    bets["pick"] = bets.pick.where(~bets.pick.str.lower().isin(["over", "under"]), bets.pick.str.title())
    hist = load_history(hist_dir)
    shifter = Shifter(games)
    out = []
    for _, b in bets.iterrows():
        row = b.to_dict()
        away, home = b.game.split("@")
        res = grade(b, games)
        row["result"] = res or "Pending"
        dec = american_to_decimal(b.price)
        row["profit"] = round({"Win": b.stake * (dec - 1), "Loss": -b.stake, "Push": 0.0}.get(res, np.nan), 2)
        row["clv_pct"] = np.nan
        row["closing_line"] = ""
        if not hist.empty:
            snap, when = closing_snapshot(hist, away, home)
            if snap is not None:
                fair = fair_lines(snap, SHARP)
                f = fair[(fair.market == MKT[b.market]) & (fair.side == b.pick)]
                if not f.empty:
                    fr = f.iloc[0]
                    r = price_vs_fair(shifter, MKT[b.market], b.pick,
                                      b.line if b.market != "moneyline" else np.nan, b.price, fr)
                    if r is not None:
                        row["clv_pct"] = round(100 * r[0], 1)
                    row["closing_line"] = (f"{fr.point:+g}" if b.market == "spread" else
                                           f"{fr.point:g}" if b.market == "total" else "ML") + f" ({fr.fair_q:.0%} fair)"
        out.append(row)
    t = pd.DataFrame(out)
    return t, summary(t)


def summary(t):
    done = t[t.result != "Pending"]
    w, l, p = (done.result == "Win").sum(), (done.result == "Loss").sum(), (done.result == "Push").sum()
    staked = done.stake.sum()
    clv = t.clv_pct.dropna()
    lines = ["# Bet tracker", "",
             f"- **Bets:** {len(t)} logged, {len(done)} graded ({w}-{l}-{p})",
             f"- **Profit:** {done.profit.sum():+.2f} on {staked:.2f} staked"
             + (f" ({100 * done.profit.sum() / staked:+.1f}% ROI)" if staked else ""),
             f"- **Average CLV:** {clv.mean():+.1f}% across {len(clv)} bets with a closing line" if len(clv) else
             "- **Average CLV:** no closing lines captured yet",
             f"- **Beat the close:** {(clv > 0).mean():.0%} of bets" if len(clv) else "",
             "", "CLV is the expected value of your bet price measured against Pinnacle's no-vig closing line. "
             "Positive average CLV over 100+ bets is the best sign you have a real edge; win-loss record "
             "is mostly noise at this sample size.", ""]
    if len(clv):
        by = t.dropna(subset=["clv_pct"]).groupby("book").agg(bets=("clv_pct", "size"), avg_clv=("clv_pct", "mean"))
        lines += ["| Book | Bets | Avg CLV |", "|---|---|---|"]
        lines += [f"| {b} | {int(r.bets)} | {r.avg_clv:+.1f}% |" for b, r in by.iterrows()]
    return "\n".join(x for x in lines if x is not None)
