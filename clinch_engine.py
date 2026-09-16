import datetime
import json
import os

TOTAL_GAMES = 143
GAMES_INTRA = 25  # 同一リーグ内対戦総数
DB_FILE = "games_db.json"
HISTORY_FILE = "history_standings.json"

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

BASELINE_RECORDS = {
    "阪神": {"games": 127, "win": 69, "lose": 57, "draw": 1},
    "巨人": {"games": 131, "win": 70, "lose": 59, "draw": 2},
    "ＤｅＮＡ": {"games": 130, "win": 64, "lose": 63, "draw": 3},
    "ヤクルト": {"games": 129, "win": 56, "lose": 71, "draw": 2},
    "中日": {"games": 133, "win": 57, "lose": 74, "draw": 2},
    "広島": {"games": 126, "win": 52, "lose": 70, "draw": 4},
    "ソフトバンク": {"games": 127, "win": 79, "lose": 45, "draw": 3},
    "日本ハム": {"games": 129, "win": 68, "lose": 53, "draw": 8},
    "ロッテ": {"games": 126, "win": 63, "lose": 57, "draw": 6},
    "楽天": {"games": 125, "win": 60, "lose": 62, "draw": 3},
    "オリックス": {"games": 128, "win": 57, "lose": 68, "draw": 3},
    "西武": {"games": 129, "win": 43, "lose": 84, "draw": 2}
}

BASELINE_H2H_PLAYED = {
    "阪神": {"巨人": 23, "ＤｅＮＡ": 22, "ヤクルト": 21, "中日": 22, "広島": 21},
    "巨人": {"阪神": 23, "ＤｅＮＡ": 23, "ヤクルト": 23, "中日": 22, "広島": 22},
    "ＤｅＮＡ": {"阪神": 22, "巨人": 23, "ヤクルト": 22, "中日": 23, "広島": 22},
    "ヤクルト": {"阪神": 21, "巨人": 23, "ＤｅＮＡ": 22, "中日": 23, "広島": 22},
    "中日": {"阪神": 22, "巨人": 22, "ＤｅＮＡ": 23, "ヤクルト": 23, "広島": 25},
    "広島": {"阪神": 21, "巨人": 22, "ＤｅＮＡ": 22, "ヤクルト": 22, "中日": 25}
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "season": 2026,
        "teams": {"central": CENTRAL_TEAMS, "pacific": PACIFIC_TEAMS},
        "games": []
    }

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def build_standings_at_date(db, target_date_str):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    records = {}
    for t in all_teams:
        b = BASELINE_RECORDS.get(t, {"games": 0, "win": 0, "lose": 0, "draw": 0})
        records[t] = {
            "team": t,
            "games": b["games"],
            "win": b["win"],
            "lose": b["lose"],
            "draw": b["draw"],
            "remaining": TOTAL_GAMES - b["games"]
        }

    h2h_played = {}
    for t1 in all_teams:
        h2h_played[t1] = {}
        for t2 in all_teams:
            if t1 in BASELINE_H2H_PLAYED and t2 in BASELINE_H2H_PLAYED[t1]:
                h2h_played[t1][t2] = BASELINE_H2H_PLAYED[t1][t2]
            else:
                h2h_played[t1][t2] = 21

    for g in db.get("games", []):
        g_date = g.get("date", "")
        if g.get("status") == "finished" and "2026-09-14" < g_date <= target_date_str:
            h, a = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            if h not in records or a not in records:
                continue

            records[h]["games"] += 1
            records[a]["games"] += 1
            records[h]["remaining"] -= 1
            records[a]["remaining"] -= 1
            h2h_played[h][a] += 1
            h2h_played[a][h] += 1

            if hs > as_:
                records[h]["win"] += 1
                records[a]["lose"] += 1
            elif hs < as_:
                records[a]["win"] += 1
                records[h]["lose"] += 1
            else:
                records[h]["draw"] += 1
                records[a]["draw"] += 1

    def format_league(team_list):
        res = []
        for t in team_list:
            r = records[t]
            r["rate"] = calc_win_rate(r["win"], r["lose"])
            res.append(r)
        res.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
        top_w, top_l = res[0]["win"], res[0]["lose"]
        for idx, t in enumerate(res):
            t["rank"] = idx + 1
            diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
            t["diff"] = max(0.0, diff) if idx > 0 else 0.0
        return res

    return {
        "central": format_league(CENTRAL_TEAMS),
        "pacific": format_league(PACIFIC_TEAMS)
    }, h2h_played

