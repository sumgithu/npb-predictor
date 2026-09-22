import datetime
import json
import math
import os
import random
import re
from collections import defaultdict
from copy import deepcopy

# ============================================================
# NPB Predictor - improved probability engine
# ============================================================
# 目的:
#  1) 現在利用可能な試合結果・日付・球場・先発情報から
#     1試合の勝/分/敗確率を推定
#  2) その確率を用いてレギュラーシーズン完走をMonte Carloする
#  3) 既存 history_standings.json の構造をできるだけ維持する
#
# 旧モデルからの主要変更:
#  - Pythagorean + Log5 を主モデルから外し、得失点を直接使う
#    opponent-adjusted の Poisson attack/defense model に変更
#  - 2026年の直近試合を指数加重して「最近の強さ」を反映
#  - 2016-2025 を複数年priorとして利用（2025だけに依存しない）
#  - ホームアドバンテージと球場run environmentを過去データから推定
#  - Poisson score distributionから勝/分/敗を直接計算（固定4.5%引分を廃止）
#  - 先発補正は「実際に先発情報が確認できるmanual DB」のみ利用
#  - 日付からrest effectを過去データで推定
#  - H2Hは予測モデルへ二重計上せず、順位確定計算にのみ使用
#  - historical snapshotの優勝確率も、その時点の情報だけでMonte Carlo推定
#  - 未来情報リークを防止
# ============================================================

TOTAL_GAMES = 143
GAMES_INTRA = 25
GAMES_INTER = 3
HISTORY_FILE = "history_standings.json"
MANUAL_DB_FILE = "games_db.json"
TEXT_LOG_FILE = "2016-2026プロ野球レギュラーシーズン結果.txt"
FALLBACK_CSV_FILE = "npb_games_clean.csv"

# Simulation
MAIN_NUM_SIMS = 5000
HISTORICAL_NUM_SIMS = 300
RANDOM_SEED = 20260921

# Early/mid-season model-uncertainty band.
# This is separate from Monte Carlo sampling error. We perturb the fitted
# attack/defense parameters using a Laplace-style diagonal approximation and
# then rerun smaller season simulations. The public page uses the 10th-90th
# percentile of these scenario results while more than one team still has
# a self-clinchable path to 1st place.
UNCERTAINTY_MODEL_SIMS = 10
UNCERTAINTY_SEASON_SIMS = 60
UNCERTAINTY_LOW_Q = 0.10
UNCERTAINTY_HIGH_Q = 0.90
MODEL_UNCERTAINTY_INFLATION = 1.35

# Run model hyperparameters.
# 50日程度の半減期なら、4月の試合を9月時点で強く引きずりすぎない。
RECENCY_HALF_LIFE_DAYS = 50.0
PRIOR_SEASON_DECAY = 0.55
PRIOR_L2 = 8.0
FIT_ITERATIONS = 70
FIT_LEARNING_RATE = 0.025

# Pitcher information in the current DB is sparse and W/L is noisy.
# Therefore keep the impact deliberately small.
PITCHER_PRIOR_STARTS = 8.0
MAX_PITCHER_LOG_RUN_EFFECT = 0.08

# Poisson tail. MLB/NPB game scores above 20 are extremely rare; the remaining
# tail is renormalized automatically.
MAX_RUNS = 24

# Teams and canonical names
CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]
ALL_TEAMS = CENTRAL_TEAMS + PACIFIC_TEAMS

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
    "中日": "中日", "中日ドラゴンズ": "中日",
}

STADIUM_NAMES = {
    "阪神": "甲子園", "巨人": "東京D", "ＤｅＮＡ": "横浜",
    "ヤクルト": "神宮", "中日": "バンテリン", "広島": "マツダS",
    "ソフトバンク": "PayPay", "日本ハム": "エスコンF", "ロッテ": "ZOZO",
    "楽天": "楽天モバイル", "オリックス": "京セラ", "西武": "ベルーナ",
}

# ------------------------------------------------------------
# Generic helpers
# ------------------------------------------------------------

def normalize_team(name):
    if not name:
        return ""
    clean = str(name).strip()
    return TEAM_ALIASES.get(clean, clean)


def is_finished(game):
    return (
        game.get("status") == "finished"
        and game.get("home_score") is not None
        and game.get("away_score") is not None
    )


def is_cancelled(game):
    return game.get("status") == "cancelled"


def safe_exp(x):
    return math.exp(max(-8.0, min(8.0, x)))


