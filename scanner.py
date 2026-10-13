"""Stale-line scanner: price every Massachusetts book against Pinnacle's no-vig fair line."""
import pandas as pd

from edge import Shifter, fair_lines, price_vs_fair

SHARP = "Pinnacle (sharp reference)"
MIN_EV = 0.02          # alert threshold: +2% expected value
MIN_EV_SHIFTED = 0.03  # stricter when the book's number differs from Pinnacle's (more model risk)


def scan(odds, games):
    shifter = Shifter(games)
    fair = fair_lines(odds, SHARP)
    if fair.empty:
        return pd.DataFrame()
    retail = odds[odds.book != SHARP]
    rows = []
    for _, r in retail.iterrows():
        f = fair[(fair.away == r.away) & (fair.home == r.home) & (fair.market == r.market) & (fair.side == r.side)]
        if f.empty:
            continue
        res = price_vs_fair(shifter, r.market, r.side, r.point, r.price, f.iloc[0])
        if res is None:
            continue
        e, line_gain = res
        label = {"spreads": f"{r.side} {r.point:+g}", "totals": f"{r.side} {r.point:g}",
                 "h2h": f"{r.side} moneyline"}[r.market]
        fq = f.iloc[0]
        fair_label = {"spreads": f"{fq.point:+g}", "totals": f"{fq.point:g}", "h2h": "ML"}[r.market]
        rows.append({
            "commence_time_utc": r.commence_time_utc, "matchup": f"{r.away} @ {r.home}", "book": r.book,
            "bet": label, "price": int(r.price), "ev_pct": round(100 * e, 1),
            "pinnacle_line": fair_label, "pinnacle_fair_prob": round(fq.fair_q, 3),
            "line_diff_pts": round(line_gain, 1), "market": r.market, "side": r.side, "point": r.point})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    shifted = out.line_diff_pts.abs() > 0
    out["flag"] = (out.ev_pct >= 100 * MIN_EV) & ~shifted | (out.ev_pct >= 100 * MIN_EV_SHIFTED) & shifted
    return out.sort_values("ev_pct", ascending=False).reset_index(drop=True)


def alert_text(flagged, pulled_at):
    lines = [f"**+EV lines found ({pulled_at})** — priced against Pinnacle's no-vig line. "
             "Check the price in the app before betting; these can move fast.\n",
             "| Game | Bet | Book | Price | EV | Pinnacle |", "|---|---|---|---|---|---|"]
    for _, r in flagged.iterrows():
        lines.append(f"| {r.matchup} | {r.bet} | {r.book} | {r.price:+d} | +{r.ev_pct}% | {r.pinnacle_line} ({r.pinnacle_fair_prob:.0%}) |")
    lines.append("\nLog any bet you place in `data/bets.csv` so the tracker can measure your CLV.")
    return "\n".join(lines)
