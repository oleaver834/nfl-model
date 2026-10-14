# NFL model, stale-line scanner and CLV tracker

A GitHub Action checks twice an hour and decides what's due:

| Job | When | What it does |
|---|---|---|
| **Stale-line scan** | At least 6 hours since the last pull, 8am–11pm ET, on game weeks | Pulls every book's odds and prices each line against Pinnacle's no-vig fair line. Lines worth +2% or more (+3% when the book's number differs from Pinnacle's) open a GitHub issue, which pushes to your phone. |
| **Model refresh** | Once per slate, 18–30h before the first kickoff that day | Reruns the power-rating model on this season's latest data and writes projections next to the best line at each book. |
| **Closing line** | Within 3 hours of each kickoff | Saves the latest odds so the tracker can measure your CLV. |
| **Bet tracker** | Every run, and whenever you edit `data/bets.csv` | Grades your bets and measures closing line value. |

GitHub drops most scheduled runs, often leaving gaps of several hours, so every window above is
measured against elapsed time or widened enough that a late run still catches it. Nothing depends on
a run landing at a particular minute.

Books: DraftKings, FanDuel, BetMGM, Caesars, Fanatics, theScore Bet, Bally Bet, with Pinnacle as the sharp reference.

## Setup

1. Add your Odds API key: **Settings → Secrets and variables → Actions → New repository secret**, named `ODDS_API_KEY`. Nothing is pulled until this exists, and the run fails with "ODDS_API_KEY secret is not set" so you notice.
2. **Settings → Actions → General → Workflow permissions**: choose **Read and write**.
3. Get phone alerts: install the GitHub mobile app and turn on notifications. New issues in your own repo will notify you.
4. Run it once by hand: **Actions → Update NFL odds, scanner and tracker → Run workflow**, with "force" checked.

## Odds API credits

Each pull costs 3 credits. On the default settings that works out to 25–32 pulls a week, so about
320–410 credits a month depending on how many scheduled runs survive. To scan more often (stale lines
often last minutes, not hours), upgrade the plan and add a repository **variable** (not secret) named
`SCAN_EVERY_HOURS` set to `2` or `1`.

## Logging bets

Add a row to `data/bets.csv` for every bet you place (you can edit it in the GitHub app):

```
date,book,game,market,pick,line,price,stake
2026-10-17,DraftKings,NYJ@NE,spread,NE,-6.5,-120,10
2026-10-17,FanDuel,CHI@ATL,total,Under,49.5,-110,10
2026-10-17,BetMGM,NO@NYG,moneyline,NYG,,105,10
```

`game` is AWAY@HOME using the abbreviations in the alerts. `market` is spread, total or moneyline.
Results land in `data/TRACKER.md` (summary) and `data/bet_tracker.csv` (every bet).

## How to read the results

- **CLV** is the expected value of your price measured against Pinnacle's no-vig closing line. Averaging positive CLV over 100+ bets is the best evidence of a real edge. Win-loss record is mostly noise at that sample size.
- If average CLV is negative after ~150 bets, the approach isn't working; stop and rethink rather than bet more.
- Moving a fair price between nearby numbers (say Pinnacle -7.5 vs a book's -6.5) uses a key-number model fit to 2015–2025 games. It's an estimate, so those alerts need a bigger edge (+3%).

## Google Sheets

Put this in a cell to pull the latest scan into a sheet (Google refreshes it about hourly):

```
=IMPORTDATA("https://raw.githubusercontent.com/oleaver834/nfl-model/main/data/ev_scan.csv")
```

Swap `ev_scan.csv` for any file below, e.g. `best_lines_vs_model.csv` or `bet_tracker.csv`.

## Output files

| File | What's in it |
|---|---|
| `data/ev_scan.csv` | Every book price checked in the latest scan, with its EV |
| `data/best_lines_vs_model.csv` | Best spread, moneyline, over and under at each book, next to the model |
| `data/projections.csv` | Model projections for the next week |
| `data/odds_history/` | Every odds pull (openers, movement and closing lines) |
| `data/TRACKER.md`, `data/bet_tracker.csv` | Your bet results and CLV |

## Caveats

The model was tested on 2024–2026 games it hadn't seen: less accurate than the closing line and no betting edge
against it. Use it to find games worth a look. The scanner is the part aimed at a real edge, and books limit
accounts that consistently beat the close.
