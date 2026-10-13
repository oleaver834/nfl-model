"""Hourly job: if any NFL game kicks off in about 24 hours (or --force), pull fresh
book-by-book odds, rerun the model, and write the output CSVs in data/.

Odds credits are only spent when a kickoff window is ~24h away, not on every hourly check.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
ET = ZoneInfo("America/New_York")
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{}.csv.gz"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
# Books live in Massachusetts, plus Pinnacle as the sharp reference price.
BOOKS = ["draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics", "espnbet", "ballybet", "pinnacle"]
BOOK_NAMES = {"williamhill_us": "Caesars", "espnbet": "theScore Bet", "draftkings": "DraftKings",
              "fanduel": "FanDuel", "betmgm": "BetMGM", "fanatics": "Fanatics", "ballybet": "Bally Bet",
              "pinnacle": "Pinnacle (sharp reference)"}
TEAM_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"}
STATE = os.path.join(D, "state.json")


def download(url, path):
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def kickoffs(games):
    g = games[games.home_score.isna() & games.gameday.notna() & games.gametime.notna()].copy()
    g["kickoff"] = [datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
                    for d, t in zip(g.gameday, g.gametime)]
    return g


def due_window(games, now, state):
    """Kickoff time ~24h out (within the next 23-25h) whose model refresh hasn't run yet."""
    g = kickoffs(games)
    soon = g[(g.kickoff >= now + timedelta(hours=23)) & (g.kickoff < now + timedelta(hours=25))]
    for k in sorted(soon.kickoff.unique()):
        key = pd.Timestamp(k).isoformat()
        if key not in state.get("fetched", []):
            return key
    return None


def closing_due(games, now, state):
    """Kickoff within the next 75 minutes whose closing line hasn't been captured yet."""
    g = kickoffs(games)
    soon = g[(g.kickoff > now) & (g.kickoff <= now + timedelta(minutes=75))]
    for k in sorted(soon.kickoff.unique()):
        key = pd.Timestamp(k).isoformat()
        if key not in state.get("closed", []):
            return key
    return None


def scan_due(games, now):
    """Regular scan: every SCAN_EVERY_HOURS hours, 8am-11pm ET, when a game is within 4 days."""
    every = int(os.environ.get("SCAN_EVERY_HOURS", "6") or 6)
    if not (8 <= now.hour <= 23) or now.hour % every:
        return False
    g = kickoffs(games)
    return bool(((g.kickoff > now) & (g.kickoff <= now + timedelta(days=4))).any())


def rebuild_current_season(season):
    """Aggregate this season's play-by-play into the same shape as the history files."""
    path = os.path.join(D, "pbp_current.csv.gz")
    download(PBP_URL.format(season), path)
    p = pd.read_csv(path, low_memory=False)
    os.remove(path)
    p = p[p.season_type == "REG"]
    plays = p[((p["pass"] == 1) | (p["rush"] == 1)) & p.epa.notna() & p.posteam.notna()].copy()
    plays["ng"] = plays.wp.between(0.10, 0.90)
    t = plays.groupby(["season", "week", "game_id", "posteam"]).agg(
        plays=("epa", "size"), epa=("epa", "mean"), sr=("success", "mean")).reset_index()
    ng = plays[plays.ng].groupby(["game_id", "posteam"]).agg(epa_ng=("epa", "mean"), plays_ng=("epa", "size")).reset_index()
    t = t.merge(ng, on=["game_id", "posteam"], how="left")
    qb = (plays[plays.qb_dropback == 1].groupby(["game_id", "posteam", "passer_player_id", "passer_player_name"])
          .size().reset_index(name="db").sort_values("db").groupby(["game_id", "posteam"]).tail(1))
    t = t.merge(qb[["game_id", "posteam", "passer_player_name"]].rename(columns={"passer_player_name": "qb"}),
                on=["game_id", "posteam"], how="left")
    t.to_csv(os.path.join(D, "current_team_game_epa.csv"), index=False)
    db = p[(p.qb_dropback == 1) & p.epa.notna() & p.id.notna()]
    q = db.groupby(["season", "week", "game_id", "posteam", "id", "name"]).agg(
        dropbacks=("epa", "size"), epa_sum=("epa", "sum")).reset_index()
    q.to_csv(os.path.join(D, "current_qb_game.csv"), index=False)


def fetch_odds(api_key):
    r = requests.get(ODDS_URL, timeout=60, params={
        "apiKey": api_key, "markets": "h2h,spreads,totals", "oddsFormat": "american",
        "bookmakers": ",".join(BOOKS)})
    r.raise_for_status()
    print("odds credits used:", r.headers.get("x-requests-used"), "remaining:", r.headers.get("x-requests-remaining"))
    rows = []
    for ev in r.json():
        home, away = TEAM_ABBR.get(ev["home_team"], ev["home_team"]), TEAM_ABBR.get(ev["away_team"], ev["away_team"])
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for oc in mk.get("outcomes", []):
                    side = TEAM_ABBR.get(oc["name"], oc["name"])
                    rows.append({"commence_time_utc": ev["commence_time"], "away": away, "home": home,
                                 "book": BOOK_NAMES.get(bk["key"], bk["title"]), "market": mk["key"],
                                 "side": side, "point": oc.get("point"), "price": oc.get("price"),
                                 "book_updated": bk.get("last_update")})
    return pd.DataFrame(rows)


