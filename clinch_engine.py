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
STANDINGS_FILE = "standings.json"
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

PARK_FACTORS = {
    "神宮": 1.15, "横浜": 1.10, "エスコンF": 1.06, "ZOZO": 1.02,
    "東京D": 1.00, "マツダS": 0.97, "PayPay": 0.96, "京セラ": 0.95,
    "楽天モバイル": 0.95, "ベルーナ": 0.90, "甲子園": 0.88, "バンテリン": 0.84
}

# 2016-2025年NPBレギュラーシーズン実測引き分け率（382引分 / 8,580試合 = 約4.45%）
NPB_DRAW_RATE = 0.0445

def normalize_team(name):
    if not name:
        return ""
    clean = name.strip()
    return TEAM_ALIASES.get(clean, clean)

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def normalize_probabilities_to_100(prob_dict):
    total_val = sum(prob_dict.values())
    if total_val <= 0:
        return {k: 0 for k in prob_dict}
    scaled = {k: (v / total_val) * 100.0 for k, v in prob_dict.items()}
    floored = {k: int(math.floor(v)) for k, v in scaled.items()}
    remainder = {k: scaled[k] - floored[k] for k in scaled}
    remaining_sum = 100 - sum(floored.values())
    sorted_by_remainder = sorted(remainder.keys(), key=lambda k: remainder[k], reverse=True)
    for i in range(remaining_sum):
        floored[sorted_by_remainder[i]] += 1
    return floored

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

        date_m = re.match(r'^(\d{1,2})\/(\d{1,2})(?:[（(][日月火水木金土][）)])?\s*(.*)$', line)
        if date_m:
            m, d = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"{target_year}-{m:02d}-{d:02d}"
            line = date_m.group(3).strip()
            if not line:
                continue

        if not current_date:
            continue

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

        match_sched = re.search(r'([^\s\d]+)\s*-\s*([^\s\d]+)', line)
        if match_sched:
            h = normalize_team(match_sched.group(1))
            a = normalize_team(match_sched.group(2))
            if h in all_teams and a in all_teams:
                starters = re.findall(r'(?:先発|予告)[:：]?\s*([^\s,，()（）]+)', line)
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

    manual_map = {}
    if os.path.exists(MANUAL_DB_FILE):
        try:
            with open(MANUAL_DB_FILE, "r", encoding="utf-8") as f:
                manual_payload = json.load(f)

            manual_games = manual_payload if isinstance(manual_payload, list) else manual_payload.get("games", [])

            for mg in manual_games:
                if not mg or "date" not in mg or "home" not in mg or "away" not in mg:
                    continue
                norm_h = normalize_team(mg["home"])
                norm_a = normalize_team(mg["away"])

                entry = dict(mg)
                entry["home"] = norm_h
                entry["away"] = norm_a

                hs_val = mg.get("home_score")
                as_val = mg.get("away_score")

                is_fin = False
                if hs_val is not None and as_val is not None:
                    hs_s = str(hs_val).strip()
                    as_s = str(as_val).strip()
                    if hs_s != "" and as_s != "" and hs_s != "null" and as_s != "null":
                        try:
                            entry["home_score"] = int(hs_s)
                            entry["away_score"] = int(as_s)
                            entry["status"] = "finished"
                            is_fin = True
                        except ValueError:
                            pass

                if not is_fin:
                    if mg.get("status") == "cancelled":
                        entry["home_score"] = None
                        entry["away_score"] = None
                        entry["status"] = "cancelled"
                    else:
                        entry["home_score"] = None
                        entry["away_score"] = None
                        entry["status"] = "scheduled"

                manual_map[(mg["date"], norm_h, norm_a)] = entry
        except Exception as e:
            print(f"games_db.json 読込警告: {e}")

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

    merged_2026.sort(key=lambda x: x["date"])
    return games_2025, merged_2026

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

BASE_PYTHAGOREAN_EXP = 1.83
HOME_ODDS_ADVANTAGE = 1.15

def calc_pythagorean_rate(rs, ra, stadium_name="東京D"):
    if rs <= 0 and ra <= 0:
        return 0.5
    pf = PARK_FACTORS.get(stadium_name, 1.00)
    eff_exp = BASE_PYTHAGOREAN_EXP * math.pow(pf, 0.25)
    rs_pow = math.pow(max(0.1, rs), eff_exp)
    ra_pow = math.pow(max(0.1, ra), eff_exp)
    return rs_pow / (rs_pow + ra_pow)