def sigmoid(x):
    x = max(-35.0, min(35.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def logit(p):
    p = max(1e-8, min(1.0 - 1e-8, p))
    return math.log(p / (1.0 - p))


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
    for i in range(max(0, remaining_sum)):
        floored[sorted_by_remainder[i]] += 1
    return floored


def parse_date(value):
    return datetime.date.fromisoformat(value)


def days_between(date_a, date_b):
    return (parse_date(date_a) - parse_date(date_b)).days

# ------------------------------------------------------------
# Input parser
# ------------------------------------------------------------

def parse_year_games_from_text(raw_text, target_year):
    """Parse one season from the user's long text log.

    Important: finished games contain winning/losing decision pitchers, not
    necessarily confirmed starters. They are therefore NOT treated as starter
    observations. Future games may contain explicit 先発/予告 information.
    """
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    sec_key = f"\n{target_year}\n"
    if sec_key in normalized:
        sec = normalized.split(sec_key)[-1]
    elif normalized.startswith(f"{target_year}\n"):
        sec = normalized.split(f"{target_year}\n")[-1]
    else:
        return []

    for ny in range(target_year + 1, 2030):
        if f"\n{ny}\n" in sec:
            sec = sec.split(f"\n{ny}\n")[0]
            break

    games = []
    current_date = None

    for raw_line in sec.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # A CSV-like format, if present in future data.
        csv_parts = [p.strip() for p in line.split(",")]
        if len(csv_parts) >= 6 and re.match(r"^\d{4}-\d{2}-\d{2}$", csv_parts[0]):
            c_date, h_raw, a_raw = csv_parts[0], csv_parts[1], csv_parts[2]
            h, a = normalize_team(h_raw), normalize_team(a_raw)
            if h in ALL_TEAMS and a in ALL_TEAMS:
                if csv_parts[3] == "" or (len(csv_parts) >= 8 and csv_parts[7] == "1"):
                    games.append({
                        "date": c_date, "home": h, "away": a,
                        "home_score": None, "away_score": None,
                        "home_pitcher": "", "away_pitcher": "",
                        "home_starter": "未定", "away_starter": "未定",
                        "starter_confirmed": False,
                        "status": "cancelled",
                        "source": "text",
                    })
                else:
                    hs, as_ = int(csv_parts[3]), int(csv_parts[4])
                    p_info = csv_parts[5] if len(csv_parts) >= 6 else ""
                    games.append({
                        "date": c_date, "home": h, "away": a,
                        "home_score": hs, "away_score": as_,
                        # In CSV source this field may be a decision pitcher.
                        "home_pitcher": p_info, "away_pitcher": p_info,
                        "home_starter": "未定", "away_starter": "未定",
                        "starter_confirmed": False,
                        "status": "finished",
                        "source": "text",
                    })
                continue

        date_m = re.match(r"^(\d{1,2})\/(\d{1,2})(?:[（(][日月火水木金土][）)])?\s*(.*)$", line)
        if date_m:
            month, day = int(date_m.group(1)), int(date_m.group(2))
            current_date = f"{target_year}-{month:02d}-{day:02d}"
            line = date_m.group(3).strip()
            if not line:
                continue

        if not current_date:
            continue

        if "中止" in line or "ノーゲーム" in line:
            match_can = re.search(r"([^\s\d]+)\s*(?:中止|ノーゲーム)\s*([^\s\d]+)", line)
            if match_can:
                h, a = normalize_team(match_can.group(1)), normalize_team(match_can.group(2))
                if h in ALL_TEAMS and a in ALL_TEAMS:
                    games.append({
                        "date": current_date, "home": h, "away": a,
                        "home_score": None, "away_score": None,
                        "home_pitcher": "", "away_pitcher": "",
                        "home_starter": "未定", "away_starter": "未定",
                        "starter_confirmed": False,
                        "status": "cancelled",
                        "source": "text",
                    })
            continue

        # Example:
        # 阪神 3 - 2 DeNA 甲子園 18:00 勝：A 敗：B
        match_fin = re.search(r"([^\s\d]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s\d]+)", line)
        if match_fin:
            h = normalize_team(match_fin.group(1))
            hs = int(match_fin.group(2))
            as_ = int(match_fin.group(3))
            a = normalize_team(match_fin.group(4))
            if h in ALL_TEAMS and a in ALL_TEAMS:
                win_p = re.search(r"勝(?:利)?[:：]\s*([^\s,，]+)", line)
                lose_p = re.search(r"敗(?:戦)?[:：]\s*([^\s,，]+)", line)
                draw_p = re.findall(r"分[:：]\s*([^\s,，]+)", line)
                win_pitcher = win_p.group(1) if win_p else ""
                lose_pitcher = lose_p.group(1) if lose_p else ""

                if hs > as_:
                    h_decision, a_decision = win_pitcher, lose_pitcher
                elif hs < as_:
                    h_decision, a_decision = lose_pitcher, win_pitcher
                else:
                    h_decision = draw_p[0] if len(draw_p) > 0 else ""
                    a_decision = draw_p[1] if len(draw_p) > 1 else ""

                games.append({
                    "date": current_date, "home": h, "away": a,
                    "home_score": hs, "away_score": as_,
                    "home_pitcher": h_decision,
                    "away_pitcher": a_decision,
                    "home_starter": "未定",
                    "away_starter": "未定",
                    "starter_confirmed": False,
                    "status": "finished",
                    "source": "text",
                })
            continue

        # Postponement/reserve-day notation used in the master text:
        #   DeNA (予備日) 中日
        # Treat it as the scheduled matchup DeNA - 中日.
        match_reserve = re.search(r"^(.+?)\s*\(\s*予備日\s*\)\s*(.+?)(?:\s+.*)?$", line)
        if match_reserve:
            h = normalize_team(match_reserve.group(1).strip())
            a = normalize_team(match_reserve.group(2).strip())
            if h in ALL_TEAMS and a in ALL_TEAMS:
                games.append({
                    "date": current_date, "home": h, "away": a,
                    "home_score": None, "away_score": None,
                    "home_pitcher": "", "away_pitcher": "",
                    "home_starter": "未定", "away_starter": "未定",
                    "starter_confirmed": False,
                    "status": "scheduled",
                    "source": "text",
                    "reserve_day": True,
                })
            continue

        match_sched = re.search(r"([^\s\d]+)\s*-\s*([^\s\d]+)", line)
        if match_sched:
            h = normalize_team(match_sched.group(1))
            a = normalize_team(match_sched.group(2))
            if h in ALL_TEAMS and a in ALL_TEAMS:
                starters = re.findall(r"(?:先発|予告)[:：]?\s*([^\s,，()（）]+)", line)
                h_starter = starters[0] if len(starters) > 0 else "未定"
                a_starter = starters[1] if len(starters) > 1 else "未定"
                confirmed = h_starter != "未定" or a_starter != "未定"
                games.append({
                    "date": current_date, "home": h, "away": a,
                    "home_score": None, "away_score": None,
                    "home_pitcher": "", "away_pitcher": "",
                    "home_starter": h_starter, "away_starter": a_starter,
                    "starter_confirmed": confirmed,
                    "status": "scheduled",
                    "source": "text",
                })
            continue

    return games


def _normalize_manual_entry(mg):
    if not mg or "date" not in mg or "home" not in mg or "away" not in mg:
        return None
    h, a = normalize_team(mg["home"]), normalize_team(mg["away"])
    entry = dict(mg)
    entry["home"] = h
    entry["away"] = a

    hs_val = mg.get("home_score")
    as_val = mg.get("away_score")
    is_fin = False
    if hs_val is not None and as_val is not None:
        hs_s, as_s = str(hs_val).strip(), str(as_val).strip()
        if hs_s not in ("", "null") and as_s not in ("", "null"):
            try:
                entry["home_score"] = int(hs_s)
                entry["away_score"] = int(as_s)
                entry["status"] = "finished"
                is_fin = True
            except ValueError:
                pass

    if not is_fin:
        entry["home_score"] = None
        entry["away_score"] = None
        entry["status"] = "cancelled" if mg.get("status") == "cancelled" else "scheduled"

    hs = entry.get("home_starter") or mg.get("home_pitcher") or "未定"
    a_s = entry.get("away_starter") or mg.get("away_pitcher") or "未定"
    entry["home_starter"] = str(hs).strip() if hs else "未定"
    entry["away_starter"] = str(a_s).strip() if a_s else "未定"
    entry["starter_confirmed"] = (
        entry["home_starter"] not in ("", "未定")
        or entry["away_starter"] not in ("", "未定")
    )
    entry["source"] = "manual"
    return entry


def load_all_games():
    historical_games = []
    games_2026_master = []
    active_master = TEXT_LOG_FILE if os.path.exists(TEXT_LOG_FILE) else FALLBACK_CSV_FILE

    if os.path.exists(active_master):
        with open(active_master, "r", encoding="utf-8") as f:
            raw_text = f.read()
        for year in range(2016, 2026):
            historical_games.extend(parse_year_games_from_text(raw_text, year))
        games_2026_master = parse_year_games_from_text(raw_text, 2026)

    manual_map = {}
    if os.path.exists(MANUAL_DB_FILE):
        try:
            with open(MANUAL_DB_FILE, "r", encoding="utf-8") as f:
                manual_payload = json.load(f)
            manual_games = manual_payload if isinstance(manual_payload, list) else manual_payload.get("games", [])
            for mg in manual_games:
                entry = _normalize_manual_entry(mg)
                if entry:
                    manual_map[(entry["date"], entry["home"], entry["away"])] = entry
        except Exception as exc:
            print(f"games_db.json 読込警告: {exc}")

    merged_2026 = []
    applied_keys = set()
    for master in games_2026_master:
        key = (master["date"], master["home"], master["away"])
        if key in manual_map:
            merged_2026.append(manual_map[key])
            applied_keys.add(key)
        else:
            merged_2026.append(master)

    for key, manual in manual_map.items():
        if key not in applied_keys:
            merged_2026.append(manual)

    merged_2026.sort(key=lambda x: (x["date"], x["home"], x["away"]))
    historical_games.sort(key=lambda x: (x["date"], x["home"], x["away"]))
    return historical_games, merged_2026

# ------------------------------------------------------------
# Historical prior / environment estimation
# ------------------------------------------------------------

def estimate_multi_year_prior(historical_games):
    """Build preseason priors from 2016-2025.

    We estimate attack and defense on a log run-rate scale and then shrink
    older seasons exponentially. This is deliberately much weaker than
    current-season evidence.
    """
    season_stats = defaultdict(lambda: {t: {"rs": 0.0, "ra": 0.0, "g": 0} for t in ALL_TEAMS})
    season_total_runs = defaultdict(float)
    season_games = defaultdict(int)

    for g in historical_games:
        if not is_finished(g):
            continue
        year = int(g["date"][:4])
        h, a = g["home"], g["away"]
        hs, as_ = float(g["home_score"]), float(g["away_score"])
        season_stats[year][h]["rs"] += hs
        season_stats[year][h]["ra"] += as_
        season_stats[year][h]["g"] += 1
        season_stats[year][a]["rs"] += as_
        season_stats[year][a]["ra"] += hs
        season_stats[year][a]["g"] += 1
        season_total_runs[year] += hs + as_
        season_games[year] += 1

    latest_prior_year = 2025
    weighted = {t: {"attack": 0.0, "defense": 0.0, "weight": 0.0} for t in ALL_TEAMS}
    league_log_mean_num = 0.0
    league_log_mean_den = 0.0

    for year, teams in season_stats.items():
        if year > latest_prior_year:
            continue
        if season_games[year] <= 0:
            continue
        league_rpg = season_total_runs[year] / (2.0 * season_games[year])
        if league_rpg <= 0:
            continue
        season_weight = PRIOR_SEASON_DECAY ** (latest_prior_year - year)
        league_log_mean_num += season_weight * math.log(league_rpg)
        league_log_mean_den += season_weight

        for team in ALL_TEAMS:
            gcount = teams[team]["g"]
            if gcount <= 0:
                continue
            rs_g = max(0.1, teams[team]["rs"] / gcount)
            ra_g = max(0.1, teams[team]["ra"] / gcount)
            attack = math.log(rs_g / league_rpg)
            defense = math.log(league_rpg / ra_g)
            weighted[team]["attack"] += season_weight * attack
            weighted[team]["defense"] += season_weight * defense
            weighted[team]["weight"] += season_weight

    prior_attack = {t: 0.0 for t in ALL_TEAMS}
    prior_defense = {t: 0.0 for t in ALL_TEAMS}
    for team in ALL_TEAMS:
        if weighted[team]["weight"] > 0:
            prior_attack[team] = weighted[team]["attack"] / weighted[team]["weight"]
            prior_defense[team] = weighted[team]["defense"] / weighted[team]["weight"]

    # Center the parameters so that intercept remains interpretable.
    mean_attack = sum(prior_attack.values()) / len(ALL_TEAMS)
    mean_defense = sum(prior_defense.values()) / len(ALL_TEAMS)
    prior_attack = {t: prior_attack[t] - mean_attack for t in ALL_TEAMS}
    prior_defense = {t: prior_defense[t] - mean_defense for t in ALL_TEAMS}
    prior_intercept = (
        league_log_mean_num / league_log_mean_den if league_log_mean_den > 0 else math.log(3.5)
    )

    return {
        "attack": prior_attack,
        "defense": prior_defense,
        "intercept": prior_intercept,
    }


def estimate_environment(historical_games):
    """Estimate home advantage and park run environment from 2016-2025 only."""
    home_runs = 0.0
    away_runs = 0.0
    total_runs = 0.0
    total_games = 0
    park_totals = defaultdict(float)
    park_games = defaultdict(int)

    for g in historical_games:
        if not is_finished(g):
            continue
        h, a = g["home"], g["away"]
        hs, as_ = float(g["home_score"]), float(g["away_score"])
        park = STADIUM_NAMES.get(h, "東京D")
        home_runs += hs
        away_runs += as_
        total_runs += hs + as_
        total_games += 1
        park_totals[park] += hs + as_
        park_games[park] += 1

    if total_games <= 0:
        return {"home_adv_log": 0.08, "park_log": {s: 0.0 for s in set(STADIUM_NAMES.values())}, "league_rpg": 3.5}

    league_rpg = total_runs / (2.0 * total_games)
    home_adv_log = math.log(max(0.5, home_runs) / max(0.5, away_runs))
    # Heavy shrinkage because park and home effects are partially confounded.
    home_adv_log *= 0.70

    park_log = {}
    for stadium in set(STADIUM_NAMES.values()):
        n = park_games.get(stadium, 0)
        if n <= 0:
            park_log[stadium] = 0.0
            continue
        park_rpg = park_totals[stadium] / (2.0 * n)
        raw = math.log(max(0.75, park_rpg) / max(0.75, league_rpg))
        shrink = n / (n + 180.0)
        park_log[stadium] = raw * shrink

    mean_park = sum(park_log.values()) / max(1, len(park_log))
    park_log = {k: v - mean_park for k, v in park_log.items()}

    return {
        "home_adv_log": max(-0.05, min(0.20, home_adv_log)),
        "park_log": park_log,
        "league_rpg": league_rpg,
    }

def estimate_historical_draw_rate(historical_games):
    """Descriptive draw baseline from completed 2016-2025 games.

    The prediction model does NOT use this as a fixed draw probability.
    It is reported only as a reference metric for users.
    """
    total = 0
    draws = 0
    for g in historical_games:
        if not is_finished(g):
            continue
        total += 1
        if int(g["home_score"]) == int(g["away_score"]):
            draws += 1
    return (draws / total * 100.0) if total else None


# ------------------------------------------------------------
# Rest effect estimation
# ------------------------------------------------------------

def _build_rest_samples(games):
    previous = {}
    samples = []
    sorted_games = sorted([g for g in games if is_finished(g)], key=lambda x: (x["date"], x["home"], x["away"]))
    for g in sorted_games:
        h, a = g["home"], g["away"]
        h_prev = previous.get(h)
        a_prev = previous.get(a)
        if h_prev and a_prev:
            h_rest = max(0, (parse_date(g["date"]) - parse_date(h_prev)).days - 1)
            a_rest = max(0, (parse_date(g["date"]) - parse_date(a_prev)).days - 1)
            x = max(-4.0, min(4.0, float(h_rest - a_rest)))
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            if hs != as_:
                samples.append((x, 1.0 if hs > as_ else 0.0))
        previous[h] = g["date"]
        previous[a] = g["date"]
    return samples


def estimate_rest_effect(historical_games):
    samples = _build_rest_samples(historical_games)
    if len(samples) < 100:
        return 0.0

    b0 = 0.10
    b1 = 0.0
    ridge = 15.0
    for _ in range(18):
        g0 = 0.0
        g1 = -ridge * b1
        h00 = -ridge * 0.000001
        h01 = 0.0
        h11 = -ridge
        for x, y in samples:
            p = sigmoid(b0 + b1 * x)
            e = y - p
            w = p * (1.0 - p)
            g0 += e
            g1 += x * e
            h00 -= w
            h01 -= x * w
            h11 -= x * x * w

        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-10:
            break
        # Newton step: H * delta = -grad
        d0 = (-g0 * h11 + h01 * g1) / det
        d1 = (h01 * g0 - h00 * g1) / det
        b0 += d0
        b1 += d1
        b1 = max(-0.12, min(0.12, b1))
        if abs(d0) + abs(d1) < 1e-7:
            break
    return b1

# ------------------------------------------------------------
# Current-season attack/defense model
# ------------------------------------------------------------

def _model_weight(game_date, target_date):
    age = max(0, (parse_date(target_date) - parse_date(game_date)).days)
    return math.exp(-math.log(2.0) * age / RECENCY_HALF_LIFE_DAYS)


def fit_run_model(games_2026, target_date, prior, environment):
    """Fit 2026 attack/defense parameters using only games before target_date."""
    completed = [g for g in games_2026 if is_finished(g) and g["date"] < target_date]

    attack = dict(prior["attack"])
    defense = dict(prior["defense"])
    intercept = prior["intercept"]

    if not completed:
        base_sd = math.sqrt(1.0 / max(1e-9, PRIOR_L2))
        return {
            "attack": attack,
            "defense": defense,
            "intercept": intercept,
            "home_adv_log": environment["home_adv_log"],
            "park_log": environment["park_log"],
            "uncertainty": {
                "attack_sd": {t: base_sd * MODEL_UNCERTAINTY_INFLATION for t in ALL_TEAMS},
                "defense_sd": {t: base_sd * MODEL_UNCERTAINTY_INFLATION for t in ALL_TEAMS},
                "intercept_sd": base_sd * 0.35 * MODEL_UNCERTAINTY_INFLATION,
            },
        }

    # Adam optimizer. Pure standard-library implementation so GitHub Actions
    # does not require numpy/scipy.
    m = {"intercept": 0.0}
    v = {"intercept": 0.0}
    for t in ALL_TEAMS:
        m[f"a:{t}"] = 0.0
        v[f"a:{t}"] = 0.0
        m[f"d:{t}"] = 0.0
        v[f"d:{t}"] = 0.0

    beta1, beta2 = 0.9, 0.999
    eps = 1e-8

    for iteration in range(1, FIT_ITERATIONS + 1):
        grad_i = 0.0
        grad_a = {t: 0.0 for t in ALL_TEAMS}
        grad_d = {t: 0.0 for t in ALL_TEAMS}

        for g in completed:
            w = _model_weight(g["date"], target_date)
            if w <= 1e-5:
                continue
            h, a = g["home"], g["away"]
            hs = float(g["home_score"])
            as_ = float(g["away_score"])
            park = environment["park_log"].get(STADIUM_NAMES.get(h, "東京D"), 0.0)

            log_h = intercept + environment["home_adv_log"] + park + attack[h] - defense[a]
            log_a = intercept + park + attack[a] - defense[h]
            lam_h = safe_exp(log_h)
            lam_a = safe_exp(log_a)

            r_h = w * (hs - lam_h)
            r_a = w * (as_ - lam_a)
            grad_i += r_h + r_a
            grad_a[h] += r_h
            grad_d[a] -= r_h
            grad_a[a] += r_a
            grad_d[h] -= r_a

        # Gaussian prior regularization toward multi-year strength.
        grad_i -= PRIOR_L2 * (intercept - prior["intercept"])
        for t in ALL_TEAMS:
            grad_a[t] -= PRIOR_L2 * (attack[t] - prior["attack"][t])
            grad_d[t] -= PRIOR_L2 * (defense[t] - prior["defense"][t])

        grads = {"intercept": grad_i}
        for t in ALL_TEAMS:
            grads[f"a:{t}"] = grad_a[t]
            grads[f"d:{t}"] = grad_d[t]

        for key, gval in grads.items():
            m[key] = beta1 * m[key] + (1.0 - beta1) * gval
            v[key] = beta2 * v[key] + (1.0 - beta2) * (gval * gval)
            mhat = m[key] / (1.0 - beta1 ** iteration)
            vhat = v[key] / (1.0 - beta2 ** iteration)
            step = FIT_LEARNING_RATE * mhat / (math.sqrt(vhat) + eps)
            if key == "intercept":
                intercept += step
            elif key.startswith("a:"):
                attack[key[2:]] += step
            else:
                defense[key[2:]] += step

        # Identifiability constraints: mean attack = mean defense = 0.
        mean_a = sum(attack.values()) / len(ALL_TEAMS)
        for t in ALL_TEAMS:
            attack[t] -= mean_a
        intercept += mean_a

        mean_d = sum(defense.values()) / len(ALL_TEAMS)
        for t in ALL_TEAMS:
            defense[t] -= mean_d
        intercept -= mean_d

    # Diagonal observed-information approximation for parameter uncertainty.
    # Covariances are deliberately ignored here; the public range is a
    # sensitivity band, not a formal Bayesian credible interval.
    attack_info = {t: PRIOR_L2 for t in ALL_TEAMS}
    defense_info = {t: PRIOR_L2 for t in ALL_TEAMS}
    intercept_info = PRIOR_L2
    for g in completed:
        w = _model_weight(g["date"], target_date)
        h, a = g["home"], g["away"]
        park = environment["park_log"].get(STADIUM_NAMES.get(h, "東京D"), 0.0)
        log_h = intercept + environment["home_adv_log"] + park + attack[h] - defense[a]
        log_a = intercept + park + attack[a] - defense[h]
        lam_h = safe_exp(log_h)
        lam_a = safe_exp(log_a)
        attack_info[h] += w * lam_h
        defense_info[a] += w * lam_h
        attack_info[a] += w * lam_a
        defense_info[h] += w * lam_a
        intercept_info += w * (lam_h + lam_a)

    uncertainty = {
        "attack_sd": {
            t: MODEL_UNCERTAINTY_INFLATION / math.sqrt(max(1e-9, attack_info[t]))
            for t in ALL_TEAMS
        },
        "defense_sd": {
            t: MODEL_UNCERTAINTY_INFLATION / math.sqrt(max(1e-9, defense_info[t]))
            for t in ALL_TEAMS
        },
        "intercept_sd": MODEL_UNCERTAINTY_INFLATION / math.sqrt(max(1e-9, intercept_info)),
    }

    return {
        "attack": attack,
        "defense": defense,
        "intercept": intercept,
        "home_adv_log": environment["home_adv_log"],
        "park_log": environment["park_log"],
        "uncertainty": uncertainty,
    }

# ------------------------------------------------------------
# Pitcher adjustment from confirmed starter observations only
# ------------------------------------------------------------

def build_pitcher_start_stats(games_2026, target_date):
    stats = {}
    for g in games_2026:
        if not is_finished(g) or g["date"] >= target_date:
            continue
        if not g.get("starter_confirmed"):
            continue
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        for side, team_score, opp_score in (
            ("home", hs, as_),
            ("away", as_, hs),
        ):
            pitcher = (g.get(f"{side}_starter") or "").strip()
            if not pitcher or pitcher == "未定":
                continue
            st = stats.setdefault(pitcher, {"starts": 0, "wins": 0, "losses": 0, "margin": 0.0})
            st["starts"] += 1
            st["margin"] += team_score - opp_score
            if team_score > opp_score:
                st["wins"] += 1
            elif team_score < opp_score:
                st["losses"] += 1
    return stats


def get_pitcher_run_effect(pitcher_name, pitcher_stats):
    if not pitcher_name or pitcher_name in ("未定", "未確認"):
        return 0.0
    st = pitcher_stats.get(pitcher_name)
    if not st or st["starts"] <= 0:
        return 0.0

    starts = st["starts"]
    # Very strong shrinkage because starter results are contaminated by team
    # offense/defense and because current manual starter history is sparse.
    shrunk_win = (st["wins"] + 0.5 * PITCHER_PRIOR_STARTS) / (starts + PITCHER_PRIOR_STARTS)
    effect = (shrunk_win - 0.5) * 0.30
    return max(-MAX_PITCHER_LOG_RUN_EFFECT, min(MAX_PITCHER_LOG_RUN_EFFECT, effect))

# ------------------------------------------------------------
# Rest calculation and three-way score probabilities
# ------------------------------------------------------------

def build_last_known_game_dates(games, cutoff_date=None, include_scheduled=False):
    latest = {}
    for g in sorted(games, key=lambda x: (x["date"], x["home"], x["away"])):
        if cutoff_date is not None and g["date"] >= cutoff_date:
            continue
        if is_cancelled(g):
            continue
        if is_finished(g) or (include_scheduled and g.get("status") == "scheduled"):
            latest[g["home"]] = g["date"]
            latest[g["away"]] = g["date"]
    return latest


def rest_difference_for_game(game, all_games, as_of_date=None):
    target = game["date"]
    last_dates = build_last_known_game_dates(
        all_games,
        cutoff_date=target if as_of_date is None else as_of_date,
        include_scheduled=(as_of_date is None),
    )
    h_prev = last_dates.get(game["home"])
    a_prev = last_dates.get(game["away"])
    if not h_prev or not a_prev:
        return 0.0
    h_rest = max(0, (parse_date(target) - parse_date(h_prev)).days - 1)
    a_rest = max(0, (parse_date(target) - parse_date(a_prev)).days - 1)
    return float(max(-4, min(4, h_rest - a_rest)))


def poisson_pmf(k, lam):
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1.0))


