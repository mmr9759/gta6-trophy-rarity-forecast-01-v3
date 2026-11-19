#!/usr/bin/env python3
"""GTA VI trophy rarity forecaster (week one) -- v3.

Reads one case per *.in file (schema makefun/gta6-trophies-case/v1): every
valid Exophase read of the game's trophy page before the case checkpoint, and
forecasts, for each trophy, five quantiles (5/25/50/75/95%) of its earn rate
at the case target, in percent.

Method (counts-first, two branches):

* Work in counts, not rates: rate = 100 * earners / players_tracked.
  Exophase only refreshes its counts every so often, so consecutive reads are
  usually identical; the signal is the sequence of DISTINCT snapshots.

* FLAT branch (fewer than 3 distinct snapshots -- the practice cases, whose
  whole history is one unchanged launch-night page): a refresh-inversion
  prior. The one refresh the rules record went from 638 to 3,868 players
  tracked (x6.06); the median next refresh is taken to multiply players by
  that much again, while each trophy's earners grow only modestly (median
  x1.15) -- new players dilute every rate, and only the trophies reachable
  in a few hours of play gain earners fast. The three trophies with the most
  launch-night earners (the easiest/earliest content) get a +0.5 logit
  median boost: in the launch-night refresh the board evidence says the
  top-rate trophies jumped about +1.0 logit while the rest sat near +0.15.
  The spread is honest about a step change nobody has seen: +-0.30 logit
  at the 5/95% quantiles. The forecast deliberately does NOT drift with the
  checkpoint clock: with no refresh observed there is no basis for a
  30-minute rise, and assuming one was v2's costliest error (its p01
  forecast sat x1.286 above its p00 forecast for the same refresh).

* GENERAL branch (3+ distinct snapshots -- the final case, two days of
  reads with real refreshes): fit log earners and log players against
  log(1 + hours) on the last 48 hours of distinct snapshots, shrink each
  trophy's slope toward the pooled median slope, extrapolate from the
  last snapshot with the log-time distance to the target damped by 0.6,
  and set the spread from the fit residuals plus an extrapolation term.
  Anchoring at the last snapshot matters: a global log-time fit
  over-extrapolates when growth saturates, which launch curves do.

A trophy at 100% stays at 100% (it survived the one observed refresh
unchanged). Pure standard library, deterministic, no network, writes only
to the output directory.
"""

import json
import math
import os

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)

# --- flat-branch prior, calibrated on the launch-night refresh ---------
# (rules: players tracked 638 -> 3,868 at the first refresh) and on the
# board inversion of v2 (loss 0.141, coverage 0.67): typical earners
# growth a little above flat, top-rate trophies much faster, and a
# spread far wider than v2's +-0.20 logit band.
PLAYERS_MULT_MED = 3868.0 / 638.0     # 6.063, the one observed refresh
EARNERS_MULT_MED = 1.15               # median earners growth at a refresh
FLAT_OFFSETS = (-0.30, -0.12, 0.0, 0.12, 0.30)   # logit offsets
TOP_K = 3                             # trophies with most earners
TOP_BOOST = 0.5                       # logit boost for those trophies

