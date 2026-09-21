import datetime
import json
import math
import os
import random
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25
GAMES_INTER = 3
HISTORY_FILE = "history_standings.json"
MANUAL_DB_FILE = "games_db.json"
TEXT_LOG_FILE = "2016-2026プロ野球レギュラーシーズン結果.txt"

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

TEAM_ALIASES = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "横浜DeNA": "ＤｅＮＡ", "横浜ＤｅＮＡ": "ＤｅＮＡ",
    "ソフトバンク": "ソフトバンク", "福岡ソフトバンク": "ソフトバンク", "福岡": "ソフトバンク",
    "ロッテ": "ロッテ", "千葉ロッテ": "ロッテ",
    "楽天": "楽天", "東北楽天": "楽天",
    "オリックス": "オリックス", "オリックス・バファローズ": "オリックス",
    "日本ハム": "日本ハム", "北海道日本ハム": "日本ハム", "日ハム": "日本ハム",
    "西武": "西武", "埼玉西武": "西武",
    "阪神": "阪神", "阪神タイガース": "阪神",
    "巨人": "巨人", "読売": "巨人", "読売ジャイアンツ": "巨人",
    "広島": "広島", "広島東洋": "広島",
    "ヤクルト": "ヤクルト", "東京ヤクルト": "ヤクルト",
    "中日": "中日", "中日ドラゴンズ": "中日"
}

STADIUM_NAMES = {
    "阪神": "甲子園", "巨人": "東京D", "ＤｅＮＡ": "横浜",
    "ヤクルト": "神宮", "中日": "バンテリン", "広島": "マツダS",
    "ソフトバンク": "PayPay", "日本ハム": "エスコンF", "ロッテ": "ZOZO",
    "楽天": "楽天モバイル", "オリックス": "京セラ", "西武": "ベルーナ"
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

        # 1. カンマ区切り形式（CSV）
        csv_parts = [p.strip() for p in line.split(',')]
        if len(csv_parts) >= 6 and re.match(r'^\d{4}-\d{2}-\d{2}$', csv_parts[0]):
            c_date, h_raw, a_raw = csv_parts[0], csv_parts[1], csv_parts[2]
            h = normalize_team(h_raw)
            a = normalize_team(a_raw)
            if h in all_teams and a in all_teams:
                if csv_parts[3] == "" or (len(csv_parts) >= 8 and csv_parts[7] == "1"):
                    games.append({
                        "date": c_date, "home": h, "away": a,
                        "home_score": None, "away_score": None,
                        "home_pitcher": "未定", "away_pitcher": "未定",
                        "home_starter": "未定", "away_starter": "未定",
                        "status": "cancelled"
                    })
                else:
                    hs = int(csv_parts[3])
                    as_ = int(csv_parts[4])
                    p_info = csv_parts[5] if len(csv_parts) >= 6 else ""
                    games.append({
                        "date": c_date, "home": h, "away": a,
                        "home_score": hs, "away_score": as_,
                        "home_pitcher": p_info, "away_pitcher": p_info,
                        "home_starter": p_info, "away_starter": p_info,
                        "status": "finished"
                    })
                continue

        # 2. テキスト形式（日付行）
        date_m = re.match(r'^(\d{1,2})\/(\d{1,2})(?:[（(][日月火水木金土][）)])?\s*(.*)$', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"{target_year}-{m:02d}-{d:02d}"
            line = date_m.group(3).strip()
            if not line:
                continue

        if not current_date:
            continue

        # 中止行の検出
        if "中止" in line or "ノーゲーム" in line:
            match_can = re.search(r'([^\s\d]+)\s*(?:中止|ノーゲーム)\s*([^\s\d]+)', line)
            if match_can:
                h = normalize_team(match_can.group(1))
                a = normalize_team(match_can.group(2))
                if h in all_teams and a in all_teams:
                    games.append({
                        "date": current_date, "home": h, "away": a,
                        "home_score": None, "away_score": None,
                        "home_pitcher": "未定", "away_pitcher": "未定",
                        "home_starter": "未定", "away_starter": "未定",
                        "status": "cancelled"
                    })
            continue

        # 試合終了行の検出
        match_fin = re.search(r'([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)', line)
        if match_fin:
            h = normalize_team(match_fin.group(1))
            hs = int(match_fin.group(2))
            as_ = int(match_fin.group(3))
            a = normalize_team(match_fin.group(4))
            if h in all_teams and a in all_teams:
                win_p = re.search(r'勝(?:利)?[:：]\s*([^\s,，]+)', line)
                lose_p = re.search(r'敗(?:戦)?[:：]\s*([^\s,，]+)', line)
                draw_p = re.findall(r'分[:：]\s*([^\s,，]+)', line)

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
                    "date": current_date, "home": h, "away": a,
                    "home_score": hs, "away_score": as_,
                    "home_pitcher": h_pitcher, "away_pitcher": a_pitcher,
                    "home_starter": h_pitcher, "away_starter": a_pitcher,
                    "status": "finished"
                })
            continue

        # 予告先発・予定行の検出
        match_sched = re.search(r'([^\s\d]+)\s*-\s*([^\s\d]+)', line)
        if match_sched:
            h = normalize_team(match_sched.group(1))
            a = normalize_team(match_sched.group(2))
            if h in all_teams and a in all_teams:
                starters = re.findall(r'先発[:：]\s*([^\s,，]+)', line)
                h_starter = starters[0] if len(starters) > 0 else "未定"
                a_starter = starters[1] if len(starters) > 1 else "未定"

                games.append({
                    "date": current_date, "home": h, "away": a,
                    "home_score": None, "away_score": None,
                    "home_pitcher": h_starter, "away_pitcher": a_starter,
                    "home_starter": h_starter, "away_starter": a_starter,
                    "status": "scheduled"
                })
            continue

    return games

