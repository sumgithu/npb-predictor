import datetime
import json
import math
import os
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25  # 同一リーグ内対戦総数
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

def parse_year_games(raw_text, target_year):
    """指定された年度の全試合ログを抽出"""
    sec_key = f"{target_year}\n"
    if sec_key not in raw_text:
        return []
    sec = raw_text.split(sec_key)[-1]
    # 次の年号ヘッダー（例: 2026）があればそこで切る
    next_years = [str(y) for y in range(target_year + 1, 2030)]
    for ny in next_years:
        if f"\n{ny}\n" in sec:
            sec = sec.split(f"\n{ny}\n")[0]

    games = []
    current_date = None
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS

    for line in sec.splitlines():
        line = line.strip()
        if not line:
            continue
        date_m = re.match(r'^(\d{1,2})/(\d{1,2})', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"{target_year}-{m:02d}-{d:02d}"
            line = re.sub(r'^\d{1,2}/\d{1,2}（[日月火水木金土]）\s*', '', line)

        if not current_date or "中止" in line or "ノーゲーム" in line:
            continue

        match = re.search(r'([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)', line)
        if match:
            h = normalize_team(match.group(1))
            hs = int(match.group(2))
            as_ = int(match.group(3))
            a = normalize_team(match.group(4))
            if h in all_teams and a in all_teams:
                # 予告先発/先発投手の抽出（勝・敗投手等の情報があれば取得）
                pitcher_m = re.findall(r'[勝敗分]：([^\s]+)', line)
                h_starter = pitcher_m[0] if len(pitcher_m) > 0 else "未定"
                a_starter = pitcher_m[1] if len(pitcher_m) > 1 else "未定"

                games.append({
                    "date": current_date,
                    "home": h, "away": a,
                    "home_score": hs, "away_score": as_,
                    "home_starter": h_starter, "away_starter": a_starter
                })
    return games

def get_remaining_h2h(t1, t2, h2h_played, rem_1, rem_2):
    played = h2h_played.get(t1, {}).get(t2, 0)
    is_intra = (t1 in CENTRAL_TEAMS and t2 in CENTRAL_TEAMS) or (t1 in PACIFIC_TEAMS and t2 in PACIFIC_TEAMS)
    max_games = GAMES_INTRA if is_intra else GAMES_INTER
    return max(0, min(max_games - played, rem_1, rem_2))

def evaluate_clinch_target(team_a, target_k, all_teams, h2h_played):
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)

    others = [ot for ot in all_teams if ot["team"] != ta]

    # 完全消滅判定
    guaranteed_higher = 0
    for ot in others:
        ot_min_rate = calc_win_rate(ot["win"], ot["lose"] + ot["remaining"])
        if ot_min_rate > a_max_rate:
            guaranteed_higher += 1

    if guaranteed_higher >= target_k:
        return "-"

    # 完全確定判定
    threats = 0
    for ot in others:
        ot_max_rate = calc_win_rate(ot["win"] + ot["remaining"], ot["lose"])
        if ot_max_rate >= a_min_rate:
            threats += 1

    if threats < target_k:
        return "確定"

    # クリンチナンバー探索
    if target_k == 1:
        border = all_teams[1] if team_a["rank"] == 1 else all_teams[0]
    else:
        border = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]

    tb = border["team"]
    rem_b = border["remaining"]
    rem_h2h = get_remaining_h2h(ta, tb, h2h_played, rem_a, rem_b)

    magic = None
    for x in range(0, rem_a + 1):
        a_losses = rem_a - x
        forced_b_losses = max(0, rem_h2h - a_losses)
        b_max_win = border["win"] + (rem_b - forced_b_losses)
        b_max_lose = border["lose"] + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)

        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + a_losses)
        if a_rate > b_max_rate:
            magic = x
            break

    if magic is not None:
        return "確定" if magic == 0 else magic

    b_abs_max_rate = calc_win_rate(border["win"] + rem_b, border["lose"])
    for x in range(rem_a + 1, rem_a + 40):
        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"])
        if a_rate > b_abs_max_rate:
            return x

    return rem_a + 1

