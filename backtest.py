"""Walk-forward evaluation for clinch_engine.py.

This evaluates each completed 2026 game using ONLY information available before
that game date. It reports:
- multiclass log loss (home/draw/away)
- multiclass Brier score
- decided-game accuracy (ignoring draws)
- calibration by probability bins

Run in the repository root after copying clinch_engine.py:
    python backtest_improved.py
"""

import math
from collections import defaultdict
from pathlib import Path

import clinch_engine as m


def brier(p, y):
    return sum((p[i] - y[i]) ** 2 for i in range(3))


def main():
    historical, games = m.load_all_games()
    prior = m.estimate_multi_year_prior(historical)
    env = m.estimate_environment(historical)
    rest_effect = m.estimate_rest_effect(historical)

    completed = [g for g in games if m.is_finished(g)]
    dates = sorted({g["date"] for g in completed})

    logloss = 0.0
    brier_sum = 0.0
    decided_correct = 0
    decided_total = 0
    draw_correct = 0
    draw_total = 0
    n = 0
    reliability = defaultdict(lambda: {"n": 0, "p": 0.0, "y": 0.0})

    for d in dates:
        model = m.fit_run_model(games, d, prior, env)
        pitcher_stats = m.build_pitcher_start_stats(games, d)

        for g in completed:
            if g["date"] != d:
                continue
            h = g["home"]
            a = g["away"]
            stadium = m.STADIUM_NAMES.get(h, "東京D")
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            h_start = g.get("home_starter") if g.get("starter_confirmed") else "未定"
            a_start = g.get("away_starter") if g.get("starter_confirmed") else "未定"
            rest_diff = m.rest_difference_for_game(g, games, as_of_date=d)
            p = m.predict_game(model, h, a, stadium, h_start or "未定", a_start or "未定", pitcher_stats, rest_diff, rest_effect)
            probs = [p["home"], p["draw"], p["away"]]
            outcome = [0, 0, 0]
            if hs > as_:
                outcome[0] = 1
                decided_total += 1
                decided_correct += int(p["home"] >= p["away"])
            elif hs == as_:
                outcome[1] = 1
                draw_total += 1
                draw_correct += int(max(probs) == p["draw"])
            else:
                outcome[2] = 1
                decided_total += 1
                decided_correct += int(p["away"] > p["home"])

            eps = 1e-9
            logloss -= math.log(max(eps, probs[outcome.index(1)]))
            brier_sum += brier(probs, outcome)
            n += 1

            pred_decided = p["home"] / max(1e-9, p["home"] + p["away"])
            outcome_decided = 1.0 if hs > as_ else 0.0 if hs < as_ else None
            if outcome_decided is not None:
                bin_idx = min(9, max(0, int(pred_decided * 10)))
                reliability[bin_idx]["n"] += 1
                reliability[bin_idx]["p"] += pred_decided
                reliability[bin_idx]["y"] += outcome_decided

    print(f"games={n}")
    print(f"multiclass log loss={logloss / max(1,n):.5f}")
    print(f"multiclass Brier={brier_sum / max(1,n):.5f}")
    print(f"decided accuracy={decided_correct / max(1,decided_total):.4%} (n={decided_total})")
    print(f"draw hit rate={draw_correct / max(1,draw_total):.4%} (n={draw_total})")
    print("\nDecided-game calibration:")
    for b in range(10):
        row = reliability[b]
        if row["n"] == 0:
            continue
        print(
            f"  {b*10:02d}-{(b+1)*10:02d}%: n={row['n']:4d}, "
            f"mean_p={row['p']/row['n']:.3f}, actual={row['y']/row['n']:.3f}"
        )


if __name__ == "__main__":
    main()