def load_all_games():
    games_2025 = []
    games_2026_master = []

    active_master = TEXT_LOG_FILE if os.path.exists(TEXT_LOG_FILE) else "npb_games_clean.csv"
    if os.path.exists(active_master):
        with open(active_master, "r", encoding="utf-8") as f:
            raw_text = f.read()
        games_2025 = parse_year_games_from_text(raw_text, 2025)
        games_2026_master = parse_year_games_from_text(raw_text, 2026)

    if os.path.exists(MANUAL_DB_FILE):
        try:
            with open(MANUAL_DB_FILE, "r", encoding="utf-8") as f:
                manual_payload = json.load(f)

            manual_games = manual_payload if isinstance(manual_payload, list) else manual_payload.get("games", [])

            manual_map = {}
            for mg in manual_games:
                if not mg or "date" not in mg or "home" not in mg or "away" not in mg:
                    continue
                norm_h = normalize_team(mg["home"])
                norm_a = normalize_team(mg["away"])
                mg["home"] = norm_h
                mg["away"] = norm_a

                hs_raw = str(mg.get("home_score", "")).strip()
                as_raw = str(mg.get("away_score", "")).strip()
                if hs_raw != "" and as_raw != "" and hs_raw != "null" and as_raw != "null":
                    mg["home_score"] = int(hs_raw)
                    mg["away_score"] = int(as_raw)
                    mg["status"] = "finished"
                elif mg.get("status") == "cancelled":
                    mg["home_score"] = None
                    mg["away_score"] = None
                else:
                    mg["home_score"] = None
                    mg["away_score"] = None
                    mg["status"] = "scheduled"

                manual_map[(mg["date"], norm_h, norm_a)] = mg

            merged_2026 = []
            applied_keys = set()

            for mg_orig in games_2026_master:
                k = (mg_orig["date"], mg_orig["home"], mg_orig["away"])
                if k in manual_map:
                    merged_2026.append(manual_map[k])
                    applied_keys.add(k)
                else:
                    merged_2026.append(mg_orig)

            for k, mg in manual_map.items():
                if k not in applied_keys:
                    merged_2026.append(mg)

            merged_2026.sort(key=lambda x: (x["date"], x.get("status") == "finished"))
            return games_2025, merged_2026
        except Exception as e:
            print(f"games_db.json 読込警告: {e}")

    return games_2025, games_2026_master

