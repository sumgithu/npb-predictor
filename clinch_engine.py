import datetime
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
    "DeNA": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク", "ロッテ": "ロッテ",
    "楽天": "楽天", "オリックス": "オリックス", "日本ハム": "日本ハム",
    "西武": "西武", "阪神": "阪神", "巨人": "巨人", "広島": "広島",
    "ヤクルト": "ヤクルト", "中日": "中日"
}

def normalize_team(name):
    clean = name.strip()
    return TEAM_ALIASES.get(clean, clean)

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def parse_games_from_text(raw_text):
    """テキストデータから2026年シーズンの全試合を正確に抽出"""
    games = []
    # 2026年のセクションを抽出
    sec_2026 = raw_text.split("2026\n")[-1]
    
    current_date = None
    lines = sec_2026.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 日付マッチ (例: 3/27（金）)
        date_m = re.match(r'^(\d{1,2})/(\d{1,2})', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"2026-{m:02d}-{d:02d}"
            # 同一行にある試合も処理するため line を日付以降にスライス
            line = re.sub(r'^\d{1,2}/\d{1,2}（[日月火水木金土]）\s*', '', line)

        if not current_date:
            continue

        # 中止・ノーゲーム行はスキップ
        if "中止" in line or "ノーゲーム" in line:
            continue

        # 試合結果マッチ (例: 巨人 4 - 2 阪神)
        match = re.search(r'([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)', line)
        if match:
            h_team = normalize_team(match.group(1))
            h_score = int(match.group(2))
            a_score = int(match.group(3))
            a_team = normalize_team(match.group(4))

            if h_team in (CENTRAL_TEAMS + PACIFIC_TEAMS) and a_team in (CENTRAL_TEAMS + PACIFIC_TEAMS):
                games.append({
                    "date": current_date,
                    "home": h_team,
                    "away": a_team,
                    "home_score": h_score,
                    "away_score": a_score
                })
    return games

def build_all_history(games):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    unique_dates = sorted(list({g["date"] for g in games}))
    
    history_snapshots = {}

    for target_date in unique_dates:
        # その日までの累積成績を算出
        records = {t: {"team": t, "games": 0, "win": 0, "lose": 0, "draw": 0} for t in all_teams}
        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}

        for g in games:
            if g["date"] <= target_date:
                h, a = g["home"], g["away"]
                hs, as_ = g["home_score"], g["away_score"]

                records[h]["games"] += 1
                records[a]["games"] += 1
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

        def format_league(league_teams):
            table = []
            for t in league_teams:
                r = records[t]
                r["remaining"] = TOTAL_GAMES - r["games"]
                r["rate"] = calc_win_rate(r["win"], r["lose"])
                table.append(r)
            # 勝率降順、勝利数降順
            table.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
            top_w, top_l = table[0]["win"], table[0]["lose"]
            for idx, t in enumerate(table):
                t["rank"] = idx + 1
                diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
                t["diff"] = max(0.0, diff) if idx > 0 else 0.0
            return table

        c_table = format_league(CENTRAL_TEAMS)
        p_table = format_league(PACIFIC_TEAMS)

        history_snapshots[target_date] = {
            "central": evaluate_league_clinches(c_table, h2h_played),
            "pacific": evaluate_league_clinches(p_table, h2h_played)
        }

    return unique_dates, history_snapshots

def evaluate_target_clinch(team_a, target_k, all_teams, h2h_played):
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)

    # 1. 完全消滅（エリミネーション）：上位保証チーム数が target_k 以上
    higher_guaranteed = 0
    for other in all_teams:
        if other["team"] == ta:
            continue
        other_min_rate = calc_win_rate(other["win"], other["lose"] + other["remaining"])
        if other_min_rate > a_max_rate:
            higher_guaranteed += 1

    if higher_guaranteed >= target_k:
        return "-"

    # 2. 完全確定：自チームが全敗しても逆転できる可能性のあるチーム数が target_k 未満
    threats = 0
    for other in all_teams:
        if other["team"] == ta:
            continue
        other_max_rate = calc_win_rate(other["win"] + other["remaining"], other["lose"])
        if other_max_rate >= a_min_rate:
            threats += 1

    if threats < target_k:
        return "確定"

    # 3. 必要勝利数の算出
    border = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]
    tb = border["team"]
    rem_b = border["remaining"]

    played = h2h_played.get(ta, {}).get(tb, 0)
    is_same = (ta in CENTRAL_TEAMS and tb in CENTRAL_TEAMS) or (ta in PACIFIC_TEAMS and tb in PACIFIC_TEAMS)
    max_h2h = GAMES_INTRA if is_same else GAMES_INTER
    rem_h2h = max(0, min(max_h2h - played, rem_a, rem_b))

    for x in range(0, rem_a + 1):
        forced_b_losses = min(x, rem_h2h)
        b_max_rate = calc_win_rate(border["win"] + (rem_b - forced_b_losses), border["lose"] + forced_b_losses)
        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
        if a_rate > b_max_rate:
            return "確定" if x == 0 else x

    # 4. 自力消滅（他力アシストが必要なケース）
    b_abs_max_rate = calc_win_rate(border["win"] + rem_b, border["lose"])
    for x in range(rem_a + 1, rem_a + 30):
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
    txt_path = "2016-2026プロ野球レギュラーシーズン結果.txt"
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    else:
        # リポジトリ直下にファイルが無い場合のフォールバック（直近games_db参照）
        with open("games_db.json", "r", encoding="utf-8") as f:
            db_data = json.load(f)
            games = db_data.get("games", [])
            dates, history = build_all_history(games)
            with open(HISTORY_FILE, "w", encoding="utf-8") as out:
                json.dump({"latest_date": dates[-1], "available_dates": dates, "history": history}, out, ensure_ascii=False, indent=2)
            return

    games = parse_games_from_text(raw_text)
    print(f"2026年 完了試合ログ抽出: {len(games)} 試合")

    dates, history = build_all_history(games)

    output = {
        "latest_date": dates[-1] if dates else "2026-09-16",
        "available_dates": dates,
        "history": history
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"全 {len(dates)} 日分の完全順位・CN履歴を出力しました ({dates[0]} 〜 {dates[-1]})")

if __name__ == "__main__":
    main()