def get_season_true_strength(team_total_rs, team_total_ra, games_played, prior_stats, stadium_name="東京D"):
    if games_played == 0:
        avg_rs = prior_stats["rs"] / max(1, prior_stats["games"]) if prior_stats["games"] > 0 else 3.5
        avg_ra = prior_stats["ra"] / max(1, prior_stats["games"]) if prior_stats["games"] > 0 else 3.5
        return calc_pythagorean_rate(avg_rs, avg_ra, stadium_name)

    prior_weight = max(0.0, (80.0 - games_played) / 80.0) * 20.0
    p_rs = (prior_stats["rs"] / max(1, prior_stats["games"])) * prior_weight
    p_ra = (prior_stats["ra"] / max(1, prior_stats["games"])) * prior_weight

    final_rs = (team_total_rs + p_rs) / (games_played + prior_weight)
    final_ra = (team_total_ra + p_ra) / (games_played + prior_weight)

    return calc_pythagorean_rate(final_rs, final_ra, stadium_name)

def get_pitcher_multiplier(pitcher_name, pitcher_stats, stadium_name="東京D"):
    if not pitcher_name or pitcher_name == "未定":
        return 1.0
    st = pitcher_stats.get(pitcher_name, {"win": 0, "lose": 0})
    w, l = st["win"], st["lose"]
    shrunken_rate = (w + 4.0) / (w + l + 8.0)
    odds = shrunken_rate / (1.0 - shrunken_rate)

    pf = PARK_FACTORS.get(stadium_name, 1.00)
    stadium_pitcher_leverage = 0.25 / math.pow(pf, 0.5)

    return math.pow(odds, stadium_pitcher_leverage)

def calc_log5_matchup(p_away, p_home, away_pitcher, home_pitcher, pitcher_stats, stadium_name="東京D"):
    denom = p_away + p_home - (2.0 * p_away * p_home)
    p_neutral_away = 0.5 if denom <= 0 else (p_away - (p_away * p_home)) / denom
    p_neutral_away = max(0.01, min(0.99, p_neutral_away))

    odds_away = p_neutral_away / (1.0 - p_neutral_away)
    adj_odds_away = odds_away / HOME_ODDS_ADVANTAGE

    m_away = get_pitcher_multiplier(away_pitcher, pitcher_stats, stadium_name)
    m_home = get_pitcher_multiplier(home_pitcher, pitcher_stats, stadium_name)
    pitcher_ratio = m_away / max(0.1, m_home)
    final_odds_away = adj_odds_away * pitcher_ratio

    final_p_away = final_odds_away / (1.0 + final_odds_away)
    final_p_home = 1.0 - final_p_away

    return round(final_p_away * 100.0, 1), round(final_p_home * 100.0, 1)