def get_remaining_h2h(t1, t2, h2h_played, rem_1, rem_2):
    played = h2h_played.get(t1, {}).get(t2, 0)
    is_intra = (t1 in CENTRAL_TEAMS and t2 in CENTRAL_TEAMS) or (t1 in PACIFIC_TEAMS and t2 in PACIFIC_TEAMS)
    max_games = GAMES_INTRA if is_intra else GAMES_INTER
    return max(0, min(max_games - played, rem_1, rem_2))

def evaluate_clinch_target(team_a, target_k, all_teams, h2h_played):
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_w, a_l = team_a["win"], team_a["lose"]
    
    a_max_rate = calc_win_rate(a_w + rem_a, a_l)
    a_min_rate = calc_win_rate(a_w, a_l + rem_a)

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
    b_w, b_l = border["win"], border["lose"]
    rem_h2h = get_remaining_h2h(ta, tb, h2h_played, rem_a, rem_b)

    magic = None
    for x in range(0, rem_a + 1):
        a_losses = rem_a - x
        forced_b_losses = max(0, rem_h2h - a_losses)
        b_max_win = b_w + (rem_b - forced_b_losses)
        b_max_lose = b_l + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)

        a_rate = calc_win_rate(a_w + x, a_l + a_losses)
        if a_rate > b_max_rate:
            magic = x
            break

    if magic is not None:
        return "確定" if magic == 0 else magic

    b_abs_max_rate = calc_win_rate(b_w + rem_b, b_l)
    for x in range(rem_a + 1, rem_a + 40):
        a_rate = calc_win_rate(a_w + x, a_l)
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
HOME_ODDS_ADVANTAGE = 1.15
RECENT_WINDOW_GAMES = 65

def calc_pythagorean_rate(rs, ra):
    if rs <= 0 and ra <= 0:
        return 0.5
    rs_pow = math.pow(max(0.1, rs), EXP_PYTHAGOREAN)
    ra_pow = math.pow(max(0.1, ra), EXP_PYTHAGOREAN)
    return rs_pow / (rs_pow + ra_pow)

def get_rolling_recent_strength(team_match_history, prior_stats):
    total_played = len(team_match_history)
    if total_played == 0:
        avg_rs = prior_stats["rs"] / max(1, prior_stats["games"]) if prior_stats["games"] > 0 else 3.5
        avg_ra = prior_stats["ra"] / max(1, prior_stats["games"]) if prior_stats["games"] > 0 else 3.5
        return calc_pythagorean_rate(avg_rs, avg_ra)

    recent_matches = team_match_history[-RECENT_WINDOW_GAMES:]
    weighted_rs = 0.0
    weighted_ra = 0.0
    weight_sum = 0.0

    n = len(recent_matches)
    for idx, match in enumerate(recent_matches):
        w = 1.0 + (idx / max(1, n - 1))
        weighted_rs += match["rs"] * w
        weighted_ra += match["ra"] * w
        weight_sum += w

    eff_rs = weighted_rs / weight_sum
    eff_ra = weighted_ra / weight_sum

    if total_played < RECENT_WINDOW_GAMES:
        prior_weight = max(0.0, (RECENT_WINDOW_GAMES - total_played) / RECENT_WINDOW_GAMES) * 15.0
        p_rs = (prior_stats["rs"] / max(1, prior_stats["games"])) * prior_weight
        p_ra = (prior_stats["ra"] / max(1, prior_stats["games"])) * prior_weight
        final_rs = (eff_rs * total_played + p_rs) / (total_played + prior_weight)
        final_ra = (eff_ra * total_played + p_ra) / (total_played + prior_weight)
    else:
        final_rs = eff_rs
        final_ra = eff_ra

    return calc_pythagorean_rate(final_rs, final_ra)

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

