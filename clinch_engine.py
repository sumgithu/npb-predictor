import datetime
import itertools
import json
import os
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25  # 同一リーグ対戦総数
GAMES_INTER = 3   # 交流戦対戦総数
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

def get_exact_remaining_h2h(t1, t2, h2h_played, rem_1, rem_2):
    """
    NPB公式規定（同リーグ25回戦、交流戦3回戦）に基づく完全な残り直接対決数の算出
    """
    played = h2h_played.get(t1, {}).get(t2, 0)
    is_intra = (t1 in CENTRAL_TEAMS and t2 in CENTRAL_TEAMS) or (t1 in PACIFIC_TEAMS and t2 in PACIFIC_TEAMS)
    max_games = GAMES_INTRA if is_intra else GAMES_INTER
    # 規定総数から消化済み試合数を厳密に減算
    rem = max(0, max_games - played)
    return min(rem, rem_1, rem_2)

def can_team_reach_rank(team_a, target_k, all_teams, h2h_played, a_wins):
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_losses = rem_a - a_wins
    final_w_a = team_a["win"] + a_wins
    final_l_a = team_a["lose"] + a_losses
    a_rate = calc_win_rate(final_w_a, final_l_a)

    others = [t for t in all_teams if t["team"] != ta]

    # 上位チーム同士の残り直接対決の抽出
    internal_h2h = []
    for i in range(len(others)):
        for j in range(i + 1, len(others)):
            t1 = others[i]["team"]
            t2 = others[j]["team"]
            rem_pair = get_exact_remaining_h2h(t1, t2, h2h_played, others[i]["remaining"], others[j]["remaining"])
            if rem_pair > 0:
                internal_h2h.append((t1, t2, rem_pair))

    base_stats = {}
    for ot in others:
        name = ot["team"]
        rem = ot["remaining"]
        vs_a = get_exact_remaining_h2h(ta, name, h2h_played, rem_a, rem)
        forced_losses = min(a_wins, vs_a)
        base_stats[name] = {
            "win": ot["win"],
            "lose": ot["lose"] + forced_losses,
            "rem_other": max(0, rem - vs_a)
        }

    # 各チームが A の勝率を上回るために必要な追加勝利数
    needed_to_beat_a = {}
    strictly_better = 0
    for t in others:
        name = t["team"]
        rem_tot = t["remaining"]
        needed = None
        for add_w in range(0, rem_tot + 1):
            w = t["win"] + add_w
            l = t["lose"] + (rem_tot - add_w)
            if calc_win_rate(w, l) > a_rate:
                needed = add_w
                break
        needed_to_beat_a[name] = needed

        # 最低保証成績でも上回っているチーム
        min_rate = calc_win_rate(t["win"], t["lose"] + rem_tot)
        if min_rate > a_rate:
            strictly_better += 1

    if strictly_better >= target_k:
        return False

    # 上位陣同士の直接対決による不可避勝利数の流入検証
    total_internal_games = sum(g[2] for g in internal_h2h)
    total_allowed_wins = 0
    for t in others:
        name = t["team"]
        needed = needed_to_beat_a[name]
        if needed is None:
            total_allowed_wins += t["remaining"]
        else:
            max_safe = max(0, (t["win"] + needed - 1) - base_stats[name]["win"])
            total_allowed_wins += max_safe

    if total_internal_games > total_allowed_wins:
        return False

    return True

def evaluate_target_clinch_network(team_a, target_k, all_teams, h2h_played):
    ta = team_a["team"]
    rem_a = team_a["remaining"]

    # 1. 完全消滅（エリミネーション）：全勝しても target_k 位以内に入れない
    if not can_team_reach_rank(team_a, target_k, all_teams, h2h_played, rem_a):
        return "-"

    # 2. 完全確定：全敗しても target_k 位以内が保証される
    if can_team_reach_rank(team_a, target_k, all_teams, h2h_played, 0):
        threats = 0
        a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)
        for other in all_teams:
            if other["team"] == ta:
                continue
            other_max = calc_win_rate(other["win"] + other["remaining"], other["lose"])
            if other_max >= a_min_rate:
                threats += 1
        if threats < target_k:
            return "確定"

    # 3. 自力確定マジック探索 (0 〜 rem_a)
    for x in range(0, rem_a + 1):
        if can_team_reach_rank(team_a, target_k, all_teams, h2h_played, x):
            border = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]
            rem_h2h = get_exact_remaining_h2h(ta, border["team"], h2h_played, rem_a, border["remaining"])
            forced_b_losses = min(x, rem_h2h)
            b_max_rate = calc_win_rate(border["win"] + (border["remaining"] - forced_b_losses), border["lose"] + forced_b_losses)
            a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
            if a_rate > b_max_rate:
                return "確定" if x == 0 else x

    # 4. 自力消滅・可能性あり（他力アシストが必要なケース）
    border = all_teams[0] if target_k == 1 else (all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1])
    b_max_rate = calc_win_rate(border["win"] + border["remaining"], border["lose"])
    for x in range(rem_a + 1, rem_a + 25):
        if calc_win_rate(team_a["win"] + x, team_a["lose"]) > b_max_rate:
            return x

    return rem_a + 1

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
            t["magic_1st"] = evaluate_target_clinch_network(t, 1, c_table, h2h_played)
            t["magic_2nd"] = evaluate_target_clinch_network(t, 2, c_table, h2h_played)
            t["magic_3rd"] = evaluate_target_clinch_network(t, 3, c_table, h2h_played)
            t["magic_4th"] = evaluate_target_clinch_network(t, 4, c_table, h2h_played)
            t["magic_5th"] = evaluate_target_clinch_network(t, 5, c_table, h2h_played)

        for t in p_table:
            t["magic_1st"] = evaluate_target_clinch_network(t, 1, p_table, h2h_played)
            t["magic_2nd"] = evaluate_target_clinch_network(t, 2, p_table, h2h_played)
            t["magic_3rd"] = evaluate_target_clinch_network(t, 3, p_table, h2h_played)
            t["magic_4th"] = evaluate_target_clinch_network(t, 4, p_table, h2h_played)
            t["magic_5th"] = evaluate_target_clinch_network(t, 5, p_table, h2h_played)

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
    print(f"2026年 試合データ抽出: {len(games)} 試合")

    dates, history = build_all_history(games)

    output = {
        "latest_date": dates[-1] if dates else "2026-09-16",
        "available_dates": dates,
        "history": history
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"H2H完全逆算クリンチ計算完了: {dates[0]} 〜 {dates[-1]}")

if __name__ == "__main__":
    main()