def three_way_from_scores(lam_home, lam_away):
    ph = pa = pd = 0.0
    home_pmf = [poisson_pmf(k, lam_home) for k in range(MAX_RUNS + 1)]
    away_pmf = [poisson_pmf(k, lam_away) for k in range(MAX_RUNS + 1)]
    for h, hp in enumerate(home_pmf):
        for a, ap in enumerate(away_pmf):
            p = hp * ap
            if h > a:
                ph += p
            elif h < a:
                pa += p
            else:
                pd += p
    total = ph + pd + pa
    if total <= 0:
        return 0.5, 0.0, 0.5
    return ph / total, pd / total, pa / total


def apply_conditional_logit_adjustment(p_home, p_draw, p_away, log_odds_adjust):
    decision_mass = max(1e-9, p_home + p_away)
    cond_home = p_home / decision_mass
    new_cond_home = sigmoid(logit(cond_home) + log_odds_adjust)
    new_home = decision_mass * new_cond_home
    new_away = decision_mass * (1.0 - new_cond_home)
    return new_home, p_draw, new_away


def predict_game(model, home, away, stadium, home_starter, away_starter, pitcher_stats, rest_diff, rest_effect):
    park = model["park_log"].get(stadium, 0.0)
    home_log = model["intercept"] + model["home_adv_log"] + park + model["attack"][home] - model["defense"][away]
    away_log = model["intercept"] + park + model["attack"][away] - model["defense"][home]

    # A good home starter suppresses the away team's scoring; a good away
    # starter suppresses the home team's scoring.
    home_log -= get_pitcher_run_effect(away_starter, pitcher_stats)
    away_log -= get_pitcher_run_effect(home_starter, pitcher_stats)

    lam_home = safe_exp(home_log)
    lam_away = safe_exp(away_log)
    p_home, p_draw, p_away = three_way_from_scores(lam_home, lam_away)

    # Data-derived rest adjustment, applied only to the decided-game odds so
    # that draw probability remains tied to the score distribution.
    if rest_effect and abs(rest_diff) > 0:
        p_home, p_draw, p_away = apply_conditional_logit_adjustment(
            p_home, p_draw, p_away, rest_effect * rest_diff
        )

    return {
        "home": p_home,
        "draw": p_draw,
        "away": p_away,
        "lambda_home": lam_home,
        "lambda_away": lam_away,
    }