def simulate_full_season_probabilities(league_teams, current_standings, remaining_matches, team_match_histories, prior_stats):
    NUM_SIMS = 5000
    rank_counts = {t: {r: 0 for r in range(1, 7)} for t in league_teams}
    clinch_date_counts = {t: {} for t in league_teams}

    base_probs = {}
    for t1 in league_teams:
        for t2 in league_teams:
            if t1 != t2:
                p1 = get_rolling_recent_strength(team_match_histories[t1], prior_stats[t1])
                p2 = get_rolling_recent_strength(team_match_histories[t2], prior_stats[t2])
                pa, ph = calc_log5_matchup(p2, p1, "未定", "未定", {})
                base_probs[(t1, t2)] = ph / 100.0

    base_wins = {t["team"]: t["win"] for t in current_standings}
    base_loses = {t["team"]: t["lose"] for t in current_standings}
    sorted_matches = sorted([m for m in remaining_matches if m.get("status") == "scheduled"], key=lambda x: x["date"])

    matches_by_date = {}
    for m in sorted_matches:
        d = m["date"]
        if d not in matches_by_date:
            matches_by_date[d] = []
        matches_by_date[d].append(m)

    sorted_dates = sorted(matches_by_date.keys())

    for _ in range(NUM_SIMS):
        sim_w = dict(base_wins)
        sim_l = dict(base_loses)
        clinched_day = {t: None for t in league_teams}

        for d in sorted_dates:
            for match in matches_by_date[d]:
                h, a = match["home"], match["away"]
                p_home = base_probs.get((h, a), 0.535)
                if random.random() < p_home:
                    sim_w[h] += 1
                    sim_l[a] += 1
                else:
                    sim_w[a] += 1
                    sim_l[h] += 1

            sim_rates = sorted([(t, sim_w[t] / (sim_w[t] + sim_l[t]), sim_w[t]) for t in league_teams],
                               key=lambda x: (x[1], x[2]), reverse=True)
            leader = sim_rates[0][0]
            second = sim_rates[1][0]
            rem_2nd = TOTAL_GAMES - (sim_w[second] + sim_l[second])

            if sim_w[leader] > sim_w[second] + rem_2nd and clinched_day[leader] is None:
                clinched_day[leader] = d

        sim_rates = sorted([(t, sim_w[t] / (sim_w[t] + sim_l[t]), sim_w[t]) for t in league_teams],
                           key=lambda x: (x[1], x[2]), reverse=True)

        champ = sim_rates[0][0]
        c_date = clinched_day[champ]
        if c_date is not None:
            clinch_date_counts[champ][c_date] = clinch_date_counts[champ].get(c_date, 0) + 1

        for idx, item in enumerate(sim_rates):
            rank_counts[item[0]][idx + 1] += 1

    final_rank_matrix = {t: {r: round((rank_counts[t][r] / NUM_SIMS) * 100) for r in range(1, 7)} for t in league_teams}

    for t in current_standings:
        tname = t["team"]
        if t.get("magic_1st") == "確定":
            final_rank_matrix[tname][1] = 100
            for r in range(2, 7): final_rank_matrix[tname][r] = 0
        elif t.get("magic_3rd") == "確定":
            top3_sum = final_rank_matrix[tname][1] + final_rank_matrix[tname][2] + final_rank_matrix[tname][3]
            if top3_sum < 100:
                final_rank_matrix[tname][t["rank"]] += (100 - top3_sum)
            for r in range(4, 7):
                final_rank_matrix[tname][r] = 0

    clinch_date_probs = {}
    for t in league_teams:
        c_map = {}
        for d, count in clinch_date_counts[t].items():
            c_map[d] = (count / NUM_SIMS) * 100.0
        clinch_date_probs[t] = c_map

    return final_rank_matrix, clinch_date_probs