def validate_and_assert_standings(teams):
    keys = ["magic_1st", "magic_2nd", "magic_3rd", "magic_4th", "magic_5th"]
    for t in teams:
        confirmed = False
        for k in keys:
            if t[k] == "確定":
                confirmed = True
            elif confirmed:
                t[k] = "確定"

        eliminated = False
        for k in reversed(keys):
            if t[k] == "-":
                eliminated = True
            elif eliminated:
                t[k] = "-"

        last_val = 0
        for k in reversed(keys):
            val = t[k]
            if isinstance(val, int):
                if val < last_val:
                    t[k] = last_val
                else:
                    last_val = val
    return teams

# -------------------------------------------------------------
# ベイジアン・ピタゴラス & Log5法による確率推計
# -------------------------------------------------------------
EXP_PYTHAGOREAN = 1.83
PRIOR_WEIGHT_GAMES = 35.0  # 事前分布（前年実績）の重み（試合数換算）
HOME_ODDS_ADVANTAGE = 1.15 # NPBホームアドバンテージ オッズ比

def calc_pythagorean_rate(rs, ra):
    if rs <= 0 and ra <= 0:
        return 0.5
    rs_pow = math.pow(max(0.1, rs), EXP_PYTHAGOREAN)
    ra_pow = math.pow(max(0.1, ra), EXP_PYTHAGOREAN)
    return rs_pow / (rs_pow + ra_pow)

def get_bayesian_team_strength(prior_stats, current_stats):
    """前年実績（事前分布）と当年試合前累積スタッツをベイズ収縮合成"""
    cur_games = current_stats["games"]
    prior_games = prior_stats["games"]
    
    # リーグ平均基準 (得点480, 失点480)
    avg_rs = prior_stats["rs"] / max(1, prior_games) if prior_games > 0 else 3.5
    avg_ra = prior_stats["ra"] / max(1, prior_games) if prior_games > 0 else 3.5

    # 事前分布の総得失点
    p_rs = avg_rs * PRIOR_WEIGHT_GAMES
    p_ra = avg_ra * PRIOR_WEIGHT_GAMES

    # 当年累積との合成
    blended_rs = p_rs + current_stats["rs"]
    blended_ra = p_ra + current_stats["ra"]

    return calc_pythagorean_rate(blended_rs, blended_ra)

def calc_log5_matchup(p_away, p_home):
    """Log5法による対戦勝率算出 + ホームアドバンテージ補正"""
    # ニュートラル球場におけるAway勝率
    denom = p_away + p_home - (2.0 * p_away * p_home)
    if denom <= 0:
        p_neutral_away = 0.5
    else:
        p_neutral_away = (p_away - (p_away * p_home)) / denom

    # オッズ変換
    p_neutral_away = max(0.01, min(0.99, p_neutral_away))
    odds_away = p_neutral_away / (1.0 - p_neutral_away)

    # ホームチームにアドバンテージ適用（Awayのオッズを除算）
    adj_odds_away = odds_away / HOME_ODDS_ADVANTAGE
    final_p_away = adj_odds_away / (1.0 + adj_odds_away)
    final_p_home = 1.0 - final_p_away

    return round(final_p_away * 100.0, 1), round(final_p_home * 100.0, 1)