# ------------------------------------------------------------
# Standings / H2H / CN
# ------------------------------------------------------------

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

    for x in range(0, rem_a + 1):
        a_losses = rem_a - x
        forced_b_losses = max(0, rem_h2h - a_losses)
        b_max_win = b_w + (rem_b - forced_b_losses)
        b_max_lose = b_l + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)
        a_rate = calc_win_rate(a_w + x, a_l + a_losses)
        if a_rate > b_max_rate:
            return "確定" if x == 0 else x

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

# ------------------------------------------------------------
# Monte Carlo
# ------------------------------------------------------------

def build_future_probabilities(league_teams, remaining_matches, model, pitcher_stats, rest_effect, all_games):
    result = []
    for match in sorted([m for m in remaining_matches if m.get("status") == "scheduled"], key=lambda x: (x["date"], x["home"], x["away"])):
        h, a = match["home"], match["away"]
        if h not in league_teams or a not in league_teams:
            continue
        stadium = STADIUM_NAMES.get(h, "東京D")
        h_start = match.get("home_starter") if match.get("starter_confirmed") else "未定"
        a_start = match.get("away_starter") if match.get("starter_confirmed") else "未定"
        h_start = h_start or "未定"
        a_start = a_start or "未定"
        # For future games, use the current schedule to determine expected rest.
        rest_diff = rest_difference_for_game(match, all_games, as_of_date=None)
        probs = predict_game(model, h, a, stadium, h_start, a_start, pitcher_stats, rest_diff, rest_effect)
        result.append({
            "match": match,
            "p_home": probs["home"],
            "p_draw": probs["draw"],
            "p_away": probs["away"],
        })
    return result