def build_aligned_championship_grid(top_teams_standings):
    teams_data = []
    for t in top_teams_standings[:3]:
        rem = t["remaining"]
        cur_w = t["win"]
        cur_l = t["lose"]
        pats = []
        for w in range(rem, -1, -1):
            l = rem - w
            rate = calc_win_rate(cur_w + w, cur_l + l)
            pats.append({
                "w": w, "l": l, "rate": round(rate, 3),
                "rate_str": f".{round(rate * 1000):03d}"
            })
        teams_data.append({
            "team": t["team"], "remaining": rem,
            "current_w": cur_w, "current_l": cur_l,
            "patterns": pats
        })

    aligned_rows = []
    base_team = teams_data[0]
    team3_max_rate = teams_data[2]["patterns"][0]["rate"] if len(teams_data) > 2 else 1.0

    for i in range(len(base_team["patterns"])):
        base_p = base_team["patterns"][i]
        target_rate = base_p["rate"]
        row = [base_p]

        if len(teams_data) > 1:
            best_match_t2 = min(teams_data[1]["patterns"], key=lambda x: abs(x["rate"] - target_rate))
            row.append(best_match_t2)
        else:
            row.append(None)

        if len(teams_data) > 2:
            if target_rate > team3_max_rate + 0.005:
                row.append(None)
            else:
                best_match_t3 = min(teams_data[2]["patterns"], key=lambda x: abs(x["rate"] - target_rate))
                row.append(best_match_t3)
        else:
            row.append(None)

        aligned_rows.append(row)

    return {
        "headers": [{"team": td["team"], "remaining": td["remaining"], "current_w": td["current_w"], "current_l": td["current_l"]} for td in teams_data],
        "rows": aligned_rows
    }

def format_prob_sig1(p):
    if p <= 0.0001:
        return "-"
    if p < 1.0:
        if p < 0.1:
            return f"{p:.2f}%"
        return f"{p:.1f}%"
    return f"{int(round(p))}%"

