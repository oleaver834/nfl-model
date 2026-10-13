"""Model projections for the next unplayed NFL week."""
from math import erf, sqrt

import numpy as np
import pandas as pd

PARAMS = dict(alpha=0.5, lam=10, hfa=1.0, regress=0.7, decay=1.0, k=25,
              t_alpha=0.7, t_lam=12, t_decay=0.9, k_t=20, wind=0.6)


def project(model):
    wf = model.Model(**PARAMS).run()
    G = model.G
    season = model.CURRENT_SEASON
    week = int(G[(G.season == season) & G.margin.isna()].week.min())
    up = wf[(wf.season == season) & (wf.week == week)]
    rows = []
    for _, g in up.iterrows():
        h, a, proj, mkt = g.home_team, g.away_team, g.proj_home_margin, g.spread_line
        gap = proj - mkt if pd.notna(mkt) else np.nan
        tgap = g.proj_total - g.total_line if pd.notna(g.total_line) else np.nan
        qb = [f"{t}: {n} {adj:+.1f} pts" for t, n, adj in
              ((h, g.home_qb_name, g.qb_adj_home_pts), (a, g.away_qb_name, g.qb_adj_away_pts)) if abs(adj) >= 1]
        rows.append({
            "week": week, "matchup": f"{a} @ {h}", "kickoff_et": f"{g.gameday} {g.gametime}",
            "consensus_line": f"{h} {-mkt:+g}" if pd.notna(mkt) else "",
            "model_line": f"{h} {-proj:+.1f}",
            "blend_line": f"{h} {-(0.35 * proj + 0.65 * mkt):+.1f}" if pd.notna(mkt) else "",
            "spread_gap": round(gap, 1) if pd.notna(gap) else None,
            "model_side_gap3": (f"{h} {-mkt:+g}" if gap > 0 else f"{a} {mkt:+g}") if pd.notna(gap) and abs(gap) >= 3 else "",
            "consensus_total": g.total_line, "model_total": round(g.proj_total, 1),
            "total_gap": round(tgap, 1) if pd.notna(tgap) else None,
            "model_total_side_gap3": (("Over " if tgap > 0 else "Under ") + f"{g.total_line:g}") if pd.notna(tgap) and abs(tgap) >= 3 else "",
            "proj_score": f"{a} {g.proj_away_pts:.0f} - {h} {g.proj_home_pts:.0f}",
            "home_win_prob": round(0.5 * (1 + erf(proj / (13.5 * sqrt(2)))), 3),
            "qb_adjustments": "; ".join(qb),
        })
    return pd.DataFrame(rows)