def best_lines(odds):
    """Best spread for each side (more points first, then better price) and best total/moneyline, excluding Pinnacle."""
    o = odds[~odds.book.str.startswith("Pinnacle")]
    out = []
    for (away, home), g in o.groupby(["away", "home"]):
        rec = {"matchup": f"{away} @ {home}"}
        for label, team in (("away", away), ("home", home)):
            s = g[(g.market == "spreads") & (g.side == team)].sort_values(["point", "price"], ascending=False)
            if len(s):
                b = s.iloc[0]
                rec[f"{label}_best_spread"] = f"{team} {b.point:+g} ({b.price:+.0f}) @ {b.book}"
            m = g[(g.market == "h2h") & (g.side == team)].sort_values("price", ascending=False)
            if len(m):
                rec[f"{label}_best_ml"] = f"{m.iloc[0].price:+.0f} @ {m.iloc[0].book}"
        ov = g[(g.market == "totals") & (g.side == "Over")].sort_values(["point", "price"], ascending=[True, False])
        un = g[(g.market == "totals") & (g.side == "Under")].sort_values(["point", "price"], ascending=[False, False])
        if len(ov):
            rec["best_over"] = f"o{ov.iloc[0].point:g} ({ov.iloc[0].price:+.0f}) @ {ov.iloc[0].book}"
        if len(un):
            rec["best_under"] = f"u{un.iloc[0].point:g} ({un.iloc[0].price:+.0f}) @ {un.iloc[0].book}"
        out.append(rec)
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="pull odds and rebuild everything now")
    args = ap.parse_args()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    download(GAMES_URL, os.path.join(D, "games.csv"))
    games = pd.read_csv(os.path.join(D, "games.csv"))
    now = datetime.now(timezone.utc).astimezone(ET)
    stamp = now.strftime("%Y-%m-%d %H:%M ET")

    model_key = due_window(games, now, state)
    close_key = closing_due(games, now, state)
    scanning = scan_due(games, now)
    pull = args.force or model_key or close_key or scanning
    print(f"{stamp}: model refresh={bool(model_key or args.force)} closing={bool(close_key)} scan={scanning}")

    if pull:
        api_key = os.environ.get("ODDS_API_KEY")
        if not api_key:
            sys.exit("ODDS_API_KEY secret is not set.")
        odds = fetch_odds(api_key)
        odds.insert(0, "pulled_utc", now.astimezone(timezone.utc).isoformat())
        os.makedirs(os.path.join(D, "odds_history"), exist_ok=True)
        odds.to_csv(os.path.join(D, "odds_history", now.strftime("%Y-%m-%d_%H%M") + ".csv.gz"), index=False)
        odds.insert(0, "pulled_at", stamp)
        odds.to_csv(os.path.join(D, "odds_latest.csv"), index=False)

        # stale-line scan on every pull
        from scanner import scan, alert_text
        sc = scan(odds, games)
        if not sc.empty:
            sc.insert(0, "pulled_at", stamp)
            sc.to_csv(os.path.join(D, "ev_scan.csv"), index=False)
            flagged = sc[sc.flag]
            seen = set(state.get("alerted", []))
            keys = flagged.matchup + "|" + flagged.book + "|" + flagged.bet + "|" + flagged.price.astype(str)
            new = flagged[~keys.isin(seen)]
            if len(new):
                with open(os.path.join(D, "alert.md"), "w") as f:
                    f.write(alert_text(new, stamp))
                state["alerted"] = (list(seen) + list(keys[~keys.isin(seen)]))[-500:]
            print(f"scan: {len(sc)} prices checked, {len(flagged)} flagged, {len(new)} new")

        if model_key or args.force:
            season = int(games[games.home_score.isna()].season.min())
            rebuild_current_season(season)
            import model  # imported after the data refresh so it reads the new files
            from projections import project
            proj = project(model)
            proj.to_csv(os.path.join(D, "projections.csv"), index=False)
            bl = best_lines(odds.drop(columns=["pulled_utc"]))
            bl.insert(0, "pulled_at", stamp)
            bl.merge(proj, on="matchup", how="left").to_csv(os.path.join(D, "best_lines_vs_model.csv"), index=False)
            if model_key:
                state["fetched"] = (state.get("fetched", []) + [model_key])[-60:]
        if close_key:
            state["closed"] = (state.get("closed", []) + [close_key])[-60:]
        state["last_pull"] = stamp

    # bet tracker runs every time (no API cost)
    from tracker import track
    t, summ = track(os.path.join(D, "bets.csv"), os.path.join(D, "odds_history"), games)
    if t is not None and len(t):
        t.to_csv(os.path.join(D, "bet_tracker.csv"), index=False)
    if summ:
        open(os.path.join(D, "TRACKER.md"), "w").write(summ + "\n")
    json.dump(state, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main()
