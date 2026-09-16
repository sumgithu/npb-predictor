import datetime
import json
import os
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25
GAMES_INTER = 3
HISTORY_FILE = "history_standings.json"

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

TEAM_ALIASES = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

def normalize_team(name):
    clean = name.strip()
    return TEAM_ALIASES.get(clean, clean)

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def parse_games_from_text(raw_text):
    games = []
    sec_2026 = raw_text.split("2026\n")[-1]
    current_date = None

    for line in sec_2026.splitlines():
        line = line.strip()
        if not line:
            continue
        date_m = re.match(r'^(\d{1,2})/(\d{1,2})', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"2026-{m:02d}-{d:02d}"
            line = re.sub(r'^\d{1,2}/\d{1,2}（[日月火水木金土]）\s*', '', line)

        if not current_date or "中止" in line or "ノーゲーム" in line:
            continue

        match = re.search(r'([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)', line)
        if match:
            h = normalize_team(match.group(1))
            hs = int(match.group(2))
            as_ = int(match.group(3))
            a = normalize_team(match.group(4))
            all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
            if h in all_teams and a in all_teams:
                games.append({
                    "date": current_date,
                    "home": h, "away": a,
                    "home_score": hs, "away_score": as_
                })
    return games

def get_remaining_h2h(t1, t2, h2h_played, rem_1, rem_2):
    played = h2h_played.get(t1, {}).get(t2, 0)
    is_intra = (t1 in CENTRAL_TEAMS and t2 in CENTRAL_TEAMS) or (t1 in PACIFIC_TEAMS and t2 in PACIFIC_TEAMS)
    max_games = GAMES_INTRA if is_intra else GAMES_INTER
    return max(0, min(max_games - played, rem_1, rem_2))

def evaluate_clinch_target(team_a, target_k, all_teams, h2h_played):
    """
    target_k: 1(CN/優勝), 2(2nd/本拠), 3(3rd/CS), 4(4th), 5(5th/最下位回避)
    """
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)

    others = [ot for ot in all_teams if ot["team"] != ta]

    # --- 1. 完全消滅判定 ---
    guaranteed_higher = 0
    for ot in others:
        ot_min_rate = calc_win_rate(ot["win"], ot["lose"] + ot["remaining"])
        if ot_min_rate > a_max_rate:
            guaranteed_higher += 1

    if guaranteed_higher >= target_k:
        return "-"

    # 上位候補内部対決による不可避勝利判定
    contenders = [ot for ot in others if calc_win_rate(ot["win"] + ot["remaining"], ot["lose"]) > a_max_rate]
    internal_games = 0
    for i in range(len(contenders)):
        for j in range(i + 1, len(contenders)):
            internal_games += get_remaining_h2h(contenders[i]["team"], contenders[j]["team"], h2h_played, contenders[i]["remaining"], contenders[j]["remaining"])

    total_safe_capacity = 0
    for ot in contenders:
        rem = ot["remaining"]
        limit_w = 0
        for w in range(rem, -1, -1):
            if calc_win_rate(ot["win"] + w, ot["lose"] + (rem - w)) <= a_max_rate:
                limit_w = w
                break
        total_safe_capacity += limit_w

    if len(contenders) >= target_k and internal_games > total_safe_capacity:
        return "-"

    # --- 2. 完全確定判定 ---
    threats = 0
    for ot in others:
        ot_max_rate = calc_win_rate(ot["win"] + ot["remaining"], ot["lose"])
        if ot_max_rate >= a_min_rate:
            threats += 1

    if threats < target_k:
        return "確定"

    # --- 3. クリンチナンバー（必要自力勝利数）の厳密算出 ---
    border = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]
    tb = border["team"]
    rem_b = border["remaining"]
    rem_h2h = get_remaining_h2h(ta, tb, h2h_played, rem_a, rem_b)

    # 探索: Aが残り rem_a 試合中 x 勝 (rem_a - x 敗) したときの条件
    for x in range(0, rem_a + 1):
        a_losses = rem_a - x
        # 自チームの全敗数(a_losses)が直接対決に集中した際、相手Bに最低限つく敗戦数
        forced_b_losses = max(0, rem_h2h - a_losses)
        b_max_win = border["win"] + (rem_b - forced_b_losses)
        b_max_lose = border["lose"] + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)

        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + a_losses)
        if a_rate > b_max_rate:
            return "確定" if x == 0 else x

    # 自力消滅だが可能性あり（他力アシストが必要なケース）
    b_abs_max_rate = calc_win_rate(border["win"] + rem_b, border["lose"])
    for x in range(rem_a + 1, rem_a + 25):
        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"])
        if a_rate > b_abs_max_rate:
            return x

    return "-"

