# gta6-trophy-rarity-forecast-01-v3

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mmr9759/gta6-trophy-rarity-forecast-01-v3/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mmr9759/gta6-trophy-rarity-forecast-01-v3/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts every GTA VI PS5 trophy's Exophase earn rate at 2026-11-26T00:00:00Z
as five quantiles (5 / 25 / 50 / 75 / 95%), scored by mean pinball loss on
the logit scale. Counts-first forecaster with two branches: a
refresh-inversion prior when the history is one flat plateau, and a
damped log-time trend fit when the history holds real refreshes.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>

## Method

An earn rate is earners / players-tracked, and the two move in opposite
directions at a refresh: players-tracked jumps as more players register
on Exophase (dilution pulls every rate down) while earners grow only as
players progress (pulls rates up). Fitting the rate alone confuses the
two, so this entry forecasts in counts and divides at the end.

### What v2 got wrong (diagnosed from its board score)

v2 ("Refresh-inversion forecaster with calibrated launch-refresh prior")
scored 0.141 on the board (rank 347, coverage 0.67, typical miss 0.05
points). Inverting that score against the refresh the rules record
(638 -> 3,868 players tracked at 04:30 UTC on 19 Nov) says:

- Its p00 forecast was roughly right (near pure dilution: earners
  +0-1 per trophy at the refresh), but its p01 forecast sat x1.286
  above its p00 forecast -- it assumed rates keep rising steeply for
  30 more minutes, for a second refresh that never came. If the 05:00
  answer is the same refresh, that leg alone cost most of the loss.
- Its centre was too low (its p00 median implies earners x1.05; the
  board implies a typical x1.1-1.25) and its spread far too narrow
  (+-0.20 logit): about a third of trophies fell outside its band.
- The truth is heterogeneous per trophy: the best-fitting implicit
  truth puts the three highest-rate trophies (the earliest content:
  fishing, Chapter 1) about +1.0 logit above v2's median while the rest
  sit near +0.15 logit with ~0.15 scatter. A forecast that is a pure
  function of the last rate cannot capture that -- only a wider spread
  and a per-trophy boost can.

### v3

- Flat branch (history is one plateau): median rate = last rate x
  1.15 earners / 6.06 players (the one observed refresh), quantiles
  +-0.30 / +-0.12 logit, the three trophies with the most earners
  boosted +0.5 logit. Crucially it does NOT drift with the checkpoint
  clock: p00 and p01 get the identical forecast.
- General branch (3+ distinct snapshots): log earners and log players
  fitted against log(1 + hours) on the last 48 hours of distinct
  snapshots, slopes shrunk to the pooled median, extrapolated from the
  last snapshot with the log-time distance damped by 0.6, spread from
  fit residuals plus an extrapolation term. Anchoring at the last
  snapshot matters: a global log-time fit over-extrapolates when
  growth saturates, which launch curves do.
- A trophy at 100% stays at 100% (it survived the one observed
  refresh unchanged).

## Local verification

- Practice cases p00/p01: exit 0, valid output (55 rows, monotone
  quantiles, under the size cap), two runs byte-identical, ~0.04 s
  for both cases. Fresh-checkout run (files only, no repo) also
  exits 0.
- Backtest on the expanded scenario ensemble (practice cases, truth
  = earners/players at the known refresh, N = 3,868):

  | scenario | v2 | v3 |
  |---|---|---|
  | S1 pure dilution (earners x1.0) | 0.1215 | 0.0945 |
  | S2 fit (earners x1.03) | 0.1041 | 0.0800 |
  | S3 fit (earners x1.06) | 0.0890 | 0.0731 |
  | S4 split (x1.0 late / x1.5 early) | 0.1405 | 0.1068 |
  | S5 high (x1.05 + 25% tail x1.6) | 0.1320 | 0.1134 |
  | S6 single (earners x1.18) | 0.0661 | 0.0568 |
  | S7 implicit-v2 truth (inverted from board) | 0.1280 | 0.0799 |
  | S8 slow refresh (N = 2,000) | 0.4854 | 0.4525 |
  | S9 fast refresh (N = 8,000) | 0.6211 | 0.5702 |
  | S10 dilution x3 (N = 11,604) | 1.1657 | 1.1147 |
  | S11 earners x3 | 0.8241 | 0.7872 |
  | **mean, credible S1-S7** | **0.1116** | **0.0863** |
  | **mean, all S1-S11** | **0.3525** | **0.3208** |

  v3 beats v2 on every scenario, and its implicit-truth score
  (0.0799) passes the 0.08 bar -- but its credible mean (0.0863)
  does not reach the 0.05 gate: the ensemble spans mutually
  inconsistent centres (earners x1.0 to x1.18, plus heterogeneous
  splits), and the best single forecast over that span floors near
  0.076. **Verdict: HOLD on the stated gate; do not submit on the
  backtest alone.**
- Synthetic final-like cases (2.5 days of history with refresh
  plateaus, 4.5-day horizon, five worlds: saturating, log-time,
  late-surge, linear-N, stalled-N): v3 mean pinball **0.1270** vs
  v2 **0.1438** and the house baseline ~0.26-0.30 -- better in
  three worlds, tied in one, worse in one (stalled-N, 0.229 vs
  0.208).