def determine_clinched(leader, teams, sim_w, sim_l, remaining_after_date):
    # Conservative, win-percentage-consistent clinch test.
    leader_min = calc_win_rate(sim_w[leader], sim_l[leader] + remaining_after_date.get(leader, 0))
    for team in teams:
        if team == leader:
            continue
        opp_max = calc_win_rate(sim_w[team] + remaining_after_date.get(team, 0), sim_l[team])
        if opp_max >= leader_min:
            return False
    return True


def simulate_full_season_probabilities(league_teams, current_standings, remaining_matches, model, pitcher_stats, rest_effect, all_games, num_sims=MAIN_NUM_SIMS, rng=None):
    rank_counts = {t: {r: 0 for r in range(1, 7)} for t in league_teams}
    clinch_date_counts = {t: {} for t in league_teams}
    base_wins = {t["team"]: t["win"] for t in current_standings}
    base_losses = {t["team"]: t["lose"] for t in current_standings}

    future_probs = build_future_probabilities(league_teams, remaining_matches, model, pitcher_stats, rest_effect, all_games)
    rng = rng or random
    matches_by_date = defaultdict(list)
    for fp in future_probs:
        matches_by_date[fp["match"]["date"]].append(fp)
    sorted_dates = sorted(matches_by_date.keys())

    # Pre-compute the number of scheduled games strictly after each date.
    future_after = {d: {t: 0 for t in league_teams} for d in sorted_dates}
    remaining_counts = {t: 0 for t in league_teams}
    total_future = {t: 0 for t in league_teams}
    for fp in future_probs:
        h = fp["match"]["home"]
        a = fp["match"]["away"]
        total_future[h] += 1
        total_future[a] += 1
    remaining_counts = dict(total_future)
    for d in sorted_dates:
        # Remove today's games first; the remainder is strictly after d.
        for fp in matches_by_date[d]:
            h = fp["match"]["home"]
            a = fp["match"]["away"]
            remaining_counts[h] -= 1
            remaining_counts[a] -= 1
        future_after[d] = dict(remaining_counts)

    for _ in range(num_sims):
        sim_w = dict(base_wins)
        sim_l = dict(base_losses)
        clinched_day = {t: None for t in league_teams}

        for d in sorted_dates:
            for fp in matches_by_date[d]:
                h = fp["match"]["home"]
                a = fp["match"]["away"]
                rnd = rng.random()
                if rnd < fp["p_draw"]:
                    continue
                if rng.random() < (fp["p_home"] / max(1e-9, fp["p_home"] + fp["p_away"])):
                    sim_w[h] += 1
                    sim_l[a] += 1
                else:
                    sim_w[a] += 1
                    sim_l[h] += 1

            sim_rates = sorted(
                [(t, calc_win_rate(sim_w[t], sim_l[t]), sim_w[t]) for t in league_teams],
                key=lambda x: (x[1], x[2]),
                reverse=True,
            )
            leader = sim_rates[0][0]
            remaining_after = dict(future_after.get(d, {t: 0 for t in league_teams}))
            if determine_clinched(leader, league_teams, sim_w, sim_l, remaining_after) and clinched_day[leader] is None:
                clinched_day[leader] = d

        final_rates = sorted(
            [(t, calc_win_rate(sim_w[t], sim_l[t]), sim_w[t]) for t in league_teams],
            key=lambda x: (x[1], x[2]),
            reverse=True,
        )
        champ = final_rates[0][0]
        if clinched_day[champ] is not None:
            clinch_date_counts[champ][clinched_day[champ]] = clinch_date_counts[champ].get(clinched_day[champ], 0) + 1
        for idx, item in enumerate(final_rates):
            rank_counts[item[0]][idx + 1] += 1

    champ_raw = {t: rank_counts[t][1] / num_sims * 100.0 for t in league_teams}
    champ_norm = normalize_probabilities_to_100(champ_raw)

    final_rank_matrix = {}
    for t in league_teams:
        final_rank_matrix[t] = {1: champ_norm[t]}
        for r in range(2, 7):
            final_rank_matrix[t][r] = int(round(rank_counts[t][r] / num_sims * 100.0))

    # Keep the matrix internally coherent for UI percentages.
    for t in league_teams:
        total = sum(final_rank_matrix[t].values())
        if total != 100:
            # distribute the residual to the modal rank
            modal_rank = max(final_rank_matrix[t], key=final_rank_matrix[t].get)
            final_rank_matrix[t][modal_rank] += 100 - total

    clinch_date_probs = {}
    for t in league_teams:
        clinch_date_probs[t] = {d: (count / num_sims) * 100.0 for d, count in clinch_date_counts[t].items()}

    return final_rank_matrix, clinch_date_probs