# ★ 修正①: 優勝決定日判定を「NPB公式勝率ルール＋タイブレーク条件」に完全移行
def simulate_full_season_probabilities(league_teams, current_standings, remaining_matches, team_total_stats, prior_stats):
    NUM_SIMS = 3000
    rank_counts = {t: {r: 0 for r in range(1, 7)} for t in league_teams}
    clinch_date_counts = {t: {} for t in league_teams}

    base_probs = {}
    for t1 in league_teams:
        for t2 in league_teams:
            if t1 != t2:
                stadium = STADIUM_NAMES.get(t1, "東京D")
                p1 = get_season_true_strength(team_total_stats[t1]["rs"], team_total_stats[t1]["ra"], team_total_stats[t1]["games"], prior_stats[t1], stadium)
                p2 = get_season_true_strength(team_total_stats[t2]["rs"], team_total_stats[t2]["ra"], team_total_stats[t2]["games"], prior_stats[t2], stadium)
                pa, ph = calc_log5_matchup(p2, p1, "未定", "未定", {}, stadium)
                base_probs[(t1, t2)] = ph / 100.0

    base_wins = {t["team"]: t["win"] for t in current_standings}
    base_loses = {t["team"]: t["lose"] for t in current_standings}
    base_draws = {t["team"]: t["draw"] for t in current_standings}
    
    sorted_matches = sorted([m for m in remaining_matches if m.get("status") == "scheduled"], key=lambda x: x["date"])

    matches_by_date = {}
    for m in sorted_matches:
        d = m["date"]
        if d not in matches_by_date:
            matches_by_date[d] = []
        matches_by_date[d].append(m)

    sorted_dates = sorted(matches_by_date.keys())

    # 各チームの残り試合総数を算出
    team_future_game_counts = {t: 0 for t in league_teams}
    for m in sorted_matches:
        team_future_game_counts[m["home"]] += 1
        team_future_game_counts[m["away"]] += 1

    for _ in range(NUM_SIMS):
        sim_w = dict(base_wins)
        sim_l = dict(base_loses)
        sim_d = dict(base_draws)
        sim_played_future = {t: 0 for t in league_teams}
        clinched_day = {t: None for t in league_teams}

        for d in sorted_dates:
            for match in matches_by_date[d]:
                h, a = match["home"], match["away"]
                sim_played_future[h] += 1
                sim_played_future[a] += 1

                p_home_decided = base_probs.get((h, a), 0.535)

                rnd = random.random()
                if rnd < NPB_DRAW_RATE:
                    sim_d[h] += 1
                    sim_d[a] += 1
                else:
                    if random.random() < p_home_decided:
                        sim_w[h] += 1
                        sim_l[a] += 1
                    else:
                        sim_w[a] += 1
                        sim_l[h] += 1

            # NPB公式勝率ルール（W / (W + L)）に基づく順位付け
            sim_rates = sorted([(t, calc_win_rate(sim_w[t], sim_l[t]), sim_w[t]) for t in league_teams],
                               key=lambda x: (x[1], x[2]), reverse=True)
            leader = sim_rates[0][0]

            # 厳密なクリンチ判定（首位チームが残り全敗した時の最低保証勝率 vs 2位以下の残り全勝時の最高到達勝率）
            if clinched_day[leader] is None:
                leader_rem = team_future_game_counts[leader] - sim_played_future[leader]
                leader_min_rate = calc_win_rate(sim_w[leader], sim_l[leader] + leader_rem)
                
                can_overtake = False
                for ot in league_teams:
                    if ot == leader:
                        continue
                    ot_rem = team_future_game_counts[ot] - sim_played_future[ot]
                    ot_max_rate = calc_win_rate(sim_w[ot] + ot_rem, sim_l[ot])

                    # 勝率で上回る、または同率で勝数が上回る可能性がある場合は未確定
                    if ot_max_rate > leader_min_rate + 1e-6:
                        can_overtake = True
                        break
                    elif abs(ot_max_rate - leader_min_rate) <= 1e-6:
                        if (sim_w[ot] + ot_rem) >= sim_w[leader]:
                            can_overtake = True
                            break

                if not can_overtake:
                    clinched_day[leader] = d

        sim_rates = sorted([(t, calc_win_rate(sim_w[t], sim_l[t]), sim_w[t]) for t in league_teams],
                           key=lambda x: (x[1], x[2]), reverse=True)

        champ = sim_rates[0][0]
        c_date = clinched_day[champ]
        if c_date is not None:
            clinch_date_counts[champ][c_date] = clinch_date_counts[champ].get(c_date, 0) + 1

        for idx, item in enumerate(sim_rates):
            rank_counts[item[0]][idx + 1] += 1

    raw_champ_probs = {t: (rank_counts[t][1] / NUM_SIMS) * 100.0 for t in league_teams}
    norm_champ_probs = normalize_probabilities_to_100(raw_champ_probs)

    final_rank_matrix = {}
    for t in league_teams:
        final_rank_matrix[t] = {}
        final_rank_matrix[t][1] = norm_champ_probs[t]
        for r in range(2, 7):
            final_rank_matrix[t][r] = round((rank_counts[t][r] / NUM_SIMS) * 100)

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

    base_team = teams_data[0]
    base_patterns = base_team["patterns"]
    num_rows = len(base_patterns)

    aligned_rows = [[base_p] for base_p in base_patterns]

    for td in teams_data[1:]:
        pats = td["patterns"]
        t_max_rate = pats[0]["rate"]
        best_start = min(range(num_rows), key=lambda r: abs(base_patterns[r]["rate"] - t_max_rate))

        assigned = [None] * num_rows
        for p_idx, p in enumerate(pats):
            row_pos = best_start + p_idx
            if row_pos < num_rows:
                assigned[row_pos] = p

        for r_idx in range(num_rows):
            aligned_rows[r_idx].append(assigned[r_idx])

    for r in aligned_rows:
        while len(r) < len(teams_data):
            r.append(None)

    return {
        "headers": [{"team": td["team"], "remaining": td["remaining"], "current_w": td["current_w"], "current_l": td["current_l"]} for td in teams_data],
        "rows": aligned_rows
    }

