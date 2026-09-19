import datetime
import json
import math
import os
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25  # 同一リーグ内対戦総数
GAMES_INTER = 3   # 交流戦対戦総数
HISTORY_FILE = "history_standings.json"
MANUAL_DB_FILE = "games_db.json"
TEXT_LOG_FILE = "2016-2026プロ野球レギュラーシーズン結果.txt"

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

TEAM_ALIASES = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

def normalize_team(name):
    if not name:
        return ""
    clean = name.strip()
    return TEAM_ALIASES.get(clean, clean)

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def parse_year_games_from_text(raw_text, target_year):
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    sec_key = f"\n{target_year}\n"
    if sec_key in normalized:
        sec = normalized.split(sec_key)[-1]
    elif normalized.startswith(f"{target_year}\n"):
        sec = normalized.split(f"{target_year}\n")[-1]
    else:
        return []

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

        date_m = re.match(r'^(\d{1,2})\/(\d{1,2})(?:[（(][日月火水木金土][）)])?\s*(.*)$', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"{target_year}-{m:02d}-{d:02d}"
            line = date_m.group(3).strip()
            if not line:
                continue

        if not current_date or "中止" in line or "ノーゲーム" in line:
            continue

        # 1. 消化済みスコア行 (例: 巨人 6 - 4 ヤクルト 勝：内海 敗：デイビーズ)
        match_fin = re.search(r'([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)', line)
        if match_fin:
            h = normalize_team(match_fin.group(1))
            hs = int(match_fin.group(2))
            as_ = int(match_fin.group(3))
            a = normalize_team(match_fin.group(4))
            if h in all_teams and a in all_teams:
                win_p = re.search(r'勝：([^\s]+)', line)
                lose_p = re.search(r'敗：([^\s]+)', line)
                draw_p = re.findall(r'分：([^\s]+)', line)

                win_pitcher = win_p.group(1) if win_p else ""
                lose_pitcher = lose_p.group(1) if lose_p else ""

                if hs > as_:
                    h_pitcher, a_pitcher = win_pitcher, lose_pitcher
                elif hs < as_:
                    h_pitcher, a_pitcher = lose_pitcher, win_pitcher
                else:
                    h_pitcher = draw_p[0] if len(draw_p) > 0 else ""
                    a_pitcher = draw_p[1] if len(draw_p) > 1 else ""

                games.append({
                    "date": current_date,
                    "home": h, "away": a,
                    "home_score": hs, "away_score": as_,
                    "home_pitcher": h_pitcher, "away_pitcher": a_pitcher,
                    "home_starter": h_pitcher, "away_starter": a_pitcher,
                    "status": "finished"
                })
            continue

        # 2. 未消化予定試合 (ホーム - ビジター 表記)
        match_sched = re.search(r'([^\s\d]+)\s*-\s*([^\s\d]+)', line)
        if match_sched:
            h = normalize_team(match_sched.group(1))
            a = normalize_team(match_sched.group(2))
            if h in all_teams and a in all_teams:
                starters = re.findall(r'先発：([^\s]+)', line)
                h_starter = starters[0] if len(starters) > 0 else "未定"
                a_starter = starters[1] if len(starters) > 1 else "未定"

                games.append({
                    "date": current_date,
                    "home": h, "away": a,
                    "home_score": None, "away_score": None,
                    "home_pitcher": h_starter, "away_pitcher": a_starter,
                    "home_starter": h_starter, "away_starter": a_starter,
                    "status": "scheduled"
                })
            continue

        # 3. vs 表記
        match_vs = re.search(r'([^\s\d]+)\s+vs\s+([^\s\d]+)', line, re.IGNORECASE)
        if match_vs:
            a = normalize_team(match_vs.group(1))
            h = normalize_team(match_vs.group(2))
            if h in all_teams and a in all_teams:
                games.append({
                    "date": current_date,
                    "home": h, "away": a,
                    "home_score": None, "away_score": None,
                    "home_pitcher": "未定", "away_pitcher": "未定",
                    "home_starter": "未定", "away_starter": "未定",
                    "status": "scheduled"
                })

    return games

def load_all_games():
    games_2025 = []
    games_2026_base = []

    if os.path.exists(TEXT_LOG_FILE):
        with open(TEXT_LOG_FILE, "r", encoding="utf-8") as f:
            raw_text = f.read()
        games_2025 = parse_year_games_from_text(raw_text, 2025)
        games_2026_base = parse_year_games_from_text(raw_text, 2026)

    # 手入力 games_db.json があれば上書きマージ
    if os.path.exists(MANUAL_DB_FILE):
        try:
            with open(MANUAL_DB_FILE, "r", encoding="utf-8") as f:
                manual_db = json.load(f)
            manual_games = manual_db.get("games", [])
            manual_dates = {g["date"] for g in manual_games}

            merged_2026 = [g for g in games_2026_base if g["date"] not in manual_dates]
            merged_2026.extend(manual_games)
            merged_2026.sort(key=lambda x: x["date"])
            return games_2025, merged_2026
        except Exception as e:
            print(f"games_db.json 読込警告: {e}")

    return games_2025, games_2026_base

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

    guaranteed_higher = 0
    for ot in others:
        ot_min_rate = calc_win_rate(ot["win"], ot["lose"] + ot["remaining"])
        if ot_min_rate > a_max_rate:
            guaranteed_higher += 1

    if guaranteed_higher >= target_k:
        return "-"

    threats = 0
    for ot in others:
        ot_max_rate = calc_win_rate(ot["win"] + ot["remaining"], ot["lose"])
        if ot_max_rate >= a_min_rate:
            threats += 1

    if threats < target_k:
        return "確定"

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

EXP_PYTHAGOREAN = 1.83
PRIOR_WEIGHT_GAMES = 35.0
HOME_ODDS_ADVANTAGE = 1.15

def calc_pythagorean_rate(rs, ra):
    if rs <= 0 and ra <= 0:
        return 0.5
    rs_pow = math.pow(max(0.1, rs), EXP_PYTHAGOREAN)
    ra_pow = math.pow(max(0.1, ra), EXP_PYTHAGOREAN)
    return rs_pow / (rs_pow + ra_pow)

def get_bayesian_team_strength(prior_stats, current_stats):
    cur_games = current_stats["games"]
    prior_games = prior_stats["games"]
    avg_rs = prior_stats["rs"] / max(1, prior_games) if prior_games > 0 else 3.5
    avg_ra = prior_stats["ra"] / max(1, prior_games) if prior_games > 0 else 3.5

    p_rs = avg_rs * PRIOR_WEIGHT_GAMES
    p_ra = avg_ra * PRIOR_WEIGHT_GAMES

    blended_rs = p_rs + current_stats["rs"]
    blended_ra = p_ra + current_stats["ra"]

    return calc_pythagorean_rate(blended_rs, blended_ra)

def get_pitcher_multiplier(pitcher_name, pitcher_stats):
    if not pitcher_name or pitcher_name == "未定":
        return 1.0
    st = pitcher_stats.get(pitcher_name, {"win": 0, "lose": 0})
    w, l = st["win"], st["lose"]
    rate = (w + 3.0) / (w + l + 6.0)
    odds = rate / (1.0 - rate)
    return math.pow(odds, 0.35)

def calc_log5_matchup(p_away, p_home, away_pitcher, home_pitcher, pitcher_stats):
    denom = p_away + p_home - (2.0 * p_away * p_home)
    p_neutral_away = 0.5 if denom <= 0 else (p_away - (p_away * p_home)) / denom
    p_neutral_away = max(0.01, min(0.99, p_neutral_away))

    odds_away = p_neutral_away / (1.0 - p_neutral_away)
    adj_odds_away = odds_away / HOME_ODDS_ADVANTAGE

    m_away = get_pitcher_multiplier(away_pitcher, pitcher_stats)
    m_home = get_pitcher_multiplier(home_pitcher, pitcher_stats)
    pitcher_ratio = m_away / max(0.1, m_home)
    final_odds_away = adj_odds_away * pitcher_ratio

    final_p_away = final_odds_away / (1.0 + final_odds_away)
    final_p_home = 1.0 - final_p_away

    return round(final_p_away * 100.0, 1), round(final_p_home * 100.0, 1)

def build_all_history_with_predictions(games_2025, games_2026):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS

    prior_stats = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}
    pitcher_stats = {}

    for g in games_2025:
        if g.get("status") == "finished":
            h, a = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            prior_stats[h]["games"] += 1
            prior_stats[h]["rs"] += hs
            prior_stats[h]["ra"] += as_
            prior_stats[a]["games"] += 1
            prior_stats[a]["rs"] += as_
            prior_stats[a]["ra"] += hs

            hp = g.get("home_pitcher")
            ap = g.get("away_pitcher")
            if hp:
                if hp not in pitcher_stats: pitcher_stats[hp] = {"win": 0, "lose": 0}
                if hs > as_: pitcher_stats[hp]["win"] += 1
                elif hs < as_: pitcher_stats[hp]["lose"] += 1
            if ap:
                if ap not in pitcher_stats: pitcher_stats[ap] = {"win": 0, "lose": 0}
                if as_ > hs: pitcher_stats[ap]["win"] += 1
                elif as_ < hs: pitcher_stats[ap]["lose"] += 1

    all_dates = sorted(list({g["date"] for g in games_2026}))
    finished_dates = sorted(list({g["date"] for g in games_2026 if g.get("status") == "finished"}))
    last_finished_date = finished_dates[-1] if finished_dates else all_dates[0]

    history_snapshots = {}
    last_c_table = None
    last_p_table = None

    for target_date in all_dates:
        records = {t: {
            "team": t, "games": 0, "win": 0, "lose": 0, "draw": 0, "rs": 0, "ra": 0,
            "home": {"win": 0, "lose": 0, "draw": 0},
            "away": {"win": 0, "lose": 0, "draw": 0},
            "interleague": {"win": 0, "lose": 0, "draw": 0}
        } for t in all_teams}

        pre_records = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}
        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}
        h2h_details = {t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in all_teams} for t1 in all_teams}

        for g in games_2026:
            if g.get("status") != "finished":
                continue
            h, a = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            g_date = g["date"]

            if g_date < target_date:
                pre_records[h]["games"] += 1
                pre_records[h]["rs"] += hs
                pre_records[h]["ra"] += as_
                pre_records[a]["games"] += 1
                pre_records[a]["rs"] += as_
                pre_records[a]["ra"] += hs

                hp, ap = g.get("home_pitcher"), g.get("away_pitcher")
                if hp:
                    if hp not in pitcher_stats: pitcher_stats[hp] = {"win": 0, "lose": 0}
                    if hs > as_: pitcher_stats[hp]["win"] += 1
                    elif hs < as_: pitcher_stats[hp]["lose"] += 1
                if ap:
                    if ap not in pitcher_stats: pitcher_stats[ap] = {"win": 0, "lose": 0}
                    if as_ > hs: pitcher_stats[ap]["win"] += 1
                    elif as_ < hs: pitcher_stats[ap]["lose"] += 1

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

        if target_date <= last_finished_date:
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

            last_c_table = c_table
            last_p_table = p_table
        else:
            c_table = last_c_table
            p_table = last_p_table

        day_predictions = []
        for g in games_2026:
            if g["date"] == target_date:
                h, a = g["home"], g["away"]
                p_away = get_bayesian_team_strength(prior_stats[a], pre_records[a])
                p_home = get_bayesian_team_strength(prior_stats[h], pre_records[h])

                h_start = g.get("home_starter") or g.get("home_pitcher") or "未定"
                a_start = g.get("away_starter") or g.get("away_pitcher") or "未定"

                prob_away, prob_home = calc_log5_matchup(p_away, p_home, a_start, h_start, pitcher_stats)

                hs, as_ = g.get("home_score"), g.get("away_score")
                is_fin = (g.get("status") == "finished" and hs is not None and as_ is not None)

                if is_fin:
                    if hs > as_:
                        h_label = f"勝利: {h_start}" if h_start != "未定" else "勝利"
                        a_label = f"敗戦: {a_start}" if a_start != "未定" else "敗戦"
                    elif hs < as_:
                        h_label = f"敗戦: {h_start}" if h_start != "未定" else "敗戦"
                        a_label = f"勝利: {a_start}" if a_start != "未定" else "勝利"
                    else:
                        h_label = f"引分: {h_start}" if h_start != "未定" else "引分"
                        a_label = f"引分: {a_start}" if a_start != "未定" else "引分"
                else:
                    h_label = f"先発: {h_start}"
                    a_label = f"先発: {a_start}"

                day_predictions.append({
                    "away": a,
                    "home": h,
                    "away_starter": a_start,
                    "home_starter": h_start,
                    "away_status_text": a_label,
                    "home_status_text": h_label,
                    "away_prob": prob_away,
                    "home_prob": prob_home,
                    "actual_away_score": as_,
                    "actual_home_score": hs,
                    "is_finished": is_fin
                })

        history_snapshots[target_date] = {
            "central": c_table,
            "pacific": p_table,
            "predictions": day_predictions
        }

    default_latest = last_finished_date
    for d in all_dates:
        if d > last_finished_date:
            default_latest = d
            break

    return all_dates, default_latest, history_snapshots

def main():
    games_2025, games_2026 = load_all_games()
    print(f"2025年 試合データ: {len(games_2025)} 試合（事前分布）")
    print(f"2026年 試合データ: {len(games_2026)} 試合（全日程・予告先発含む）")

    dates, default_latest, history = build_all_history_with_predictions(games_2025, games_2026)

    output = {
        "latest_date": default_latest,
        "available_dates": dates,
        "history": history
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"全日程開放完了：{dates[0]} 〜 {dates[-1]} (デフォルト表示日: {default_latest})")

if __name__ == "__main__":
    main()