def validate_and_assert_standings(teams):
    keys = ["magic_1st", "magic_2nd", "magic_3rd", "magic_4th", "magic_5th"]

    for t in teams:
        # 上位確定なら下位も確定
        confirmed = False
        for k in keys:
            if t[k] == "確定":
                confirmed = True
            elif confirmed:
                t[k] = "確定"

        # 下位消滅なら上位も消滅
        eliminated = False
        for k in reversed(keys):
            if t[k] == "-":
                eliminated = True
            elif eliminated:
                t[k] = "-"

        # 単調性の検証（CN >= 2nd >= 3rd >= 4th >= 5th）
        last_val = 0
        for k in reversed(keys):
            val = t[k]
            if isinstance(val, int):
                if val < last_val:
                    t[k] = last_val
                else:
                    last_val = val

    return teams

def build_all_history(games):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    unique_dates = sorted(list({g["date"] for g in games}))
    history_snapshots = {}

    for target_date in unique_dates:
        records = {t: {"team": t, "games": 0, "win": 0, "lose": 0, "draw": 0} for t in all_teams}
        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}

        for g in games:
            if g["date"] <= target_date:
                h, a = g["home"], g["away"]
                records[h]["games"] += 1
                records[a]["games"] += 1
                h2h_played[h][a] += 1
                h2h_played[a][h] += 1

                if g["home_score"] > g["away_score"]:
                    records[h]["win"] += 1
                    records[a]["lose"] += 1
                elif g["home_score"] < g["away_score"]:
                    records[a]["win"] += 1
                    records[h]["lose"] += 1
                else:
                    records[h]["draw"] += 1
                    records[a]["draw"] += 1

        def format_league(league_teams):
            table = []
            for t in league_teams:
                r = records[t]
                r["remaining"] = TOTAL_GAMES - r["games"]
                r["rate"] = calc_win_rate(r["win"], r["lose"])
                table.append(r)
            table.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
            top_w, top_l = table[0]["win"], table[0]["lose"]
            for idx, t in enumerate(table):
                t["rank"] = idx + 1
                diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
                t["diff"] = max(0.0, diff) if idx > 0 else 0.0
            return table

        c_table = format_league(CENTRAL_TEAMS)
        p_table = format_league(PACIFIC_TEAMS)

        for t in c_table:
            t["magic_1st"] = evaluate_clinch_target(t, 1, c_table, h2h_played)
            t["magic_2nd"] = evaluate_clinch_target(t, 2, c_table, h2h_played)
            t["magic_3rd"] = evaluate_clinch_target(t, 3, c_table, h2h_played)
            t["magic_4th"] = evaluate_clinch_target(t, 4, c_table, h2h_played)
            t["magic_5th"] = evaluate_clinch_target(t, 5, c_table, h2h_played)

        for t in p_table:
            t["magic_1st"] = evaluate_clinch_target(t, 1, p_table, h2h_played)
            t["magic_2nd"] = evaluate_clinch_target(t, 2, p_table, h2h_played)
            t["magic_3rd"] = evaluate_clinch_target(t, 3, p_table, h2h_played)
            t["magic_4th"] = evaluate_clinch_target(t, 4, p_table, h2h_played)
            t["magic_5th"] = evaluate_clinch_target(t, 5, p_table, h2h_played)

        c_table = validate_and_assert_standings(c_table)
        p_table = validate_and_assert_standings(p_table)

        history_snapshots[target_date] = {"central": c_table, "pacific": p_table}

    return unique_dates, history_snapshots

def main():
    txt_path = "2016-2026プロ野球レギュラーシーズン結果.txt"
    if not os.path.exists(txt_path):
        print("テキストファイルが見つかりません。")
        return

    with open(txt_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    games = parse_games_from_text(raw_text)
    dates, history = build_all_history(games)

    output = {
        "latest_date": dates[-1] if dates else "2026-09-16",
        "available_dates": dates,
        "history": history
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("直接対決最悪ケース考慮：history_standings.json 更新完了")

if __name__ == "__main__":
    main()
