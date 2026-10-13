# NFL model and odds feed

Every hour a GitHub Action checks the NFL schedule. When a kickoff is about 24 hours away, it:

1. Pulls this season's play-by-play from nflverse and reruns the power-rating model (spreads and totals, with QB adjustments).
2. Pulls current odds from DraftKings, FanDuel, BetMGM, Caesars, Fanatics, theScore Bet and Bally Bet, plus Pinnacle as a sharp reference, via The Odds API.
3. Writes the results to `data/` and commits them.

Each odds pull costs 3 API credits, about 6 pulls a week.

## Output files

| File | What's in it |
|---|---|
| `data/best_lines_vs_model.csv` | For each game: the best spread, moneyline, over and under across the Massachusetts books, next to the model's line, total and gaps |
| `data/odds_latest.csv` | Every book's price for every market |
| `data/projections.csv` | Model projections for the next week |

## Setup

1. Add your Odds API key: **Settings → Secrets and variables → Actions → New repository secret**, named `ODDS_API_KEY`.
2. Check **Settings → Actions → General → Workflow permissions** is set to **Read and write**.
3. Run it once by hand: **Actions → Update NFL odds and projections → Run workflow**, with "force" checked.

## Google Sheets

If the repo is public, put this in a cell to pull the latest file (it refreshes about hourly):

```
=IMPORTDATA("https://raw.githubusercontent.com/oleaver834/nfl-model/main/data/best_lines_vs_model.csv")
```

The repo holds no secrets (the key lives only in GitHub's encrypted secrets), so making it public only exposes odds and projections.

## Caveats

The model was tested on 2024–2026 games it hadn't seen. It is less accurate than the closing line and showed no betting edge against it. Use its lines to find games worth a closer look, not as picks.