def build_all_history_with_predictions(games_2025, games_2026):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS

    prior_stats = {t: {"games": 0, "rs": 0, "ra": 0} for t in all_teams}
    base_pitcher_stats = {}

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
                if hp not in base_pitcher_stats: base_pitcher_stats[hp] = {"win": 0, "lose": 0}
                if hs > as_: base_pitcher_stats[hp]["win"] += 1
                elif hs < as_: base_pitcher_stats[hp]["lose"] += 1
            if ap:
                if ap not in base_pitcher_stats: base_pitcher_stats[ap] = {"win": 0, "lose": 0}
                if as_ > hs: base_pitcher_stats[ap]["win"] += 1
                elif as_ < hs: base_pitcher_stats[ap]["lose"] += 1

    all_dates = sorted(list({g["date"] for g in games_2026}))
    history_snapshots = {}

    for target_date in all_dates:
        records = {t: {
            "team": t, "games": 0, "win": 0, "lose": 0, "draw": 0, "rs": 0, "ra": 0,
            "home": {"win": 0, "lose": 0, "draw": 0},
            "away": {"win": 0, "lose": 0, "draw": 0},
            "interleague": {"win": 0, "lose": 0, "draw": 0}
        } for t in all_teams}

        team_total_stats_before_today = {t: {"rs": 0, "ra": 0, "games": 0} for t in all_teams}
        h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}
        h2h_details = {t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in all_teams} for t1 in all_teams}

        current_day_pitcher_stats = {k: dict(v) for k, v in base_pitcher_stats.items()}

        for g in games_2026:
            if g.get("status") == "cancelled" or g.get("home_score") is None or g.get("away_score") is None:
                continue
            h, a = g["home"], g["away"]
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            g_date = g["date"]

            if g_date < target_date:
                team_total_stats_before_today[h]["rs"] += hs
                team_total_stats_before_today[h]["ra"] += as_
                team_total_stats_before_today[h]["games"] += 1
                team_total_stats_before_today[a]["rs"] += as_
                team_total_stats_before_today[a]["ra"] += hs
                team_total_stats_before_today[a]["games"] += 1

                hp, ap = g.get("home_pitcher"), g.get("away_pitcher")
                if hp:
                    if hp not in current_day_pitcher_stats: current_day_pitcher_stats[hp] = {"win": 0, "lose": 0}
                    if hs > as_: current_day_pitcher_stats[hp]["win"] += 1
                    elif hs < as_: current_day_pitcher_stats[hp]["lose"] += 1
                if ap:
                    if ap not in current_day_pitcher_stats: current_day_pitcher_stats[ap] = {"win": 0, "lose": 0}
                    if as_ > hs: current_day_pitcher_stats[ap]["win"] += 1
                    elif as_ < hs: current_day_pitcher_stats[ap]["lose"] += 1

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

                stadium = STADIUM_NAMES.get(h, "東京D")
                p_away = get_season_true_strength(team_total_stats_before_today[a]["rs"], team_total_stats_before_today[a]["ra"], team_total_stats_before_today[a]["games"], prior_stats[a], stadium)
                p_home = get_season_true_strength(team_total_stats_before_today[h]["rs"], team_total_stats_before_today[h]["ra"], team_total_stats_before_today[h]["games"], prior_stats[h], stadium)

                h_start = g.get("home_starter") or g.get("home_pitcher") or ""
                a_start = g.get("away_starter") or g.get("away_pitcher") or ""
                h_start = h_start.strip() if h_start else "未定"
                a_start = a_start.strip() if a_start else "未定"

                prob_away, prob_home = calc_log5_matchup(p_away, p_home, a_start, h_start, current_day_pitcher_stats, stadium)

                hs, as_ = g.get("home_score"), g.get("away_score")
                is_fin = (g.get("status") == "finished" and hs is not None and as_ is not None)

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

    latest_team_totals = {t: {"rs": 0, "ra": 0, "games": 0} for t in all_teams}
    for g in games_2026:
        if g.get("status") == "finished" and g.get("home_score") is not None and g.get("away_score") is not None:
            h, a = g["home"], g["away"]
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            latest_team_totals[h]["rs"] += hs
            latest_team_totals[h]["ra"] += as_
            latest_team_totals[h]["games"] += 1
            latest_team_totals[a]["rs"] += as_
            latest_team_totals[a]["ra"] += hs
            latest_team_totals[a]["games"] += 1

    actual_future_matches = [g for g in games_2026 if g.get("home_score") is None and g.get("status") != "cancelled"]
    c_future = [g for g in actual_future_matches if g["home"] in CENTRAL_TEAMS or g["away"] in CENTRAL_TEAMS]
    p_future = [g for g in actual_future_matches if g["home"] in PACIFIC_TEAMS or g["away"] in PACIFIC_TEAMS]

    dates_with_finished = [d for d in all_dates if any(g["date"] == d and g.get("status") == "finished" for g in games_2026)]
    last_eval_date = dates_with_finished[-1] if dates_with_finished else all_dates[0]
    eval_c_file = history_snapshots.get(last_eval_date, history_snapshots[all_dates[0]])
    eval_c_table = eval_c_file["central"]
    eval_p_table = eval_c_file["pacific"]

    c_rank_matrix, c_clinch_dates = simulate_full_season_probabilities(CENTRAL_TEAMS, eval_c_table, c_future, latest_team_totals, prior_stats)
    p_rank_matrix, p_clinch_dates = simulate_full_season_probabilities(PACIFIC_TEAMS, eval_p_table, p_future, latest_team_totals, prior_stats)

    def attach_probs_for_snapshot(table, is_latest):
        if is_latest:
            rank_mat = c_rank_matrix if table[0]["team"] in CENTRAL_TEAMS else p_rank_matrix
            for t in table:
                probs = rank_mat.get(t["team"], {})
                t["champ_prob"] = 100 if t.get("magic_1st") == "確定" else probs.get(1, 0)
                t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else (probs.get(1, 0) + probs.get(2, 0) + probs.get(3, 0))
            return table

        raw_map = {}
        for t in table:
            played = max(1, t["games"])
            rate = t["rate"]
            diff = t["diff"]
            sample_weight = min(1.0, played / 110.0)
            regressed_rate = (rate * sample_weight) + (0.500 * (1.0 - sample_weight))
            diff_penalty = (diff * 0.35) * sample_weight
            power = math.exp((regressed_rate - 0.500) * 8.0 - diff_penalty)
            raw_map[t["team"]] = power

        norm_map = normalize_probabilities_to_100(raw_map)
        for t in table:
            t["champ_prob"] = norm_map.get(t["team"], 0)
            played = max(1, t["games"])
            sw = min(1.0, played / 100.0)
            if t["rank"] <= 3:
                t["cs_prob"] = min(100, max(25, int(50 + (4 - t["rank"]) * 15 - t["diff"] * 3 * sw)))
            else:
                t["cs_prob"] = max(5, int(45 - (t["rank"] - 3) * 12 - t["diff"] * 3 * sw))
        return table

    for d in all_dates:
        if d in history_snapshots:
            is_latest = (d >= last_eval_date)
            history_snapshots[d]["central"] = attach_probs_for_snapshot(history_snapshots[d]["central"], is_latest)
            history_snapshots[d]["pacific"] = attach_probs_for_snapshot(history_snapshots[d]["pacific"], is_latest)

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

                stadium = STADIUM_NAMES.get(host, "東京D")
                p_opp = get_season_true_strength(latest_team_totals[opp]["rs"], latest_team_totals[opp]["ra"], latest_team_totals[opp]["games"], prior_stats[opp], stadium)
                p_self = get_season_true_strength(latest_team_totals[team_name]["rs"], latest_team_totals[team_name]["ra"], latest_team_totals[team_name]["games"], prior_stats[team_name], stadium)
                if is_home:
                    _, p_win = calc_log5_matchup(p_opp, p_self, "未定", "未定", {}, stadium)
                else:
                    p_win, _ = calc_log5_matchup(p_self, p_opp, "未定", "未定", {}, stadium)
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
    final_default_date = jst_today if jst_today in all_dates else last_eval_date

    return all_dates, final_default_date, history_snapshots, simulation_payload

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

    latest_snapshot = history.get(default_latest, history[dates[-1]])
    standings_legacy_payload = {
        "updated_at": f"{default_latest} (Auto Sync)",
        "central": latest_snapshot.get("central", []),
        "pacific": latest_snapshot.get("pacific", [])
    }
    with open(STANDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(standings_legacy_payload, f, ensure_ascii=False, indent=2)

    print(f"解析＆シミュレーション更新完了（NPB公式勝率規定クリンチ判定・実測引分率同期）：{dates[0]} 〜 {dates[-1]}")

if __name__ == "__main__":
    main()
