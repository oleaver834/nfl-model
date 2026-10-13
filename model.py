"""Model v2: spread ratings + totals, with QB adjustment and recency weighting.

Walk-forward: predictions for (season, week) use only games and QB plays before that week.
Spread: ridge margin ratings (blend of actual and EPA margin) shrunk to last season (regressed).
Totals: ridge offense/defense points ratings + league mean, shrunk the same way.
QB: each QB's EPA/dropback history (decayed by season, shrunk to a backup-level prior);
    team rating is shifted by k x (this week's starter value - value of the QBs the rating was built on).
"""
import numpy as np
import pandas as pd

import os
HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
G = pd.read_csv(os.path.join(D, "games.csv"))
G = G[(G.game_type == "REG") & (G.season >= 2018)].copy()
for c in ("home_team", "away_team"):
    G[c] = G[c].replace({"OAK": "LV"})
TG = pd.concat([pd.read_csv(os.path.join(D, f)) for f in ("team_game_epa.csv", "current_team_game_epa.csv") if os.path.exists(os.path.join(D, f))])
TG["posteam"] = TG.posteam.replace({"OAK": "LV"})
TG["epa_use"] = TG.epa_ng.fillna(TG.epa)
h = TG[["game_id", "posteam", "epa_use"]]
G = G.merge(h.rename(columns={"posteam": "home_team", "epa_use": "h_epa"}), on=["game_id", "home_team"], how="left")
G = G.merge(h.rename(columns={"posteam": "away_team", "epa_use": "a_epa"}), on=["game_id", "away_team"], how="left")
G["margin"] = G.home_score - G.away_score
G["total_pts"] = G.home_score + G.away_score
G["epa_margin"] = (G.h_epa - G.a_epa) * 28.0
G["neutral"] = (G.location == "Neutral").astype(float)
G["outdoor"] = G.roof.isin(["outdoors", "open"]).astype(float)
G = G.sort_values(["season", "week"]).reset_index(drop=True)
CURRENT_SEASON = int(G[G.home_score.isna()].season.min()) if G.home_score.isna().any() else int(G.season.max())
TEAMS = sorted(set(G.home_team) | set(G.away_team))
IDX = {t: i for i, t in enumerate(TEAMS)}
N = len(TEAMS)

# EPA-implied points for a team-game (fit on 2018-2023 only, to avoid peeking)
_t = TG.merge(G[["game_id", "home_team", "home_score", "away_score"]], on="game_id")
_t["pts"] = np.where(_t.posteam == _t.home_team, _t.home_score, _t.away_score)
_fit = _t[_t.season <= 2023].dropna(subset=["pts", "epa_use"])
B1, B0 = np.polyfit(_fit.epa_use, _fit.pts, 1)
G["h_pts_epa"] = B0 + B1 * G.h_epa
G["a_pts_epa"] = B0 + B1 * G.a_epa

QB = pd.concat([pd.read_csv(os.path.join(D, f)) for f in ("qb_game.csv", "current_qb_game.csv") if os.path.exists(os.path.join(D, f))])
QB_PRIOR_MEAN, QB_PRIOR_N, QB_SEASON_DECAY = -0.05, 250.0, 0.7


def qb_values(season, week):
    """QB EPA/dropback estimates using plays strictly before (season, week)."""
    q = QB[(QB.season < season) | ((QB.season == season) & (QB.week < week))]
    w = QB_SEASON_DECAY ** (season - q.season)
    agg = pd.DataFrame({"id": q.id, "db": q.dropbacks * w, "epa": q.epa_sum * w}).groupby("id").sum()
    return ((agg.epa + QB_PRIOR_N * QB_PRIOR_MEAN) / (agg.db + QB_PRIOR_N)).to_dict()


def qv(vals, qid):
    return vals.get(qid, QB_PRIOR_MEAN) if isinstance(qid, str) else np.nan