def sample_model_variant(model, rng):
    """Sample an approximate plausible model state for the uncertainty band."""
    variant = {
        "attack": {},
        "defense": {},
        "intercept": model["intercept"],
        "home_adv_log": model["home_adv_log"],
        "park_log": dict(model["park_log"]),
    }
    unc = model.get("uncertainty", {})
    a_sd = unc.get("attack_sd", {})
    d_sd = unc.get("defense_sd", {})

    for t in ALL_TEAMS:
        variant["attack"][t] = model["attack"][t] + rng.gauss(0.0, float(a_sd.get(t, 0.0)))
        variant["defense"][t] = model["defense"][t] + rng.gauss(0.0, float(d_sd.get(t, 0.0)))

    mean_a = sum(variant["attack"].values()) / len(ALL_TEAMS)
    mean_d = sum(variant["defense"].values()) / len(ALL_TEAMS)
    for t in ALL_TEAMS:
        variant["attack"][t] -= mean_a
        variant["defense"][t] -= mean_d
    variant["intercept"] += mean_a - mean_d
    return variant


def simulate_championship_probability_band(
    league_teams,
    current_standings,
    remaining_matches,
    model,
    pitcher_stats,
    rest_effect,
    all_games,
    seed_offset=0,
):
    """Compute a model-uncertainty sensitivity band for championship probability."""
    samples = {t: [] for t in league_teams}
    future_matches = [m for m in remaining_matches if m.get("status") == "scheduled"]
    if not future_matches:
        return {t: None for t in league_teams}

    base_seed = RANDOM_SEED + int(seed_offset) + len(league_teams) * 1000
    for s in range(UNCERTAINTY_MODEL_SIMS):
        rng = random.Random(base_seed + s * 7919)
        variant = sample_model_variant(model, rng)
        mat, _ = simulate_full_season_probabilities(
            league_teams,
            current_standings,
            future_matches,
            variant,
            pitcher_stats,
            rest_effect,
            all_games,
            UNCERTAINTY_SEASON_SIMS,
            rng=rng,
        )
        for t in league_teams:
            samples[t].append(float(mat[t][1]))

    result = {}
    for t in league_teams:
        vals = sorted(samples[t])
        if not vals:
            result[t] = None
            continue
        low_idx = max(0, min(len(vals) - 1, int(math.floor((len(vals) - 1) * UNCERTAINTY_LOW_Q))))
        high_idx = max(0, min(len(vals) - 1, int(math.ceil((len(vals) - 1) * UNCERTAINTY_HIGH_Q))))
        result[t] = {"low": vals[low_idx], "high": vals[high_idx], "samples": len(vals)}
    return result


def self_clinchable_first_count(table):
    """Number of teams that can still secure 1st place by own wins alone."""
    count = 0
    for t in table:
        v = t.get("magic_1st")
        rem = int(t.get("remaining", 0))
        if v == "確定":
            count += 1
        elif isinstance(v, int) and v <= rem:
            count += 1
    return count


# ------------------------------------------------------------
# Presentation helpers
# ------------------------------------------------------------

def build_aligned_championship_grid(top_teams_standings):
    teams_data = []
    for t in top_teams_standings[:3]:
        rem = t["remaining"]
        cur_w = t["win"]
        cur_l = t["lose"]
        patterns = []
        for w in range(rem, -1, -1):
            l = rem - w
            rate = calc_win_rate(cur_w + w, cur_l + l)
            patterns.append({
                "w": w,
                "l": l,
                "rate": round(rate, 3),
                "rate_str": f".{round(rate * 1000):03d}",
            })
        teams_data.append({
            "team": t["team"],
            "remaining": rem,
            "current_w": cur_w,
            "current_l": cur_l,
            "patterns": patterns,
        })

    if not teams_data:
        return {"headers": [], "rows": []}

    base_patterns = teams_data[0]["patterns"]
    num_rows = len(base_patterns)
    aligned_rows = [[p] for p in base_patterns]

    for td in teams_data[1:]:
        pats = td["patterns"]
        t_max_rate = pats[0]["rate"] if pats else 0.0
        best_start = min(range(num_rows), key=lambda r: abs(base_patterns[r]["rate"] - t_max_rate))
        assigned = [None] * num_rows
        for p_idx, p in enumerate(pats):
            row_pos = best_start + p_idx
            if row_pos < num_rows:
                assigned[row_pos] = p
        for r_idx in range(num_rows):
            aligned_rows[r_idx].append(assigned[r_idx])

    for row in aligned_rows:
        while len(row) < len(teams_data):
            row.append(None)

    return {
        "headers": [
            {
                "team": td["team"],
                "remaining": td["remaining"],
                "current_w": td["current_w"],
                "current_l": td["current_l"],
            }
            for td in teams_data
        ],
        "rows": aligned_rows,
    }


def format_league(records, league_teams, h2h_played, h2h_details):
    table = []
    for team in league_teams:
        r = records[team]
        r["remaining"] = TOTAL_GAMES - r["games"]
        r["rate"] = calc_win_rate(r["win"], r["lose"])
        r["h2h"] = {opp: h2h_details[team][opp] for opp in league_teams}
        table.append(r)

    table.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
    top_w, top_l = table[0]["win"], table[0]["lose"]
    for idx, t in enumerate(table):
        t["rank"] = idx + 1
        diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
        t["diff"] = max(0.0, diff) if idx > 0 else 0.0

    magic_names = {1: "magic_1st", 2: "magic_2nd", 3: "magic_3rd", 4: "magic_4th", 5: "magic_5th"}
    for t in table:
        for rank in range(1, 6):
            t[magic_names[rank]] = evaluate_clinch_target(t, rank, table, h2h_played)
    return validate_and_assert_standings(table)

# ------------------------------------------------------------
# Full history build
# ------------------------------------------------------------