# --- general-branch tuning --------------------------------------------
SHRINK_PRIOR = 3.0        # pseudo-count pulling a trophy slope to pooled
WINDOW_HOURS = 48.0        # fit the general branch on the last 48h only
EXTRAP_DAMP = 0.6          # damp the log-time distance to the target
SPREAD_FLOOR = 0.22        # min logit sigma for the general branch
SPREAD_CAP = 0.70          # max logit sigma
SPREAD_BASE = 0.08         # irreducible logit sigma
EXTRAP_PER_X = 0.20        # extra logit sigma per log-time unit past data
UPSIDE = 1.15              # upside quantiles are wider (earners surprise up)


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def logit_pct(x):
    p = clamp(x, 0.01, 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit_pct(l):
    return 100.0 / (1.0 + math.exp(-clamp(l, -12.0, 12.0)))


def parse_time(s):
    """ISO 8601 UTC ('...Z') -> seconds since epoch (approx, no tz lib)."""
    import datetime
    return datetime.datetime.strptime(
        s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=datetime.timezone.utc).timestamp() \
        if "." in s else datetime.datetime.strptime(
        s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def distinct_snapshots(history):
    """Collapse consecutive identical reads (same players + same rates)."""
    out = []
    prev = None
    for read in history:
        key = (read.get("players_tracked"),
               tuple(read.get("rates") or ()))
        if key != prev:
            out.append(read)
            prev = key
    return out


def quantiles_from_logit(median_logit, offsets):
    qs = [inv_logit_pct(median_logit + o) for o in offsets]
    qs = [clamp(round(q, 4), 0.0, 100.0) for q in qs]
    for i in range(1, len(qs)):
        if qs[i] < qs[i - 1]:
            qs[i] = qs[i - 1]
    return qs


def flat_branch(last_rates, last_players, n_trophies):
    """Refresh-inversion prior: same forecast whatever the checkpoint."""
    earners = []
    for r in last_rates:
        if r is None:
            earners.append(0.0)
        else:
            earners.append(max(r, 0.0) / 100.0 * last_players)
    order = sorted((i for i in range(n_trophies)
                    if (last_rates[i] or 0.0) < 100.0),
                   key=lambda i: (-earners[i], i))
    top = set(order[:TOP_K])
    rows = []
    for i, r in enumerate(last_rates):
        if r is None:
            r = 0.0
        if r >= 100.0:
            rows.append([100.0, 100.0, 100.0, 100.0, 100.0])
            continue
        med = 100.0 * (earners[i] * EARNERS_MULT_MED) / (
            last_players * PLAYERS_MULT_MED)
        med = clamp(med, 0.01, 99.99)
        lm = logit_pct(med)
        if i in top:
            lm += TOP_BOOST
        rows.append(quantiles_from_logit(lm, FLAT_OFFSETS))
    return rows


def _ols(xs, ys):
    """Least squares y = a + b*x; returns (a, b, resid_std)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        b = 0.0
    else:
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    if n <= 2:
        return a, b, 0.0
    var = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / (n - 2)
    return a, b, math.sqrt(max(var, 0.0))


def general_branch(case, snaps, last_rates, last_players, n_trophies,
                   t_last_s):
    """Log-time trend fit on the distinct snapshots, extrapolated.

    Anchored at the last snapshot (not a global intercept: a global
    log-time fit over-extrapolates when growth saturates, which the
    launch curves do), fitted on the last 48 hours of distinct
    snapshots only, with the log-time distance to the target damped by
    0.6 -- the fitted curve is a local trend, not a law. Per-trophy
    slopes are shrunk toward the pooled median slope.
    """
    t0 = parse_time(snaps[0]["at"])
    # The data is current as of the last READ, even if the last few reads
    # repeat the previous refresh plateau unchanged.
    t_last = t_last_s
    window = [s for s in snaps
              if parse_time(s["at"]) >= t_last - WINDOW_HOURS * 3600.0]
    if len(window) < 3:
        window = snaps
    u = lambda t: math.log1p(max(t - t0, 0.0) / 3600.0)
    xs = [u(parse_time(s["at"])) for s in window]
    a_n, b_n, sd_n = _ols(
        xs, [math.log(max(float(s["players_tracked"]), 1.0))
             for s in window])

    # Per-trophy log-earners fits, slopes shrunk to the pooled median.
    slopes = []
    fits = []
    for i in range(n_trophies):
        tx, ty = [], []
        for s, x in zip(window, xs):
            rates = s.get("rates") or []
            if i < len(rates) and rates[i] is not None:
                e = max(rates[i], 0.0) / 100.0 * max(
                    float(s["players_tracked"]), 1.0)
                tx.append(x)
                ty.append(math.log(e + 0.5))
        if len(tx) >= 2:
            a_i, b_i, _ = _ols(tx, ty)
        elif tx:
            a_i, b_i = ty[0], 0.0
        else:
            a_i, b_i = math.log(0.5), 0.0
        fits.append((a_i, b_i, len(tx)))
        slopes.append(b_i)
    srt = sorted(slopes)
    b_med = srt[len(srt) // 2] if srt else 0.0

    # Pooled residual std of the log-earners fits.
    sq, cnt = 0.0, 0
    for i, (a_i, b_i, n_i) in enumerate(fits):
        for s, x in zip(window, xs):
            rates = s.get("rates") or []
            if i < len(rates) and rates[i] is not None:
                e = max(rates[i], 0.0) / 100.0 * max(
                    float(s["players_tracked"]), 1.0)
                d = math.log(e + 0.5) - (a_i + b_i * x)
                sq += d * d
                cnt += 1
    sd_e = math.sqrt(sq / max(cnt, 1)) if cnt else 0.15

    u_tgt = u(parse_time(case["target"]))
    u_last = u(t_last)
    du = max(u_tgt - u_last, 0.0) * EXTRAP_DAMP
    spread = math.sqrt(sd_e * sd_e + sd_n * sd_n
                       + (EXTRAP_PER_X * du) ** 2 + SPREAD_BASE ** 2)
    spread = clamp(spread, SPREAD_FLOOR, SPREAD_CAP)
    offsets = (Z[0] * spread, Z[1] * spread, 0.0,
               Z[3] * spread * UPSIDE, Z[4] * spread * UPSIDE)

    n_hat = max(float(last_players) * math.exp(b_n * du),
                float(last_players))
    rows = []
    for i, r in enumerate(last_rates):
        if r is None:
            r = 0.0
        if r >= 100.0:
            rows.append([100.0, 100.0, 100.0, 100.0, 100.0])
            continue
        a_i, b_i, n_i = fits[i]
        b_star = (n_i * b_i + SHRINK_PRIOR * b_med) / (n_i + SHRINK_PRIOR)
        e_last = max(r, 0.0) / 100.0 * float(last_players)
        e_hat = max(e_last * math.exp(b_star * du), e_last, 0.0)
        med = clamp(100.0 * e_hat / n_hat, 0.01, 99.99)
        rows.append(quantiles_from_logit(logit_pct(med), offsets))
    return rows


def forecast_case(case):
    trophies = case["trophies"]
    n = len(trophies)
    history = case.get("history") or []
    if not history:
        return [[0.01, 0.25, 1.0, 4.0, 25.0] for _ in range(n)]
    snaps = distinct_snapshots(history)
    last = history[-1]
    last_rates = list(last.get("rates") or [None] * n)
    if len(last_rates) < n:
        last_rates += [None] * (n - len(last_rates))
    last_players = float(last.get("players_tracked") or 638)
    if len(snaps) >= 3:
        return general_branch(case, snaps, last_rates, last_players, n,
                              parse_time(last["at"]))
    return flat_branch(last_rates, last_players, n)


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(f for f in os.listdir(in_dir) if f.endswith(".in"))
    for name in names:
        with open(os.path.join(in_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        rows = forecast_case(case)
        out = {"trophies": [
            {"id": int(t["id"]), "q": rows[i]}
            for i, t in enumerate(case["trophies"])]}
        stem = name[:-3]
        with open(os.path.join(out_dir, stem + ".out"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"))
            fh.write("\n")


if __name__ == "__main__":
    main()