class Model:
    def __init__(self, alpha=0.5, lam=6.0, hfa=1.0, regress=0.7, decay=1.0, k=0.0,
                 t_alpha=0.7, t_lam=6.0, k_t=0.0, wind=0.0, t_decay=1.0):
        self.__dict__.update(locals())
        del self.__dict__["self"]

    # ---------- spread ----------
    def fit_spread(self, games, week, prior):
        A = self.lam * np.eye(N); b = self.lam * prior.copy()
        if len(games):
            wts = self.decay ** (week - games.week.values)
            y = self.alpha * games.margin.values + (1 - self.alpha) * games.epa_margin.fillna(games.margin).values
            y = y - self.hfa * (1 - games.neutral.values)
            hi = games.home_team.map(IDX).values; ai = games.away_team.map(IDX).values
            np.add.at(A, (hi, hi), wts); np.add.at(A, (ai, ai), wts)
            np.add.at(A, (hi, ai), -wts); np.add.at(A, (ai, hi), -wts)
            np.add.at(b, hi, wts * y); np.add.at(b, ai, -wts * y)
        r = np.linalg.solve(A + 1e-6 * np.eye(N), b)
        return r - r.mean()

    # ---------- totals: params [mu, off(32), def(32)] ----------
    def fit_totals(self, games, week, prior_o, prior_d, prior_mu):
        P = 1 + 2 * N
        A = np.zeros((P, P)); b = np.zeros(P)
        A[0, 0] += 0.5; b[0] += 0.5 * prior_mu
        for i in range(N):
            A[1 + i, 1 + i] += self.t_lam; b[1 + i] += self.t_lam * prior_o[i]
            A[1 + N + i, 1 + N + i] += self.t_lam; b[1 + N + i] += self.t_lam * prior_d[i]
        if len(games):
            wts = self.t_decay ** (week - games.week.values)
            hi = games.home_team.map(IDX).values; ai = games.away_team.map(IDX).values
            for pts, pe, off, de in ((games.home_score.values, games.h_pts_epa.values, hi, ai),
                                     (games.away_score.values, games.a_pts_epa.values, ai, hi)):
                y = self.t_alpha * pts + (1 - self.t_alpha) * np.where(np.isnan(pe), pts, pe)
                rows = [np.zeros(len(y), int), 1 + off, 1 + N + de]
                for r1 in rows:
                    for r2 in rows:
                        np.add.at(A, (r1, r2), wts)
                    np.add.at(b, r1, wts * y)
        x = np.linalg.solve(A + 1e-6 * np.eye(P), b)
        mu, o, d = x[0], x[1:1 + N], x[1 + N:]
        return mu + o.mean() + d.mean(), o - o.mean(), d - d.mean()

    def _avg_qb(self, games, vals):
        num = np.zeros(N); den = np.zeros(N)
        for side in ("home", "away"):
            ti = games[f"{side}_team"].map(IDX).values
            v = np.array([qv(vals, q) for q in games[f"{side}_qb_id"]])
            ok = ~np.isnan(v)
            np.add.at(num, ti[ok], v[ok]); np.add.at(den, ti[ok], 1.0)
        return np.where(den > 0, num / np.maximum(den, 1e-9), QB_PRIOR_MEAN)

    def base_qb(self, games, week, vals, prior_games):
        """Recency-weighted average value of the QBs a team's rating was built on, shrunk to last season's
        starters. Every QB is valued with the same, current estimates so the comparison is like for like."""
        prior_base = self._avg_qb(prior_games, vals) if prior_games is not None else np.full(N, QB_PRIOR_MEAN)
        num = self.lam * prior_base; den = np.full(N, self.lam)
        if len(games):
            wts = self.decay ** (week - games.week.values)
            for side in ("home", "away"):
                ti = games[f"{side}_team"].map(IDX).values
                v = np.array([qv(vals, q) for q in games[f"{side}_qb_id"]])
                ok = ~np.isnan(v)
                np.add.at(num, ti[ok], wts[ok] * v[ok]); np.add.at(den, ti[ok], wts[ok])
        return num / den

    def run(self, seasons=None):
        seasons = seasons or range(2018, CURRENT_SEASON + 1)
        prior_r = np.zeros(N); prior_o = np.zeros(N); prior_d = np.zeros(N); prior_mu = 22.0
        prior_games = None
        out = []
        self.state = {}
        for s in seasons:
            gs = G[G.season == s]
            weeks = sorted(gs.week.unique())
            for w in weeks:
                past = gs[(gs.week < w) & gs.margin.notna()]
                r = self.fit_spread(past, w, prior_r)
                mu, o, d = self.fit_totals(past, w, prior_o, prior_d, prior_mu)
                vals = qb_values(s, w)
                base = self.base_qb(past, w, vals, prior_games)
                cur = gs[gs.week == w].copy()
                hi = cur.home_team.map(IDX).values; ai = cur.away_team.map(IDX).values
                hq = np.array([qv(vals, q) for q in cur.home_qb_id]); aq = np.array([qv(vals, q) for q in cur.away_qb_id])
                h_adj = np.nan_to_num(hq - base[hi]); a_adj = np.nan_to_num(aq - base[ai])
                cur["qb_adj_home_pts"] = self.k * h_adj
                cur["qb_adj_away_pts"] = self.k * a_adj
                cur["proj_home_margin"] = (r[hi] - r[ai] + self.hfa * (1 - cur.neutral.values)
                                           + self.k * (h_adj - a_adj))
                wind_pen = self.wind * np.clip(cur.wind.fillna(0).values - 12, 0, None) * cur.outdoor.values
                hp = mu + o[hi] + d[ai] + self.k_t * h_adj
                ap = mu + o[ai] + d[hi] + self.k_t * a_adj
                cur["proj_home_pts"] = hp - wind_pen / 2
                cur["proj_away_pts"] = ap - wind_pen / 2
                cur["proj_total"] = cur.proj_home_pts + cur.proj_away_pts
                cur["home_qb_value"] = hq; cur["away_qb_value"] = aq
                cur["home_base_qb"] = base[hi]; cur["away_base_qb"] = base[ai]
                out.append(cur)
                self.state = {"season": s, "week": w, "ratings": dict(zip(TEAMS, r)),
                              "off": dict(zip(TEAMS, o)), "def": dict(zip(TEAMS, d)), "mu": mu,
                              "base_qb": dict(zip(TEAMS, base)), "qb_vals": vals}
            done = gs[gs.margin.notna()]
            if s < CURRENT_SEASON and len(done):
                last_w = int(done.week.max()) + 1
                fr = self.fit_spread(done, last_w, prior_r)
                fmu, fo, fd = self.fit_totals(done, last_w, prior_o, prior_d, prior_mu)
                prior_r, prior_o, prior_d, prior_mu = fr * self.regress, fo * self.regress, fd * self.regress, fmu
                prior_games = done
        return pd.concat(out)


def evaluate(df, seasons):
    d = df[df.season.isin(seasons) & df.margin.notna() & df.spread_line.notna() & (df.week >= 2)]
    return {"games": len(d),
            "spread_mae": (d.proj_home_margin - d.margin).abs().mean(),
            "mkt_spread_mae": (d.spread_line - d.margin).abs().mean(),
            "total_mae": (d.proj_total - d.total_pts).abs().mean(),
            "mkt_total_mae": (d.total_line - d.total_pts).abs().mean()}