def build_all_history_with_predictions(historical_games, games_2026):
    prior = estimate_multi_year_prior(historical_games)
    environment = estimate_environment(historical_games)
    rest_effect = estimate_rest_effect(historical_games)
    draw_baseline_rate = estimate_historical_draw_rate(historical_games)

    all_dates = sorted({g["date"] for g in games_2026})
    history_snapshots = {}
    random.seed(RANDOM_SEED)

    for target_date in all_dates:
        records = {
            t: {
                "team": t,
                "games": 0,
                "win": 0,
                "lose": 0,
                "draw": 0,
                "rs": 0,
                "ra": 0,
                "home": {"win": 0, "lose": 0, "draw": 0},
                "away": {"win": 0, "lose": 0, "draw": 0},
                "interleague": {"win": 0, "lose": 0, "draw": 0},
            }
            for t in ALL_TEAMS
        }
        team_total_stats_before_today = {t: {"rs": 0, "ra": 0, "games": 0} for t in ALL_TEAMS}
        h2h_played = {t1: {t2: 0 for t2 in ALL_TEAMS} for t1 in ALL_TEAMS}
        h2h_details = {
            t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in ALL_TEAMS}
            for t1 in ALL_TEAMS
        }

        # Strictly pre-target-date information for the prediction model.
        pitcher_stats = build_pitcher_start_stats(games_2026, target_date)
        model = fit_run_model(games_2026, target_date, prior, environment)

        for g in games_2026:
            if not is_finished(g):
                continue
            h, a = g["home"], g["away"]
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            g_date = g["date"]

            # Pre-game model totals: strict < target_date only.
            if g_date < target_date:
                team_total_stats_before_today[h]["rs"] += hs
                team_total_stats_before_today[h]["ra"] += as_
                team_total_stats_before_today[h]["games"] += 1
                team_total_stats_before_today[a]["rs"] += as_
                team_total_stats_before_today[a]["ra"] += hs
                team_total_stats_before_today[a]["games"] += 1

            # Standings through target_date.
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

        c_table = format_league(records, CENTRAL_TEAMS, h2h_played, h2h_details)
        p_table = format_league(records, PACIFIC_TEAMS, h2h_played, h2h_details)

        day_predictions = []
        processed_pairs = set()
        for g in reversed(games_2026):
            if g["date"] != target_date:
                continue
            h, a = g["home"], g["away"]
            pair_key = (h, a)
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)
            if is_cancelled(g):
                continue

            stadium = STADIUM_NAMES.get(h, "東京D")
            h_start = g.get("home_starter") if g.get("starter_confirmed") else "未定"
            a_start = g.get("away_starter") if g.get("starter_confirmed") else "未定"
            h_start = h_start or "未定"
            a_start = a_start or "未定"
            rest_diff = rest_difference_for_game(g, games_2026, as_of_date=target_date)
            probs = predict_game(model, h, a, stadium, h_start, a_start, pitcher_stats, rest_diff, rest_effect)

            hs, as_ = g.get("home_score"), g.get("away_score")
            finished = is_finished(g)
            if finished:
                hs_int, as_int = int(hs), int(as_)
                if hs_int > as_int:
                    h_label = f"勝利: {h_start}" if h_start not in ("未定", "未確認") else "勝利"
                    a_label = f"敗戦: {a_start}" if a_start not in ("未定", "未確認") else "敗戦"
                elif hs_int < as_int:
                    h_label = f"敗戦: {h_start}" if h_start not in ("未定", "未確認") else "敗戦"
                    a_label = f"勝利: {a_start}" if a_start not in ("未定", "未確認") else "勝利"
                else:
                    h_label = f"引分: {h_start}" if h_start not in ("未定", "未確認") else "引分"
                    a_label = f"引分: {a_start}" if a_start not in ("未定", "未確認") else "引分"
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
                "home_prob": round(probs["home"] * 100.0, 1),
                "away_prob": round(probs["away"] * 100.0, 1),
                "draw_prob": round(probs["draw"] * 100.0, 1),
                "expected_home_runs": round(probs["lambda_home"], 2),
                "expected_away_runs": round(probs["lambda_away"], 2),
                "actual_home_score": int(hs) if finished else None,
                "actual_away_score": int(as_) if finished else None,
                "is_finished": finished,
            })

        day_predictions.reverse()

        c_self_clinchable = self_clinchable_first_count(c_table)
        p_self_clinchable = self_clinchable_first_count(p_table)

        c_band = None
        p_band = None
        if c_self_clinchable != 1:
            c_future_d = [
                g for g in games_2026
                if g["date"] > target_date
                and g["home"] in CENTRAL_TEAMS and g["away"] in CENTRAL_TEAMS
                and g.get("home_score") is None and g.get("away_score") is None
                and not is_cancelled(g)
            ]
            if c_future_d:
                c_band = simulate_championship_probability_band(
                    CENTRAL_TEAMS, c_table, c_future_d, model, pitcher_stats, rest_effect,
                    games_2026, seed_offset=int(target_date[5:7] + target_date[8:10])
                )

        if p_self_clinchable != 1:
            p_future_d = [
                g for g in games_2026
                if g["date"] > target_date
                and g["home"] in PACIFIC_TEAMS and g["away"] in PACIFIC_TEAMS
                and g.get("home_score") is None and g.get("away_score") is None
                and not is_cancelled(g)
            ]
            if p_future_d:
                p_band = simulate_championship_probability_band(
                    PACIFIC_TEAMS, p_table, p_future_d, model, pitcher_stats, rest_effect,
                    games_2026, seed_offset=int(target_date[5:7] + target_date[8:10]) + 500
                )

        for t in c_table:
            info = c_band.get(t["team"]) if c_band else None
            if info:
                t["champ_prob_low"] = round(info["low"], 1)
                t["champ_prob_high"] = round(info["high"], 1)
                t["champ_prob_band_n"] = info["samples"]

        for t in p_table:
            info = p_band.get(t["team"]) if p_band else None
            if info:
                t["champ_prob_low"] = round(info["low"], 1)
                t["champ_prob_high"] = round(info["high"], 1)
                t["champ_prob_band_n"] = info["samples"]

        history_snapshots[target_date] = {
            "central": c_table,
            "pacific": p_table,
            "predictions": day_predictions,
        }

        # Store per-date fitted model privately for historical probability pass.
        history_snapshots[target_date]["_model"] = model
        history_snapshots[target_date]["_pitcher_stats"] = pitcher_stats

    # Latest evaluation point.
    dates_with_finished = [
        d for d in all_dates if any(g["date"] == d and is_finished(g) for g in games_2026)
    ]
    last_eval_date = dates_with_finished[-1] if dates_with_finished else all_dates[0]
    latest_snapshot = history_snapshots[last_eval_date]
    latest_model = latest_snapshot["_model"]
    latest_pitcher_stats = latest_snapshot["_pitcher_stats"]

    # Current/future matches = games without results and not cancelled.
    future_matches = [
        g for g in games_2026
        if g.get("home_score") is None and g.get("away_score") is None and not is_cancelled(g)
    ]
    c_future = [g for g in future_matches if g["home"] in CENTRAL_TEAMS and g["away"] in CENTRAL_TEAMS]
    p_future = [g for g in future_matches if g["home"] in PACIFIC_TEAMS and g["away"] in PACIFIC_TEAMS]
    # Only league games remain in each league's standings simulation; interleague
    # should not exist after the regular interleague phase in this dataset, but
    # filtering here is safer if the source contains odd records.

    c_rank_matrix, c_clinch_dates = simulate_full_season_probabilities(
        CENTRAL_TEAMS,
        latest_snapshot["central"],
        c_future,
        latest_model,
        latest_pitcher_stats,
        rest_effect,
        games_2026,
        MAIN_NUM_SIMS,
    )
    p_rank_matrix, p_clinch_dates = simulate_full_season_probabilities(
        PACIFIC_TEAMS,
        latest_snapshot["pacific"],
        p_future,
        latest_model,
        latest_pitcher_stats,
        rest_effect,
        games_2026,
        MAIN_NUM_SIMS,
    )

    # Historical as-of-date champion/CS probabilities.
    # These are now genuine model-based probabilities rather than a heuristic.
    for d in all_dates:
        snap = history_snapshots[d]
        model_d = snap["_model"]
        pitcher_d = snap["_pitcher_stats"]
        # Historical simulation must treat ALL games after d as future, even
        # if those games have since been completed in the live database.
        # Do not leak future results or future starter announcements.
        future_d = []
        for g in games_2026:
            if g["date"] <= d or is_cancelled(g):
                continue
            future_copy = dict(g)
            future_copy["home_score"] = None
            future_copy["away_score"] = None
            future_copy["status"] = "scheduled"
            future_copy["starter_confirmed"] = False
            future_copy["home_starter"] = "未定"
            future_copy["away_starter"] = "未定"
            future_d.append(future_copy)
        c_future_d = [g for g in future_d if g["home"] in CENTRAL_TEAMS and g["away"] in CENTRAL_TEAMS]
        p_future_d = [g for g in future_d if g["home"] in PACIFIC_TEAMS and g["away"] in PACIFIC_TEAMS]

        if d == last_eval_date:
            c_mat, _ = c_rank_matrix, c_clinch_dates
            p_mat, _ = p_rank_matrix, p_clinch_dates
        else:
            c_mat, _ = simulate_full_season_probabilities(
                CENTRAL_TEAMS,
                snap["central"],
                c_future_d,
                model_d,
                pitcher_d,
                rest_effect,
                games_2026,
                HISTORICAL_NUM_SIMS,
            )
            p_mat, _ = simulate_full_season_probabilities(
                PACIFIC_TEAMS,
                snap["pacific"],
                p_future_d,
                model_d,
                pitcher_d,
                rest_effect,
                games_2026,
                HISTORICAL_NUM_SIMS,
            )

        for t in snap["central"]:
            mat = c_mat[t["team"]]
            t["champ_prob"] = 100 if t.get("magic_1st") == "確定" else mat[1]
            t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else sum(mat[r] for r in (1, 2, 3))
        for t in snap["pacific"]:
            mat = p_mat[t["team"]]
            t["champ_prob"] = 100 if t.get("magic_1st") == "確定" else mat[1]
            t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else sum(mat[r] for r in (1, 2, 3))

        del snap["_model"]
        del snap["_pitcher_stats"]

    # Latest schedules for championship-clinch cards.
    def build_filtered_clinch_schedule(team_name, future_matches_local, clinch_date_map, champ_prob, model, pitcher_stats):
        all_future_dates = sorted(set([m["date"] for m in future_matches_local]) | set(clinch_date_map.keys()))
        rows = []
        cumulative = 0.0
        for d in all_future_dates:
            match = next(
                (m for m in future_matches_local if m["date"] == d and (m["home"] == team_name or m["away"] == team_name)),
                None,
            )
            prob_raw = clinch_date_map.get(d, 0.0)
            m_int, d_int = int(d[5:7]), int(d[8:10])
            is_tentative = (m_int == 10 and d_int >= 7)
            date_display = f"({m_int}/{d_int})" if is_tentative else f"{m_int}/{d_int}"

            if match:
                is_home = match["home"] == team_name
                opp = match["away"] if is_home else match["home"]
                host = match["home"]
                ground = STADIUM_NAMES.get(host, "球場")
                stadium = STADIUM_NAMES.get(host, "東京D")
                h_start = match.get("home_starter") if match.get("starter_confirmed") else "未定"
                a_start = match.get("away_starter") if match.get("starter_confirmed") else "未定"
                rest_diff = rest_difference_for_game(match, games_2026, as_of_date=None)
                probs = predict_game(model, match["home"], match["away"], stadium, h_start or "未定", a_start or "未定", pitcher_stats, rest_diff, rest_effect)
                win_expect = probs["home"] if is_home else probs["away"]
                win_expect_str = str(int(round(win_expect * 100.0)))
            else:
                opp = "-"
                ground = "-"
                win_expect_str = "-"

            cumulative += prob_raw
            rows.append({
                "date": date_display,
                "raw_date": d,
                "opp": opp,
                "ground": ground,
                "clinch_prob_val": prob_raw,
                "win_expect": win_expect_str,
            })

        first_idx = next((i for i, r in enumerate(rows) if r["clinch_prob_val"] > 0.001), None)
        if first_idx is not None:
            rows = rows[first_idx:]
        else:
            rows = [r for r in rows if r["opp"] != "-"][-8:]

        cum = 0.0
        for row in rows:
            val = row["clinch_prob_val"]
            cum += val
            if val < 0.001:
                row["clinch_prob_str"] = "-"
            elif val < 1.0:
                row["clinch_prob_str"] = f"{val:.1f}%" if val >= 0.1 else f"{val:.2f}%"
            else:
                row["clinch_prob_str"] = f"{int(round(val))}%"
            if cum < 0.001:
                row["cum_prob_str"] = "-"
            elif cum < 1.0:
                row["cum_prob_str"] = f"{cum:.1f}%" if cum >= 0.1 else f"{cum:.2f}%"
            else:
                row["cum_prob_str"] = f"{int(round(cum))}%"
        return rows

    latest_c = history_snapshots[last_eval_date]["central"]
    latest_p = history_snapshots[last_eval_date]["pacific"]
    c_schedules = {
        t["team"]: build_filtered_clinch_schedule(
            t["team"], c_future, c_clinch_dates.get(t["team"], {}), t["champ_prob"], latest_model, latest_pitcher_stats
        )
        for t in latest_c
    }
    p_schedules = {
        t["team"]: build_filtered_clinch_schedule(
            t["team"], p_future, p_clinch_dates.get(t["team"], {}), t["champ_prob"], latest_model, latest_pitcher_stats
        )
        for t in latest_p
    }

    simulation_payload = {
        "central_rank_matrix": c_rank_matrix,
        "pacific_rank_matrix": p_rank_matrix,
        "central_clinch_schedules": c_schedules,
        "pacific_clinch_schedules": p_schedules,
        "central_lines_grid": build_aligned_championship_grid(latest_c),
        "pacific_lines_grid": build_aligned_championship_grid(latest_p),
        "model": {
            "version": "2026-09 improved Poisson attack-defense",
            "main_simulations": MAIN_NUM_SIMS,
            "historical_simulations": HISTORICAL_NUM_SIMS,
            "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
            "draw_baseline_rate": round(draw_baseline_rate, 2) if draw_baseline_rate is not None else None,
            "draw_baseline_period": "2016-2025実績（参考値）",
            "prior_season_decay": PRIOR_SEASON_DECAY,
            "rest_effect_logit_per_day": round(rest_effect, 6),
            "environment": {
                "home_adv_log": round(environment["home_adv_log"], 6),
                "league_run_per_team_game": round(environment["league_rpg"], 4),
                "park_log": {k: round(v, 6) for k, v in environment["park_log"].items()},
            },
            "starter_policy": "confirmed manual starter information only; strong shrinkage",
            "draw_model": "score distribution derived; no fixed 4.5% assumption",
            "championship_band": {
                "method": "parameter-uncertainty scenarios + reduced Monte Carlo",
                "low_quantile": UNCERTAINTY_LOW_Q,
                "high_quantile": UNCERTAINTY_HIGH_Q,
                "model_sims": UNCERTAINTY_MODEL_SIMS,
                "season_sims_per_model": UNCERTAINTY_SEASON_SIMS,
                "trigger": "two or more teams remain self-clinchable for 1st",
            },
        },
    }

    jst_today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).strftime("%Y-%m-%d")
    final_default_date = jst_today if jst_today in all_dates else last_eval_date
    return all_dates, final_default_date, history_snapshots, simulation_payload

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    historical_games, games_2026 = load_all_games()
    dates, default_latest, history, sim_data = build_all_history_with_predictions(historical_games, games_2026)

    output = {
        "latest_date": default_latest,
        "available_dates": dates,
        "history": history,
        "simulation": sim_data,
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(
        "解析・予測更新完了："
        f"{dates[0]} ～ {dates[-1]} / "
        f"Poisson攻守モデル + 複数年prior + recency + park/home + starter + rest / "
        f"Monte Carlo {MAIN_NUM_SIMS}回"
    )


if __name__ == "__main__":
    main()