def build_all_history_with_predictions(games_2025, games_2026):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS

    # 2025年通算スタッツ（事前分布用）
    prior_stats = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}
    for g in games_2025:
        h, a = g["home"], g["away"]
        prior_stats[h]["games"] += 1
        prior_stats[h]["rs"] += g["home_score"]
        prior_stats[h]["ra"] += g["away_score"]
        prior_stats[a]["games"] += 1
        prior_stats[a]["rs"] += g["away_score"]
        prior_stats[a]["ra"] += g["home_score"]

    unique_dates = sorted(list({g["date"] for g in games_2026}))
    history_snapshots = {}

    for target_date in unique_dates:
        # 当該日終了時点の累積成績
        records = {t: {
            "team": t, "games": 0, "win": 0, "lose": 0, "draw": 0, "rs": 0, "ra": 0,
            "home": {"win": 0, "lose": 0, "draw": 0},
            "away": {"win": 0, "lose": 0, "draw": 0},
            "interleague": {"win": 0, "lose": 0, "draw": 0}
        } for t in all_teams}

        # 当該日開始前（事前）の累積スタッツ（確率予測用）
        pre_records = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}

        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}
        h2h_details = {t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in all_teams} for t1 in all_teams}

        # 試合別集計
        for g in games_2026:
            h, a = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            g_date = g["date"]

            # 事前スタッツの積み上げ（target_date 当日の試合開始前）
            if g_date < target_date:
                pre_records[h]["games"] += 1
                pre_records[h]["rs"] += hs
                pre_records[h]["ra"] += as_
                pre_records[a]["games"] += 1
                pre_records[a]["rs"] += as_
                pre_records[a]["ra"] += hs

            # 当日終了時点のスタッツ
            if g_date <= target_date:
                records[h]["games"] += 1
                records[a]["games"] += 1
                records[h]["rs"] += hs
                records[h]["ra"] += as_
                records[a]["rs"] += as_
                records[a]["ra"] += hs
                h2h_played[h][a] += 1
                h2h_played[a][h] += 1

                is_inter = (h in CENTRAL_TEAMS and a in PACIFIC_TEAMS) or (h in PACIFIC_TEAMS and a in CENTRAL_TEAMS)

                if hs > as_:
                    records[h]["win"] += 1
                    records[h]["home"]["win"] += 1
                    records[a]["lose"] += 1
                    records[a]["away"]["lose"] += 1
                    h2h_details[h][a]["win"] += 1
                    h2h_details[a][h]["lose"] += 1
                    if is_inter:
                        records[h]["interleague"]["win"] += 1
                        records[a]["interleague"]["lose"] += 1
                elif hs < as_:
                    records[a]["win"] += 1
                    records[a]["away"]["win"] += 1
                    records[h]["lose"] += 1
                    records[h]["home"]["lose"] += 1
                    h2h_details[a][h]["win"] += 1
                    h2h_details[h][a]["lose"] += 1
                    if is_inter:
                        records[a]["interleague"]["win"] += 1
                        records[h]["interleague"]["lose"] += 1
                else:
                    records[h]["draw"] += 1
                    records[h]["home"]["draw"] += 1
                    records[a]["draw"] += 1
                    records[a]["away"]["draw"] += 1
                    h2h_details[h][a]["draw"] += 1
                    h2h_details[a][h]["draw"] += 1
                    if is_inter:
                        records[h]["interleague"]["draw"] += 1
                        records[a]["interleague"]["draw"] += 1

        # 当該日に行われた各試合の事前予測勝利確率を算出
        day_predictions = []
        for g in games_2026:
            if g["date"] == target_date:
                h, a = g["home"], g["away"]
                p_away = get_bayesian_team_strength(prior_stats[a], pre_records[a])
                p_home = get_bayesian_team_strength(prior_stats[h], pre_records[h])
                prob_away, prob_home = calc_log5_matchup(p_away, p_home)

                day_predictions.append({
                    "away": a,
                    "home": h,
                    "away_starter": g.get("away_starter", "未定"),
                    "home_starter": g.get("home_starter", "未定"),
                    "away_prob": prob_away,
                    "home_prob": prob_home,
                    "actual_away_score": g["away_score"],
                    "actual_home_score": g["home_score"]
                })

        def format_league(league_teams):
            table = []
            for t in league_teams:
                r = records[t]
                r["remaining"] = TOTAL_GAMES - r["games"]
                r["rate"] = calc_win_rate(r["win"], r["lose"])
                r["h2h"] = {opp: h2h_details[t][opp] for opp in league_teams}
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

        history_snapshots[target_date] = {
            "central": c_table,
            "pacific": p_table,
            "predictions": day_predictions
        }

    return unique_dates, history_snapshots

def main():
    txt_path = "2016-2026プロ野球レギュラーシーズン結果.txt"
    if not os.path.exists(txt_path):
        print("テキストファイルが見つかりません。")
        return

    with open(txt_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    games_2025 = parse_year_games(raw_text, 2025)
    games_2026 = parse_year_games(raw_text, 2026)

    print(f"2025年 試合データ: {len(games_2025)} 試合（事前分布としてロード）")
    print(f"2026年 試合データ: {len(games_2026)} 試合")

    dates, history = build_all_history_with_predictions(games_2025, games_2026)

    output = {
        "latest_date": dates[-1] if dates else "2026-09-16",
        "available_dates": dates,
        "history": history
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"全 {len(dates)} 日分の順位・クリンチ・Log5勝率予測データの生成完了")

if __name__ == "__main__":
    main()
