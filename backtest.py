"""Walk-forward evaluation for the current clinch_engine.py model.

Each target date is evaluated using only the 2026 input state that was
available by the end of that date when Git history is available.

Reported metrics:
- multiclass log loss (home/draw/away)
- multiclass Brier score
- decided-game accuracy (draws excluded)
- draw-probability calibration
- decided-game probability calibration
"""

import math
from collections import defaultdict

import clinch_engine as m


def brier(p, y):
    return sum((p[i] - y[i]) ** 2 for i in range(3))


def _calibration_row(store):
    if not store["n"]:
        return None
    return {
        "n": store["n"],
        "mean_p": store["p"] / store["n"],
        "actual": store["y"] / store["n"],
    }


def main():
    historical, games_current = m.load_all_games()
    prior = m.estimate_multi_year_prior(historical)
    env = m.estimate_environment(historical)
    rest_effect = m.estimate_rest_effect(historical)

    completed_current = [g for g in games_current if m.is_finished(g)]
    dates = sorted({g["date"] for g in completed_current})

    logloss = 0.0
    brier_sum = 0.0
    decided_correct = 0
    decided_total = 0
    n = 0
    draw_count = 0
    reliability_decided = defaultdict(lambda: {"n": 0, "p": 0.0, "y": 0.0})
    reliability_draw = defaultdict(lambda: {"n": 0, "p": 0.0, "y": 0.0})

    for d in dates:
        games = m.load_2026_games_as_of_date(d) or games_current
        completed_today = [g for g in games if g["date"] == d and m.is_finished(g)]
        if not completed_today:
            continue

        model = m.fit_run_model(games, d, prior, env)
        pitcher_stats = m.build_pitcher_start_stats(games, d)
        draw_rate = m.estimate_current_draw_rate(games, d)

        for g in completed_today:
            h = g["home"]
            a = g["away"]
            stadium = m.STADIUM_NAMES.get(h, "東京D")
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            h_start = g.get("home_starter") if g.get("starter_confirmed") else "未定"
            a_start = g.get("away_starter") if g.get("starter_confirmed") else "未定"
            rest_diff = m.rest_difference_for_game(g, games, as_of_date=d)
            p = m.predict_game(
                model,
                h,
                a,
                stadium,
                h_start or "未定",
                a_start or "未定",
                pitcher_stats,
                rest_diff,
                rest_effect,
                draw_rate,
            )
            probs = [p["home"], p["draw"], p["away"]]

            if hs > as_:
                outcome = [1, 0, 0]
                decided_total += 1
                decided_correct += int(p["home"] >= p["away"])
                outcome_decided = 1.0
            elif hs == as_:
                outcome = [0, 1, 0]
                draw_count += 1
                outcome_decided = None
            else:
                outcome = [0, 0, 1]
                decided_total += 1
                decided_correct += int(p["away"] > p["home"])
                outcome_decided = 0.0

            eps = 1e-9
            logloss -= math.log(max(eps, probs[outcome.index(1)]))
            brier_sum += brier(probs, outcome)
            n += 1

            draw_bin = min(5, max(0, int((p["draw"] * 100.0 - 2.0) // 1.0)))
            reliability_draw[draw_bin]["n"] += 1
            reliability_draw[draw_bin]["p"] += p["draw"]
            reliability_draw[draw_bin]["y"] += 1.0 if hs == as_ else 0.0

            if outcome_decided is not None:
                pred_decided = p["home"] / max(1e-9, p["home"] + p["away"])
                bin_idx = min(9, max(0, int(pred_decided * 10)))
                reliability_decided[bin_idx]["n"] += 1
                reliability_decided[bin_idx]["p"] += pred_decided
                reliability_decided[bin_idx]["y"] += outcome_decided

    print(f"games={n}")
    print(f"draws={draw_count} ({draw_count / max(1, n):.4%})")
    print(f"multiclass log loss={logloss / max(1, n):.5f}")
    print(f"multiclass Brier={brier_sum / max(1, n):.5f}")
    print(f"decided accuracy={decided_correct / max(1, decided_total):.4%} (n={decided_total})")

    print("\nDraw-probability calibration:")
    for b in range(6):
        row = _calibration_row(reliability_draw[b])
        if row is None:
            continue
        lo = 2 + b
        hi = lo + 1
        print(
            f"  {lo:02d}-{hi:02d}%: n={row['n']:4d}, "
            f"mean_p={row['mean_p']:.4f}, actual={row['actual']:.4f}"
        )

    print("\nDecided-game calibration:")
    for b in range(10):
        row = _calibration_row(reliability_decided[b])
        if row is None:
            continue
        print(
            f"  {b*10:02d}-{(b+1)*10:02d}%: n={row['n']:4d}, "
            f"mean_p={row['mean_p']:.3f}, actual={row['actual']:.3f}"
        )


if __name__ == "__main__":
    main()