def build_all_history_with_predictions(games_2025, games_2026):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS

    prior_stats = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}
    pitcher_stats = {}

    for g in games_2025:
        if g.get("status") == "finished" and g.get("home_score") is not None:
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
    history_snapshots = {}

    for target_date in all_dates:
        records = {t: {
            "team": t, "games": 0, "win": 0, "lose": 0, "draw": 0, "rs": 0, "ra": 0,
            "home": {"win": 0, "lose": 0, "draw": 0},
            "away": {"win": 0, "lose": 0, "draw": 0},
            "interleague": {"win": 0, "lose": 0, "draw": 0}
        } for t in all_teams}

        team_match_histories_before_today = {t: [] for t in all_teams}
        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}
        h2h_details = {t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in all_teams} for t1 in all_teams}

        for g in games_2026:
            if g.get("status") == "cancelled" or g.get("home_score") is None or g.get("away_score") is None:
                continue
            h, a = g["home"], g["away"]
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            g_date = g["date"]

            if g_date < target_date:
                team_match_histories_before_today[h].append({"rs": hs, "ra": as_})
                team_match_histories_before_today[a].append({"rs": as_, "ra": hs})
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
                    h2h_details[a][h]["lose"] += 1
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

        day_predictions = []
        processed_pairs = set()

        for g in reversed(games_2026):
            if g["date"] == target_date:
                h, a = g["home"], g["away"]
                pair_key = (h, a)
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)

                if g.get("status") == "cancelled":
                    continue

                p_away = get_rolling_recent_strength(team_match_histories_before_today[a], prior_stats[a])
                p_home = get_rolling_recent_strength(team_match_histories_before_today[h], prior_stats[h])

                h_start = g.get("home_starter") or g.get("home_pitcher") or ""
                a_start = g.get("away_starter") or g.get("away_pitcher") or ""
                h_start = h_start.strip() if h_start else "未定"
                a_start = a_start.strip() if a_start else "未定"

                prob_away, prob_home = calc_log5_matchup(p_away, p_home, a_start, h_start, pitcher_stats)

                hs, as_ = g.get("home_score"), g.get("away_score")
                is_fin = (hs is not None and as_ is not None and str(hs).strip() != "" and str(as_).strip() != "")

                if is_fin:
                    hs_int, as_int = int(hs), int(as_)
                    if hs_int > as_int:
                        h_label = f"勝利: {h_start}" if h_start != "未定" else "勝利"
                        a_label = f"敗戦: {a_start}" if a_start != "未定" else "敗戦"
                    elif hs_int < as_int:
                        h_label = f"敗戦: {h_start}" if h_start != "未定" else "敗戦"
                        a_label = f"勝利: {a_start}" if a_start != "未定" else "勝利"
                    else:
                        h_label = f"引分: {h_start}" if h_start != "未定" else "引分"
                        a_label = f"引分: {a_start}" if a_start != "未定" else "引分"
                else:
                    h_label = f"先発: {h_start}"
                    a_label = f"先発: {a_start}"

                day_predictions.append({
                    "home": h,
                    "away": a,
                    "home_starter": h_start,
                    "away_starter": a_start,
                    "home_status_text": h_label,
                    "away_status_text": a_label,
                    "home_prob": prob_home,
                    "away_prob": prob_away,
                    "actual_home_score": int(hs) if is_fin else None,
                    "actual_away_score": int(as_) if is_fin else None,
                    "is_finished": is_fin
                })

        day_predictions.reverse()

        history_snapshots[target_date] = {
            "central": c_table,
            "pacific": p_table,
            "predictions": day_predictions
        }

    latest_team_histories = {t: [] for t in all_teams}
    for g in games_2026:
        if g.get("status") == "finished" and g.get("home_score") is not None and g.get("away_score") is not None:
            h, a = g["home"], g["away"]
            latest_team_histories[h].append({"rs": int(g["home_score"]), "ra": int(g["away_score"])})
            latest_team_histories[a].append({"rs": int(g["away_score"]), "ra": int(g["home_score"])})

    actual_future_matches = [g for g in games_2026 if g.get("home_score") is None and g.get("status") != "cancelled"]
    c_future = [g for g in actual_future_matches if g["home"] in CENTRAL_TEAMS or g["away"] in CENTRAL_TEAMS]
    p_future = [g for g in actual_future_matches if g["home"] in PACIFIC_TEAMS or g["away"] in PACIFIC_TEAMS]

    dates_with_finished = [d for d in all_dates if any(g["date"] == d and g.get("home_score") is not None for g in games_2026)]
    last_eval_date = dates_with_finished[-1] if dates_with_finished else all_dates[0]
    eval_c_table = history_snapshots[last_eval_date]["central"]
    eval_p_table = history_snapshots[last_eval_date]["pacific"]

    c_rank_matrix, c_clinch_dates = simulate_full_season_probabilities(CENTRAL_TEAMS, eval_c_table, c_future, latest_team_histories, prior_stats)
    p_rank_matrix, p_clinch_dates = simulate_full_season_probabilities(PACIFIC_TEAMS, eval_p_table, p_future, latest_team_histories, prior_stats)

    def attach_probs(table, rank_mat):
        for t in table:
            probs = rank_mat.get(t["team"], {})
            t["champ_prob"] = 100 if t.get("magic_1st") == "確定" else probs.get(1, 0)
            t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else (probs.get(1, 0) + probs.get(2, 0) + probs.get(3, 0))
        return table

    for d in all_dates:
        if d in history_snapshots:
            history_snapshots[d]["central"] = attach_probs(history_snapshots[d]["central"], c_rank_matrix)
            history_snapshots[d]["pacific"] = attach_probs(history_snapshots[d]["pacific"], p_rank_matrix)

    # 決定日確率テーブル（npb.eikai.co.jp準拠＋球場名・累計確率算出）
    def build_filtered_clinch_schedule(team_name, future_matches, clinch_date_map, champ_prob):
        all_future_dates = sorted(list({m["date"] for m in future_matches} | set(clinch_date_map.keys())))
        all_rows = []

        for d in all_future_dates:
            team_m = next((m for m in future_matches if m["date"] == d and (m["home"] == team_name or m["away"] == team_name)), None)
            prob_raw = clinch_date_map.get(d, 0.0)

            m_int, d_int = int(d.split('-')[1]), int(d.split('-')[2])
            is_tentative = (m_int == 10 and d_int >= 7)
            date_display = f"({m_int}/{d_int})" if is_tentative else f"{m_int}/{d_int}"

            if team_m:
                is_home = (team_m["home"] == team_name)
                opp = team_m["away"] if is_home else team_m["home"]
                host = team_m["home"]
                ground = STADIUM_NAMES.get(host, "球場")

                p_opp = get_rolling_recent_strength(latest_team_histories[opp], prior_stats[opp])
                p_self = get_rolling_recent_strength(latest_team_histories[team_name], prior_stats[team_name])
                if is_home:
                    _, p_win = calc_log5_matchup(p_opp, p_self, "未定", "未定", {})
                else:
                    p_win, _ = calc_log5_matchup(p_self, p_opp, "未定", "未定", {})
                win_expect_str = str(int(round(p_win)))
            else:
                opp = "-"
                ground = "-"
                win_expect_str = "-"

            all_rows.append({
                "date": date_display,
                "raw_date": d,
                "opp": opp,
                "ground": ground,
                "clinch_prob_val": prob_raw,
                "win_expect": win_expect_str
            })

        first_idx = next((i for i, item in enumerate(all_rows) if item["clinch_prob_val"] > 0.001), None)
        if first_idx is not None:
            trimmed = all_rows[first_idx:]
        else:
            trimmed = [r for r in all_rows if r["opp"] != "-"][-8:]

        cum = 0.0
        for item in trimmed:
            val = item["clinch_prob_val"]
            cum += val

            if val < 0.001:
                item["clinch_prob_str"] = "-"
            elif val < 1.0:
                item["clinch_prob_str"] = f"{val:.1f}%" if val >= 0.1 else f"{val:.2f}%"
            else:
                item["clinch_prob_str"] = f"{int(round(val))}%"

            if cum < 0.001:
                item["cum_prob_str"] = "-"
            elif cum < 1.0:
                item["cum_prob_str"] = f"{cum:.1f}%" if cum >= 0.1 else f"{cum:.2f}%"
            else:
                item["cum_prob_str"] = f"{int(round(cum))}%"

        return trimmed

    last_snap_c = history_snapshots[last_eval_date]["central"]
    last_snap_p = history_snapshots[last_eval_date]["pacific"]

    c_clinch_schedules = {t: build_filtered_clinch_schedule(t, c_future, c_clinch_dates.get(t, {}), last_snap_c[idx]["champ_prob"]) for idx, t in enumerate(CENTRAL_TEAMS)}
    p_clinch_schedules = {t: build_filtered_clinch_schedule(t, p_future, p_clinch_dates.get(t, {}), last_snap_p[idx]["champ_prob"]) for idx, t in enumerate(PACIFIC_TEAMS)}

    c_lines_grid = build_aligned_championship_grid(last_snap_c)
    p_lines_grid = build_aligned_championship_grid(last_snap_p)

    simulation_payload = {
        "central_rank_matrix": c_rank_matrix,
        "pacific_rank_matrix": p_rank_matrix,
        "central_clinch_schedules": c_clinch_schedules,
        "pacific_clinch_schedules": p_clinch_schedules,
        "central_lines_grid": c_lines_grid,
        "pacific_lines_grid": p_lines_grid
    }

    jst_today = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m-%d")
    default_display_date = jst_today if jst_today in all_dates else last_eval_date

    return all_dates, default_display_date, history_snapshots, simulation_payload

def main():
    games_2025, games_2026 = load_all_games()
    dates, default_latest, history, sim_data = build_all_history_with_predictions(games_2025, games_2026)

    output = {
        "latest_date": default_latest,
        "available_dates": dates,
        "history": history,
        "simulation": sim_data
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"解析＆シミュレーション更新完了（決定日確率・累積確率完全同期）：{dates[0]} 〜 {dates[-1]}")

if __name__ == "__main__":
    main()