def evaluate_target_clinch(team_a, target_k, all_teams, h2h_played):
    """
    team_a が target_k 位以内を確定させるための条件を厳密計算
    target_k: 1(優勝), 2(2位以上), 3(3位以上), 4(4位以上), 5(5位以上)
    """
    ta = team_a["team"]
    rem_a = TOTAL_GAMES - team_a["games"]
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)

    # 1. 完全消滅（エリミネーション）判定：
    # target_k 位以上になるためには、「自チームより上のチームが target_k チーム未満」でなければならない。
    # すでに target_k 以上のチームが、自チームの最高可能勝率を上回る最低保証成績を持っているか？
    higher_guaranteed_teams = 0
    for other in all_teams:
        if other["team"] == ta:
            continue
        other_rem = TOTAL_GAMES - other["games"]
        # other が全敗したときの最低勝率
        other_min_rate = calc_win_rate(other["win"], other["lose"] + other_rem)
        if other_min_rate > a_max_rate:
            higher_guaranteed_teams += 1

    if higher_guaranteed_teams >= target_k:
        return "-"  # 広島の優勝のように、数学的に席が残っていない場合は即座に消滅

    # 2. 完全確定判定：
    # 自チームが残り全敗しても、target_k 位以内に入ることが保証されているか？
    # ＝自チームを上回る可能性のあるチーム数が target_k 未満であるか
    potential_threats = 0
    for other in all_teams:
        if other["team"] == ta:
            continue
        other_rem = TOTAL_GAMES - other["games"]
        other_max_rate = calc_win_rate(other["win"] + other_rem, other["lose"])
        if other_max_rate >= a_min_rate:
            potential_threats += 1

    if potential_threats < target_k:
        return "確定"

    # 3. 必要勝利数の算出
    # target_k 位を争う直接のライバルチーム（ボーダーチーム）を特定
    # 自チームが圏内(rank <= target_k)なら target_k+1 位のチーム
    # 自チームが圏外(rank > target_k)なら target_k 位のチーム
    border_team = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]
    tb = border_team["team"]
    rem_b = TOTAL_GAMES - border_team["games"]

    played = h2h_played.get(ta, {}).get(tb, 21)
    rem_h2h = max(0, GAMES_INTRA - played)
    rem_h2h = min(rem_h2h, rem_a, rem_b)

    # 自力確定可能かの探索 (0 〜 rem_a)
    for x in range(0, rem_a + 1):
        forced_b_losses = min(x, rem_h2h)
        b_max_win = border_team["win"] + (rem_b - forced_b_losses)
        b_max_lose = border_team["lose"] + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)

        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
        if a_rate > b_max_rate:
            return "確定" if x == 0 else x

    # 4. 自力消滅だが可能性が残っている場合（他力アシストが必要）
    # 相手が全勝ペースと仮定した際の数学的必要数（rem_a を超過する数値）
    b_abs_max_rate = calc_win_rate(border_team["win"] + rem_b, border_team["lose"])
    for x in range(rem_a + 1, rem_a + 25):
        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"])
        if a_rate > b_abs_max_rate:
            return x

    return rem_a + 1

def evaluate_league_clinches(teams, h2h_played):
    for t in teams:
        t["magic_1st"] = evaluate_target_clinch(t, 1, teams, h2h_played)
        t["magic_2nd"] = evaluate_target_clinch(t, 2, teams, h2h_played)
        t["magic_3rd"] = evaluate_target_clinch(t, 3, teams, h2h_played)
        t["magic_4th"] = evaluate_target_clinch(t, 4, teams, h2h_played)
        t["magic_5th"] = evaluate_target_clinch(t, 5, teams, h2h_played)
    return teams

def main():
    db = load_db()
    start_date = datetime.date(2026, 9, 14)
    today = datetime.date.today()

    game_dates = []
    curr = start_date
    while curr <= today:
        game_dates.append(curr.strftime("%Y-%m-%d"))
        curr += datetime.timedelta(days=1)

    history_snapshots = {}
    for d in game_dates:
        standings, h2h = build_standings_at_date(db, d)
        history_snapshots[d] = {
            "central": evaluate_league_clinches(standings["central"], h2h),
            "pacific": evaluate_league_clinches(standings["pacific"], h2h)
        }

    output = {
        "latest_date": game_dates[-1],
        "available_dates": game_dates,
        "history": history_snapshots
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": f"{game_dates[-1]} JST",
            "central": history_snapshots[game_dates[-1]]["central"],
            "pacific": history_snapshots[game_dates[-1]]["pacific"]
        }, f, ensure_ascii=False, indent=2)

    print("厳密クリンチ・エリミネーション計算完了")

if __name__ == "__main__":
    main()
