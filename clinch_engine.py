import datetime
import json
import math
import os
import random
import re
import subprocess
from fractions import Fraction
from functools import lru_cache
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
HISTORICAL_NUM_SIMS = 100
RANDOM_SEED = 20260921

# Early/mid-season model-uncertainty band.
# This is separate from Monte Carlo sampling error. We perturb the fitted
# attack/defense parameters using a Laplace-style diagonal approximation and
# then rerun smaller season simulations. The public page uses the 20th-80th
# percentile of these scenario results while more than one team still has
# a self-clinchable path to 1st place.
UNCERTAINTY_MODEL_SIMS = 10
UNCERTAINTY_SEASON_SIMS = 60
UNCERTAINTY_LOW_Q = 0.20
UNCERTAINTY_HIGH_Q = 0.80
MODEL_UNCERTAINTY_INFLATION = 0.70

# Run model hyperparameters.
# 50日程度の半減期なら、4月の試合を9月時点で強く引きずりすぎない。
RECENCY_HALF_LIFE_DAYS = 50.0
PRIOR_SEASON_DECAY = 0.55
PRIOR_L2 = 8.0
FIT_ITERATIONS = 35
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

# ------------------------------------------------------------
# Historical championship prior / early-season shrinkage
# ------------------------------------------------------------
# These 2005-2024 final-rank histories are the user's 20-year reference
# dataset.  They are used ONLY to construct the preseason/early-season
# championship prior.  The current-game Poisson model is kept separate.
#
# For a 2026 forecast, the previous-season (2025) rank is derived from the
# actual 2025 results loaded from the master game log, while the historical
# prior itself uses the 2005-2024 20-year reference period.

HISTORICAL_RANK_YEARS = list(range(2005, 2025))

CENTRAL_HISTORICAL_RANKS = {
    "巨人":     [5,4,1,1,1,3,3,1,1,1,2,2,4,3,1,1,3,4,4,1],
    "阪神":     [1,2,3,2,4,2,4,5,2,2,3,4,2,6,3,2,2,3,1,2],
    "ＤｅＮＡ": [3,6,4,6,6,6,6,6,5,5,6,3,3,4,2,4,6,2,3,3],
    "広島":     [6,5,5,4,5,5,5,4,3,3,4,1,1,1,4,5,4,5,2,4],
    "ヤクルト": [4,3,6,5,3,4,2,3,6,6,1,5,6,2,6,6,1,1,5,5],
    "中日":     [2,1,2,3,2,1,1,2,4,4,5,6,5,5,5,3,5,6,6,6],
}

PACIFIC_HISTORICAL_RANKS = {
    "ソフトバンク": [2,3,3,6,3,1,1,3,4,1,1,2,1,2,2,1,4,2,3,1],
    "日本ハム":     [5,1,1,3,1,4,2,1,6,3,2,1,5,3,5,5,5,6,6,2],
    "ロッテ":       [1,4,2,4,5,3,6,5,3,4,3,3,6,5,4,2,2,5,2,3],
    "楽天":         [6,6,4,5,2,6,5,4,1,6,6,5,3,6,3,4,3,4,4,4],
    "オリックス":   [4,5,6,2,6,5,4,6,5,2,5,6,4,4,6,6,1,1,1,5],
    "西武":         [3,2,5,1,4,2,3,2,2,5,4,4,2,1,1,3,6,3,5,6],
}

# Smoothing strength for historical rates.  A value of 3 means that each
# rank/category receives three pseudo-observations with a league-wide title
# rate of 1/6.  This prevents small historical cells such as "0 of 19" from
# producing a literal 0% preseason prior.
HISTORICAL_PRIOR_SMOOTHING = 3.0

# Mix the two historical signals: (A) next-year champion rate conditional on
# previous rank, and (B) the team's own 20-year championship rate.
HISTORICAL_RANK_PRIOR_MIX = 0.70
HISTORICAL_TEAM_PRIOR_MIX = 0.30

# Weight given to the current-season Poisson/Monte-Carlo championship model.
# It starts low and rises smoothly as more 2026 games accumulate.
EARLY_SEASON_CURRENT_WEIGHT_MIN = 0.08
EARLY_SEASON_CURRENT_WEIGHT_MAX = 0.95
EARLY_SEASON_CURRENT_WEIGHT_SCALE = 45.0

# ------------------------------------------------------------
# Draw-probability calibration
# ------------------------------------------------------------
# The Poisson score distribution is used to determine the relative
# home-vs-away strength, but its raw same-score probability is NOT used as
# the NPB draw probability. At realistic NPB run environments, independent
# Poisson scoring can imply a draw rate well above the historical league rate.
# Instead, start from an empirical NPB baseline of about 4.5% and shrink it
# gradually toward the observed 2026 draw rate as more games are completed.
# This keeps early-season draw probabilities near the historical baseline and
# allows them to converge toward the current-season level (around 2.2% in the
# present 2026 data) without becoming unstable.
DRAW_PRIOR_RATE = 0.045
DRAW_PRIOR_EFFECTIVE_GAMES = 40.0
DRAW_RATE_MIN = 0.020
DRAW_RATE_MAX = 0.060

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



@lru_cache(maxsize=8)
def _load_git_file_timeline(path):
    """起動時に指定ファイルの全コミット履歴と内容を一度に取得して辞書化する"""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%H%x09%cI", "--", path],
            check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError):
        return tuple()

    timeline = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        sha, iso_ts = line.split("\t", 1)
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
            timeline.append((sha, ts))
        except ValueError:
            continue
    return tuple(timeline)


@lru_cache(maxsize=512)
def _get_git_blob_content(commit_sha, path):
    try:
        proc = subprocess.run(
            ["git", "show", f"{commit_sha}:{path}"],
            check=True, capture_output=True, text=True, encoding="utf-8"
        )
        return proc.stdout
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return None


@lru_cache(maxsize=512)
def read_tracked_file_as_of_date(path, target_date):
    """対象日の23:59:59時点のファイル内容をキャッシュを活用して素早く取得する"""
    cutoff = datetime.datetime.fromisoformat(f"{target_date}T23:59:59+09:00")
    timeline = _load_git_file_timeline(path)
    for commit_sha, commit_ts in timeline:
        if commit_ts <= cutoff:
            return _get_git_blob_content(commit_sha, path)
    return None


def _parse_manual_games_text(raw_text):
    if not raw_text or not raw_text.strip():
        return []
    try:
        payload = json.loads(raw_text)
    except Exception:
        return []
    manual_games = payload if isinstance(payload, list) else payload.get("games", [])
    result = []
    for mg in manual_games:
        entry = _normalize_manual_entry(mg)
        if entry:
            result.append(entry)
    return result


def _merge_2026_master_and_manual(games_2026_master, manual_games):
    manual_map = {
        (g["date"], g["home"], g["away"]): g
        for g in manual_games
    }
    merged = []
    applied_keys = set()
    for master in games_2026_master:
        key = (master["date"], master["home"], master["away"])
        if key in manual_map:
            merged.append(manual_map[key])
            applied_keys.add(key)
        else:
            merged.append(master)
    for key, manual in manual_map.items():
        if key not in applied_keys:
            merged.append(manual)
    merged.sort(key=lambda x: (x["date"], x["home"], x["away"]))
    return merged


def augment_unresolved_postponements(games):
    """Add undated postponed games that are still missing from the schedule.

    NPB regular-season pairs have a fixed number of scheduled games in the
    site's model.  A cancelled game that has not yet been replaced by a later
    scheduled/finished game therefore remains a real future game, even though
    its date is unknown.  Keeping it out of the season-completion simulation
    would understate the number of remaining games and can falsely trigger a
    championship or final-rank clinch.

    The postponed game's original matchup/home stadium are retained, but the
    date is set to None and starter information is cleared.  The UI can then
    display these games as "日程未定" without inventing a date.
    """
    base = [dict(g) for g in games]
    existing_undated = {
        (
            g.get("home"),
            g.get("away"),
            g.get("original_date"),
        )
        for g in base
        if g.get("undated_postponed")
    }

    def pair_key(home, away):
        return tuple(sorted((home, away)))

    by_pair = defaultdict(list)
    for g in base:
        if g.get("home") in ALL_TEAMS and g.get("away") in ALL_TEAMS:
            by_pair[pair_key(g["home"], g["away"])].append(g)

    for pair, pair_games in by_pair.items():
        t1, t2 = pair
        is_intra = (
            (t1 in CENTRAL_TEAMS and t2 in CENTRAL_TEAMS)
            or (t1 in PACIFIC_TEAMS and t2 in PACIFIC_TEAMS)
        )
        required = GAMES_INTRA if is_intra else GAMES_INTER

        played_or_scheduled = [
            g for g in pair_games
            if is_finished(g) or g.get("status") == "scheduled"
        ]
        missing = max(0, required - len(played_or_scheduled))
        if missing <= 0:
            continue

        cancelled = sorted(
            [g for g in pair_games if is_cancelled(g)],
            key=lambda x: x.get("date") or "",
        )

        # Use the unresolved cancellations as the source of the missing
        # matchups.  In the current 2026 data this identifies the two
        # postponed 阪神-広島 games whose make-up dates are still unknown.
        for template in cancelled:
            if missing <= 0:
                break
            marker = (
                template.get("home"),
                template.get("away"),
                template.get("date"),
            )
            if marker in existing_undated:
                continue

            future = dict(template)
            future["original_date"] = template.get("date")
            future["date"] = None
            future["status"] = "scheduled"
            future["home_score"] = None
            future["away_score"] = None
            future["home_starter"] = "未定"
            future["away_starter"] = "未定"
            future["starter_confirmed"] = False
            future["home_pitcher"] = ""
            future["away_pitcher"] = ""
            future["source"] = "postponed-undated"
            future["undated_postponed"] = True
            base.append(future)
            existing_undated.add(marker)
            missing -= 1

    return base


@lru_cache(maxsize=512)
def load_2026_games_as_of_date(target_date):
    """Return 2026 games from Git-tracked inputs as of target_date.

    This is used only for historical snapshots.  When Git history is not
    available, it returns None and the caller falls back to the current input.
    """
    master_text = read_tracked_file_as_of_date(TEXT_LOG_FILE, target_date)
    if master_text is None:
        return None
    master_games = parse_year_games_from_text(master_text, 2026)

    db_text = read_tracked_file_as_of_date(MANUAL_DB_FILE, target_date)
    manual_games = _parse_manual_games_text(db_text)
    merged = _merge_2026_master_and_manual(master_games, manual_games)
    return augment_unresolved_postponements(merged)


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

    manual_games = []
    if os.path.exists(MANUAL_DB_FILE):
        try:
            with open(MANUAL_DB_FILE, "r", encoding="utf-8") as f:
                manual_games = _parse_manual_games_text(f.read())
        except Exception as exc:
            print(f"games_db.json 読込警告: {exc}")

    merged_2026 = _merge_2026_master_and_manual(games_2026_master, manual_games)
    merged_2026 = augment_unresolved_postponements(merged_2026)
    historical_games.sort(key=lambda x: (x["date"], x["home"], x["away"]))
    return historical_games, merged_2026

def _smooth_binomial_rate(successes, trials, prior_strength=HISTORICAL_PRIOR_SMOOTHING):
    """Empirical-Bayes smoothing toward the six-team baseline of 1/6."""
    prior_mean = 1.0 / 6.0
    return (float(successes) + prior_strength * prior_mean) / (float(trials) + prior_strength) if (trials + prior_strength) > 0 else prior_mean


def _league_historical_rank_map(league_teams):
    if set(league_teams) == set(CENTRAL_TEAMS):
        return CENTRAL_HISTORICAL_RANKS
    if set(league_teams) == set(PACIFIC_TEAMS):
        return PACIFIC_HISTORICAL_RANKS
    raise ValueError("未知のリーグです")


def _rate_fraction(wins, losses):
    decided = int(wins) + int(losses)
    return Fraction(int(wins), decided) if decided > 0 else Fraction(0, 1)


def _group_by_fraction(items, key_func):
    groups = {}
    for item in items:
        key = key_func(item)
        groups.setdefault(key, []).append(item)
    return groups


def _head_to_head_fraction(team, group, h2h_details):
    wins = losses = 0
    for opp in group:
        if opp == team:
            continue
        rec = h2h_details.get(team, {}).get(opp, {})
        wins += int(rec.get("win", 0))
        losses += int(rec.get("lose", 0))
    return _rate_fraction(wins, losses)


def _pairwise_h2h_signature(team, group, h2h_details):
    rates = []
    for opp in group:
        if opp == team:
            continue
        rec = h2h_details.get(team, {}).get(opp, {})
        rates.append(_rate_fraction(rec.get("win", 0), rec.get("lose", 0)))
    return tuple(sorted(rates, reverse=True))


def _league_record_fraction(team, records, league_teams):
    rec = records[team]
    league = rec.get("league", {})
    return _rate_fraction(league.get("win", 0), league.get("lose", 0))


def rank_teams_official(league_teams, records, h2h_details, previous_rank_map):
    """Rank a league using the NPB regular-season tiebreak structure.

    Central League:
      overall winning percentage -> wins -> tied-group H2H -> pairwise H2H
      signature -> previous-season rank.

    Pacific League:
      overall winning percentage -> tied-group H2H -> league-only winning
      percentage -> pairwise H2H signature -> previous-season rank.

    The pairwise signature is used only when the preceding group metric is
    exactly tied, matching the purpose of the published "当該球団間" fallback.
    """
    league_set = set(league_teams)
    if league_set == set(CENTRAL_TEAMS):
        league_type = "central"
    elif league_set == set(PACIFIC_TEAMS):
        league_type = "pacific"
    else:
        raise ValueError("未知のリーグです")

    initial = list(league_teams)
    overall_groups = _group_by_fraction(
        initial,
        lambda t: _rate_fraction(records[t]["win"], records[t]["lose"]),
    )

    ordered = []
    for overall_rate in sorted(overall_groups.keys(), reverse=True):
        group = overall_groups[overall_rate]
        if len(group) == 1:
            ordered.extend(group)
            continue

        if league_type == "central":
            win_groups = _group_by_fraction(group, lambda t: Fraction(records[t]["win"], 1))
            for win_value in sorted(win_groups.keys(), reverse=True):
                sub = win_groups[win_value]
                if len(sub) == 1:
                    ordered.extend(sub)
                    continue
                h2h_groups = _group_by_fraction(sub, lambda t: _head_to_head_fraction(t, sub, h2h_details))
                for h2h_rate in sorted(h2h_groups.keys(), reverse=True):
                    tied = h2h_groups[h2h_rate]
                    if len(tied) == 1:
                        ordered.extend(tied)
                        continue
                    sig_groups = _group_by_fraction(tied, lambda t: _pairwise_h2h_signature(t, tied, h2h_details))
                    for sig in sorted(sig_groups.keys(), reverse=True):
                        final_tied = sig_groups[sig]
                        ordered.extend(sorted(final_tied, key=lambda t: previous_rank_map.get(t, 999)))
        else:
            h2h_groups = _group_by_fraction(group, lambda t: _head_to_head_fraction(t, group, h2h_details))
            for h2h_rate in sorted(h2h_groups.keys(), reverse=True):
                tied = h2h_groups[h2h_rate]
                if len(tied) == 1:
                    ordered.extend(tied)
                    continue
                league_groups = _group_by_fraction(tied, lambda t: _league_record_fraction(t, records, league_teams))
                for league_rate in sorted(league_groups.keys(), reverse=True):
                    final_tied = league_groups[league_rate]
                    if len(final_tied) == 1:
                        ordered.extend(final_tied)
                        continue
                    sig_groups = _group_by_fraction(final_tied, lambda t: _pairwise_h2h_signature(t, final_tied, h2h_details))
                    for sig in sorted(sig_groups.keys(), reverse=True):
                        last_tied = sig_groups[sig]
                        ordered.extend(sorted(last_tied, key=lambda t: previous_rank_map.get(t, 999)))

    if set(ordered) != set(league_teams) or len(ordered) != len(league_teams):
        raise AssertionError("公式順位付けでチームが欠落しました")
    return ordered


def derive_final_rank_from_games(games, year, league_teams):
    """Derive a completed season's final rank with the official tiebreaks."""
    rec = {
        t: {
            "team": t,
            "win": 0,
            "lose": 0,
            "draw": 0,
            "league": {"win": 0, "lose": 0, "draw": 0},
        }
        for t in league_teams
    }
    h2h = {
        t1: {t2: {"win": 0, "lose": 0, "draw": 0} for t2 in league_teams}
        for t1 in league_teams
    }
    for g in games:
        if int(g.get("date", "0000")[:4]) != year or not is_finished(g):
            continue
        h, a = g["home"], g["away"]
        if h not in league_teams or a not in league_teams:
            continue
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        if hs > as_:
            rec[h]["win"] += 1; rec[a]["lose"] += 1
            rec[h]["league"]["win"] += 1; rec[a]["league"]["lose"] += 1
            h2h[h][a]["win"] += 1; h2h[a][h]["lose"] += 1
        elif hs < as_:
            rec[a]["win"] += 1; rec[h]["lose"] += 1
            rec[a]["league"]["win"] += 1; rec[h]["league"]["lose"] += 1
            h2h[a][h]["win"] += 1; h2h[h][a]["lose"] += 1
        else:
            rec[h]["draw"] += 1; rec[a]["draw"] += 1
            rec[h]["league"]["draw"] += 1; rec[a]["league"]["draw"] += 1
            h2h[h][a]["draw"] += 1; h2h[a][h]["draw"] += 1

    previous_rank_map = {t: 999 for t in league_teams}
    if year - 1 in HISTORICAL_RANK_YEARS:
        rank_map = _league_historical_rank_map(league_teams)
        idx = year - 1 - HISTORICAL_RANK_YEARS[0]
        previous_rank_map = {t: rank_map[t][idx] for t in league_teams}

    ordered = rank_teams_official(league_teams, rec, h2h, previous_rank_map)
    return {team: idx + 1 for idx, team in enumerate(ordered)}

def previous_season_ranks_for_target(target_year, historical_games, league_teams):
    """Return the best available previous-season final ranks without leakage."""
    prev_year = target_year - 1
    rank_map = _league_historical_rank_map(league_teams)
    if prev_year in HISTORICAL_RANK_YEARS:
        idx = prev_year - HISTORICAL_RANK_YEARS[0]
        return {team: rank_map[team][idx] for team in league_teams}
    # 2025 is available from the actual 2016-2025 historical game log.
    derived = derive_final_rank_from_games(historical_games, prev_year, league_teams)
    if all(derived[t] is not None for t in league_teams):
        return derived
    raise ValueError(f"{prev_year}年の前年順位を取得できません")


def build_historical_championship_prior(target_year, historical_games, league_teams):
    """Build a six-team championship prior for a target season.

    Uses only rank-history data that would have been known before target_year:
      A. P(champion next year | previous-year rank)
      B. Each team's historical championship rate
    The two are smoothed and mixed, then normalized.
    """
    rank_map = _league_historical_rank_map(league_teams)
    max_hist_year = min(2024, target_year - 1)
    available_years = [y for y in HISTORICAL_RANK_YEARS if y <= max_hist_year]
    if len(available_years) < 2:
        # Not enough reference history: equal prior.
        return {t: 1.0 / len(league_teams) for t in league_teams}

    # A: next-year champion rate conditional on previous rank.
    next_champ_by_prev_rank = {r: 0 for r in range(1, 7)}
    transition_count_by_prev_rank = {r: 0 for r in range(1, 7)}
    for i in range(len(available_years) - 1):
        y = available_years[i]
        y_next = available_years[i + 1]
        if y_next != y + 1:
            continue
        for team in league_teams:
            prev_rank = rank_map[team][y - HISTORICAL_RANK_YEARS[0]]
            next_rank = rank_map[team][y_next - HISTORICAL_RANK_YEARS[0]]
            transition_count_by_prev_rank[prev_rank] += 1
            if next_rank == 1:
                next_champ_by_prev_rank[prev_rank] += 1

    rank_rate = {}
    for r in range(1, 7):
        rank_rate[r] = _smooth_binomial_rate(
            next_champ_by_prev_rank[r],
            transition_count_by_prev_rank[r],
        )

    # B: team's own title rate in the historical reference period.
    title_rate = {}
    trials = len(available_years)
    for team in league_teams:
        titles = sum(1 for y in available_years if rank_map[team][y - HISTORICAL_RANK_YEARS[0]] == 1)
        title_rate[team] = _smooth_binomial_rate(titles, trials)

    prev_ranks = previous_season_ranks_for_target(target_year, historical_games, league_teams)
    raw_rank_prior = {team: rank_rate[prev_ranks[team]] for team in league_teams}
    raw_team_prior = {team: title_rate[team] for team in league_teams}

    rank_total = sum(raw_rank_prior.values())
    team_total = sum(raw_team_prior.values())
    if rank_total <= 0 or team_total <= 0:
        return {t: 1.0 / len(league_teams) for t in league_teams}

    rank_prior = {t: raw_rank_prior[t] / rank_total for t in league_teams}
    team_prior = {t: raw_team_prior[t] / team_total for t in league_teams}

    combined = {
        t: HISTORICAL_RANK_PRIOR_MIX * rank_prior[t]
        + HISTORICAL_TEAM_PRIOR_MIX * team_prior[t]
        for t in league_teams
    }
    total = sum(combined.values())
    return {t: combined[t] / total for t in league_teams}


def current_season_information_weight(completed_games_per_team):
    """Increase current-season influence smoothly from the April prior toward 1."""
    n = max(0.0, float(completed_games_per_team))
    growth = 1.0 - math.exp(-n / EARLY_SEASON_CURRENT_WEIGHT_SCALE)
    return EARLY_SEASON_CURRENT_WEIGHT_MIN + (
        EARLY_SEASON_CURRENT_WEIGHT_MAX - EARLY_SEASON_CURRENT_WEIGHT_MIN
    ) * growth


def apply_championship_prior_shrinkage(table, league_teams, raw_champ_probs, raw_bands, target_year, historical_games):
    """Shrink early-season championship probabilities toward historical prior.

    This layer does NOT alter individual game win/draw/loss probabilities.
    It only regularizes the season-long championship forecast, which prevents
    4-5 games in April from overwhelming the 20-year historical baseline.
    The influence of the current-season model rises automatically as games
    accumulate.
    """
    prior = build_historical_championship_prior(target_year, historical_games, league_teams)
    completed_avg = sum(float(t.get("games", 0)) for t in table) / max(1, len(table))
    current_weight = current_season_information_weight(completed_avg)
    prior_weight = 1.0 - current_weight

    # Mathematical clinch overrides every probabilistic layer.
    confirmed = [t["team"] for t in table if t.get("magic_1st") == "確定"]
    if confirmed:
        clinch_team = confirmed[0]
        for t in table:
            t["championship_prior"] = round(prior.get(t["team"], 1.0 / len(league_teams)) * 100.0, 3)
            t["championship_current_weight"] = round(current_weight, 4)
            t["champ_prob_raw"] = 100.0 if t["team"] == clinch_team else 0.0
            t["champ_prob"] = 100.0 if t["team"] == clinch_team else 0.0
            t["champ_prob_low"] = 100.0 if t["team"] == clinch_team else 0.0
            t["champ_prob_high"] = 100.0 if t["team"] == clinch_team else 0.0
        return prior, current_weight

    blended = {}
    for t in table:
        team = t["team"]
        p0 = prior.get(team, 1.0 / len(league_teams)) * 100.0
        raw = float(raw_champ_probs.get(team, 0.0))
        blended_p = prior_weight * p0 + current_weight * raw
        blended[team] = blended_p
        t["championship_prior"] = round(p0, 3)
        t["champ_prob_raw"] = round(raw, 3)
        t["champ_prob"] = round(blended_p, 3)
        t["champ_prob_prior_weight"] = round(prior_weight, 4)
        t["champ_prob_current_weight"] = round(current_weight, 4)

        band = raw_bands.get(team) if raw_bands else None
        if band:
            low = prior_weight * p0 + current_weight * float(band.get("low", raw))
            high = prior_weight * p0 + current_weight * float(band.get("high", raw))
            t["champ_prob_low"] = round(min(low, high), 3)
            t["champ_prob_high"] = round(max(low, high), 3)
        else:
            t["champ_prob_low"] = round(blended_p, 3)
            t["champ_prob_high"] = round(blended_p, 3)

    # Numerical normalization of the point estimates.
    total_blended = sum(t["champ_prob"] for t in table)
    if total_blended > 0:
        scale = 100.0 / total_blended
        for t in table:
            t["champ_prob"] = round(t["champ_prob"] * scale, 3)

    return prior, current_weight


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

    Reported only as a reference metric. The forecast uses the calibrated
    draw-rate function below rather than the raw Poisson same-score rate.
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


def estimate_current_draw_rate(games_2026, target_date):
    """Calibrate the draw probability for a forecast made on target_date.

    A 4.5% historical NPB baseline acts like a small prior sample. As 2026
    games accumulate, the observed 2026 draw rate gradually replaces that
    prior. Information after target_date is never used.
    """
    completed = [
        g for g in games_2026
        if is_finished(g) and g["date"] < target_date
    ]
    n = len(completed)
    draws = sum(1 for g in completed if int(g["home_score"]) == int(g["away_score"]))
    smoothed = (DRAW_PRIOR_RATE * DRAW_PRIOR_EFFECTIVE_GAMES + draws) / (DRAW_PRIOR_EFFECTIVE_GAMES + n)
    return max(DRAW_RATE_MIN, min(DRAW_RATE_MAX, smoothed))


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
    #
    # Performance: model weights and date parsing are precomputed once per
    # target date instead of being recomputed on every optimizer iteration.
    weighted_completed = []
    for g in completed:
        w = _model_weight(g["date"], target_date)
        if w <= 1e-5:
            continue
        h, a = g["home"], g["away"]
        hs = float(g["home_score"])
        as_ = float(g["away_score"])
        park = environment["park_log"].get(STADIUM_NAMES.get(h, "東京D"), 0.0)
        weighted_completed.append((h, a, hs, as_, w, park))

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

        for h, a, hs, as_, w, park in weighted_completed:
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
    for h, a, hs, as_, w, park in weighted_completed:
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
        if not is_finished(g) or not g.get("date") or g["date"] >= target_date:
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
    for g in sorted(
        [x for x in games if x.get("date")],
        key=lambda x: (x["date"], x["home"], x["away"])
    ):
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


def three_way_from_scores(lam_home, lam_away, draw_rate):
    """Return H/D/A probabilities with a calibrated NPB draw rate.

    Poisson scoring still supplies the relative home/away odds, but draw is
    explicitly calibrated to the empirical league/season level. This avoids
    the severe overprediction of draws caused by treating independent Poisson
    same-score probability as the actual NPB draw probability.
    """
    ph = pa = 0.0
    home_pmf = [poisson_pmf(k, lam_home) for k in range(MAX_RUNS + 1)]
    away_pmf = [poisson_pmf(k, lam_away) for k in range(MAX_RUNS + 1)]
    for h, hp in enumerate(home_pmf):
        for a, ap in enumerate(away_pmf):
            p = hp * ap
            if h > a:
                ph += p
            elif h < a:
                pa += p

    decisive_total = ph + pa
    if decisive_total <= 0:
        return 0.5 * (1.0 - draw_rate), draw_rate, 0.5 * (1.0 - draw_rate)

    draw_rate = max(DRAW_RATE_MIN, min(DRAW_RATE_MAX, float(draw_rate)))
    non_draw = 1.0 - draw_rate
    p_home = non_draw * ph / decisive_total
    p_away = non_draw * pa / decisive_total
    return p_home, draw_rate, p_away


def apply_conditional_logit_adjustment(p_home, p_draw, p_away, log_odds_adjust):
    decision_mass = max(1e-9, p_home + p_away)
    cond_home = p_home / decision_mass
    new_cond_home = sigmoid(logit(cond_home) + log_odds_adjust)
    new_home = decision_mass * new_cond_home
    new_away = decision_mass * (1.0 - new_cond_home)
    return new_home, p_draw, new_away


def predict_game(model, home, away, stadium, home_starter, away_starter, pitcher_stats, rest_diff, rest_effect, draw_rate):
    park = model["park_log"].get(stadium, 0.0)
    home_log = model["intercept"] + model["home_adv_log"] + park + model["attack"][home] - model["defense"][away]
    away_log = model["intercept"] + park + model["attack"][away] - model["defense"][home]

    # A good home starter suppresses the away team's scoring; a good away
    # starter suppresses the home team's scoring.
    home_log -= get_pitcher_run_effect(away_starter, pitcher_stats)
    away_log -= get_pitcher_run_effect(home_starter, pitcher_stats)

    lam_home = safe_exp(home_log)
    lam_away = safe_exp(away_log)
    p_home, p_draw, p_away = three_way_from_scores(lam_home, lam_away, draw_rate)

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


def _remaining_league_games(team, h2h_played):
    """Count remaining same-league games from the current H2H ledger."""
    league = CENTRAL_TEAMS if team in CENTRAL_TEAMS else PACIFIC_TEAMS
    return sum(
        max(0, GAMES_INTRA - h2h_played.get(team, {}).get(opp, 0))
        for opp in league
        if opp != team
    )


def _tie_break_rival_above_target(
    target,
    rival,
    target_wins,
    target_losses,
    rival_wins,
    rival_losses,
    target_h2h_wins,
    rival_h2h_wins,
    target_league_wins,
    target_league_losses,
    rival_league_wins,
    rival_league_losses,
    previous_rank_map,
):
    """Apply the official two-team tiebreak sequence after equal overall rate."""
    target_rate = calc_win_rate(target_wins, target_losses)
    rival_rate = calc_win_rate(rival_wins, rival_losses)

    if rival_rate > target_rate:
        return True
    if rival_rate < target_rate:
        return False

    is_central = target in CENTRAL_TEAMS and rival in CENTRAL_TEAMS

    # Central League: winning percentage -> wins -> H2H -> previous rank.
    if is_central:
        if rival_wins > target_wins:
            return True
        if rival_wins < target_wins:
            return False

    target_h2h_rate = calc_win_rate(target_h2h_wins, rival_h2h_wins)
    rival_h2h_rate = calc_win_rate(rival_h2h_wins, target_h2h_wins)
    if rival_h2h_rate > target_h2h_rate:
        return True
    if rival_h2h_rate < target_h2h_rate:
        return False

    # Pacific League: after H2H, compare league-only winning percentage.
    if not is_central:
        target_league_rate = calc_win_rate(target_league_wins, target_league_losses)
        rival_league_rate = calc_win_rate(rival_league_wins, rival_league_losses)
        if rival_league_rate > target_league_rate:
            return True
        if rival_league_rate < target_league_rate:
            return False

    # Lower previous-season rank number is the official tiebreak advantage.
    return previous_rank_map.get(rival, 999) < previous_rank_map.get(target, 999)


def evaluate_clinch_target(
    team_a, target_k, all_teams, h2h_played, previous_rank_map=None
):
    """Calculate CN / clinch numbers using official tiebreaks.

    A candidate is calculated for every team.  The presentation layer decides
    whether the sole self-clinchable 1st-place candidate gets the special M.
    Equal winning percentages are resolved with the league-specific official
    tiebreak sequence.  Multi-team equal-rate ambiguity is handled
    conservatively rather than declaring an early false clinch.
    """
    previous_rank_map = previous_rank_map or {
        t["team"]: t["rank"] for t in all_teams
    }
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_w, a_l = team_a["win"], team_a["lose"]

    # --- 自チームが残り全勝しても届かない場合は数学的消滅 ("-") ---
    max_wins_a = a_w + rem_a
    max_rate_a = calc_win_rate(max_wins_a, a_l)
    rivals_already_above = 0
    for rival in all_teams:
        if rival["team"] == ta:
            continue
        # ライバルの「現在の確定勝利数」と「残り全敗時の敗戦数」での最低勝率
        min_rate_rival = calc_win_rate(rival["win"], rival["lose"] + rival["remaining"])
        if min_rate_rival > max_rate_a:
            rivals_already_above += 1

    if rivals_already_above >= target_k:
        return "-"

    def threats_for_target_wins(x):
        actual_target_wins = min(max(0, x), rem_a)
        target_future_losses = max(0, rem_a - actual_target_wins)
        target_final_wins = a_w + x
        target_final_losses = a_l + target_future_losses
        target_rate = calc_win_rate(target_final_wins, target_final_losses)

        strict_threats = 0
        equal_rate_unresolved = 0

        for rival in all_teams:
            tb = rival["team"]
            if tb == ta:
                continue

            rem_b = rival["remaining"]
            rem_h2h = get_remaining_h2h(ta, tb, h2h_played, rem_a, rem_b)

            # To maximize the rival, allocate the target's losses to H2H first.
            # The target therefore wins the minimum feasible number of the
            # remaining games against this rival.
            target_h2h_future_wins = max(0, rem_h2h - target_future_losses)
            target_h2h_future_wins = min(rem_h2h, target_h2h_future_wins)
            rival_h2h_future_wins = rem_h2h - target_h2h_future_wins

            rival_final_wins = rival["win"] + rem_b - target_h2h_future_wins
            rival_final_losses = rival["lose"] + target_h2h_future_wins
            rival_rate = calc_win_rate(rival_final_wins, rival_final_losses)

            if rival_rate > target_rate:
                strict_threats += 1
                continue
            if rival_rate < target_rate:
                continue

            # At equal overall rate, evaluate the official two-team tiebreak.
            target_h2h_current_wins = int(
                team_a.get("h2h", {}).get(tb, {}).get("win", 0)
            )
            rival_h2h_current_wins = int(
                rival.get("h2h", {}).get(ta, {}).get("win", 0)
            )

            target_league_remaining = _remaining_league_games(ta, h2h_played)
            rival_league_remaining = _remaining_league_games(tb, h2h_played)

            # For the Pacific tiebreak, minimize the target's league winning
            # percentage while keeping its total number of wins at x.
            target_inter_remaining = max(
                0, rem_a - target_league_remaining
            )
            target_league_future_wins = max(
                target_h2h_future_wins,
                max(0, x - target_inter_remaining),
            )
            target_league_future_wins = min(
                target_league_remaining, target_league_future_wins
            )
            target_league_future_losses = (
                target_league_remaining - target_league_future_wins
            )

            # Maximize the rival's league record: every remaining league game
            # other than mandatory losses to the target is a rival win.
            rival_league_future_wins = max(
                0, rival_league_remaining - target_h2h_future_wins
            )
            rival_league_future_losses = target_h2h_future_wins

            can_outrank = _tie_break_rival_above_target(
                ta,
                tb,
                target_final_wins,
                target_final_losses,
                rival_final_wins,
                rival_final_losses,
                target_h2h_current_wins + target_h2h_future_wins,
                rival_h2h_current_wins + rival_h2h_future_wins,
                team_a.get("league", {}).get("win", 0)
                + target_league_future_wins,
                team_a.get("league", {}).get("lose", 0)
                + target_league_future_losses,
                rival.get("league", {}).get("win", 0)
                + rival_league_future_wins,
                rival.get("league", {}).get("lose", 0)
                + rival_league_future_losses,
                previous_rank_map,
            )

            if can_outrank:
                strict_threats += 1
            else:
                equal_rate_unresolved += 1

        # With multiple rivals at exactly the same attainable overall rate,
        # a three-way H2H configuration can affect the result.  Unless the
        # data prove otherwise, keep the clinch open rather than overstate it.
        if strict_threats == 0 and equal_rate_unresolved >= 2:
            strict_threats = equal_rate_unresolved

        return strict_threats

    # Search beyond the physically remaining games as well.
    #
    # This is intentional for this site's Championship Number definition:
    # CN is a "how many additional wins are needed" indicator, not merely a
    # conventional magic number constrained to the remaining schedule.
    # Therefore CN may legitimately exceed the number of games left.  The
    # presentation layer marks such values as ◇N◇ so that users can see both
    # the numerical threshold and that it is no longer self-clinchable.
    #
    # For x > remaining_games, the extra wins are hypothetical.  All actual
    # remaining games are treated as wins for the target, while the extra
    # amount only raises its final win total.
    search_limit = rem_a + TOTAL_GAMES + 20
    for x in range(0, search_limit + 1):
        if threats_for_target_wins(x) < target_k:
            return "確定" if x == 0 else x

    # A value should be found well before this point because the target's
    # winning percentage approaches 1 as hypothetical wins increase.
    return search_limit + 1

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

def build_future_probabilities(league_teams, remaining_matches, model, pitcher_stats, rest_effect, all_games, draw_rate):
    result = []
    league_set = set(league_teams)
    scheduled_matches = [
        m for m in remaining_matches if m.get("status") == "scheduled"
    ]
    scheduled_matches.sort(
        key=lambda x: (
            x.get("date") or "9999-12-31",
            x["home"],
            x["away"],
        )
    )
    for match in scheduled_matches:
        h, a = match["home"], match["away"]
        if h not in league_set and a not in league_set:
            continue
        stadium = STADIUM_NAMES.get(h, "東京D")
        h_start = match.get("home_starter") if match.get("starter_confirmed") else "未定"
        a_start = match.get("away_starter") if match.get("starter_confirmed") else "未定"
        h_start = h_start or "未定"
        a_start = a_start or "未定"
        if match.get("date"):
            rest_diff = rest_difference_for_game(match, all_games, as_of_date=None)
        else:
            # Undated postponed games have no valid rest calendar or confirmed
            # starter information, so do not manufacture a rest adjustment.
            rest_diff = 0.0
        probs = predict_game(
            model, h, a, stadium, h_start, a_start,
            pitcher_stats, rest_diff, rest_effect, draw_rate,
        )
        result.append({
            "match": match,
            "simulation_date": match.get("date") or "9999-12-31",
            "p_home": probs["home"],
            "p_draw": probs["draw"],
            "p_away": probs["away"],
        })
    return result

def determine_clinched(
    leader,
    teams,
    sim_w,
    sim_l,
    sim_league_w,
    sim_league_l,
    sim_h2h,
    remaining_after_date,
    remaining_league_after_date,
    remaining_h2h_after_date,
    previous_rank_map,
):
    """Determine mathematical 1st-place clinch after a simulated date.

    The leader is given every remaining win.  For each rival, that rival is
    given every possible non-leader game as a win and every remaining H2H
    game against the leader as a loss.  This maximizes the rival's final
    winning percentage.  Ties are then resolved using the official league
    tiebreak sequence.  If multiple rivals can simultaneously reach the same
    winning percentage as the leader, the result remains open because a
    multi-team tiebreak cannot be certified safely from pairwise extremes.
    """
    leader_rem = remaining_after_date.get(leader, 0)
    leader_final_w = sim_w[leader] + leader_rem
    leader_final_l = sim_l[leader]
    leader_rate = calc_win_rate(leader_final_w, leader_final_l)

    equal_rate_rivals = 0

    for rival in teams:
        if rival == leader:
            continue

        rival_rem = remaining_after_date.get(rival, 0)
        h2h_rem = remaining_h2h_after_date.get(leader, {}).get(rival, 0)

        # Rival wins every remaining game except H2H games against the leader.
        rival_final_w = sim_w[rival] + rival_rem - h2h_rem
        rival_final_l = sim_l[rival] + h2h_rem
        rival_rate = calc_win_rate(rival_final_w, rival_final_l)

        if rival_rate > leader_rate:
            return False
        if rival_rate < leader_rate:
            continue

        # Exact two-team tiebreak at equal overall winning percentage.
        leader_h2h_current = sim_h2h.get(leader, {}).get(rival, {})
        rival_h2h_current = sim_h2h.get(rival, {}).get(leader, {})

        leader_h2h_future_w = h2h_rem
        rival_h2h_future_w = 0

        leader_league_rem = remaining_league_after_date.get(leader, 0)
        rival_league_rem = remaining_league_after_date.get(rival, 0)

        leader_league_future_w = leader_league_rem
        leader_league_future_l = 0

        # H2H games are league games, and rival loses all of them.
        rival_league_future_w = max(0, rival_league_rem - h2h_rem)
        rival_league_future_l = h2h_rem

        rival_above = _tie_break_rival_above_target(
            leader,
            rival,
            leader_final_w,
            leader_final_l,
            rival_final_w,
            rival_final_l,
            int(leader_h2h_current.get("win", 0)) + leader_h2h_future_w,
            int(rival_h2h_current.get("win", 0)) + rival_h2h_future_w,
            sim_league_w[leader] + leader_league_future_w,
            sim_league_l[leader] + leader_league_future_l,
            sim_league_w[rival] + rival_league_future_w,
            sim_league_l[rival] + rival_league_future_l,
            previous_rank_map,
        )
        if rival_above:
            return False

        equal_rate_rivals += 1

    # A three-or-more-team equal-WP case requires aggregate H2H handling.
    # Keep the clinch open unless the leader has a strict WP advantage over all.
    if equal_rate_rivals >= 2:
        return False

    return True


def _simulated_records(league_teams, sim_w, sim_l, sim_league_w, sim_league_l, sim_league_d):
    return {
        t: {
            "team": t,
            "win": sim_w[t],
            "lose": sim_l[t],
            "draw": sim_league_d[t],
            "league": {
                "win": sim_league_w[t],
                "lose": sim_league_l[t],
                "draw": sim_league_d[t],
            },
        }
        for t in league_teams
    }


def _rank_simulated_teams(
    league_teams,
    sim_w,
    sim_l,
    sim_league_w,
    sim_league_l,
    sim_league_d,
    sim_h2h,
    previous_rank_map,
):
    records = _simulated_records(
        league_teams, sim_w, sim_l, sim_league_w, sim_league_l, sim_league_d
    )
    return rank_teams_official(league_teams, records, sim_h2h, previous_rank_map)


def simulate_full_season_probabilities(
    league_teams,
    current_standings,
    remaining_matches,
    model,
    pitcher_stats,
    rest_effect,
    all_games,
    draw_rate,
    num_sims=MAIN_NUM_SIMS,
    rng=None,
    previous_rank_map=None,
):
    rank_counts = {t: {r: 0 for r in range(1, 7)} for t in league_teams}
    clinch_date_counts = {t: {} for t in league_teams}
    base_wins = {t["team"]: t["win"] for t in current_standings}
    base_losses = {t["team"]: t["lose"] for t in current_standings}
    base_league_wins = {t["team"]: t.get("league", {}).get("win", 0) for t in current_standings}
    base_league_losses = {t["team"]: t.get("league", {}).get("lose", 0) for t in current_standings}
    base_league_draws = {t["team"]: t.get("league", {}).get("draw", 0) for t in current_standings}

    if previous_rank_map is None:
        previous_rank_map = {t: i + 1 for i, t in enumerate(league_teams)}

    standings_map = {t["team"]: t for t in current_standings}
    base_h2h = {
        t: {
            o: dict(standings_map[t].get("h2h", {}).get(o, {"win": 0, "lose": 0, "draw": 0}))
            for o in league_teams if o != t
        }
        for t in league_teams
    }

    future_probs = build_future_probabilities(
        league_teams, remaining_matches, model, pitcher_stats, rest_effect, all_games, draw_rate
    )
    rng = rng or random
    matches_by_date = defaultdict(list)
    for fp in future_probs:
        matches_by_date[fp["simulation_date"]].append(fp)
    sorted_dates = sorted(matches_by_date.keys())

    future_after = {d: {t: 0 for t in league_teams} for d in sorted_dates}
    future_league_after = {d: {t: 0 for t in league_teams} for d in sorted_dates}
    future_h2h_after = {
        d: {t: {o: 0 for o in league_teams if o != t} for t in league_teams}
        for d in sorted_dates
    }

    remaining_counts = {t: 0 for t in league_teams}
    remaining_league_counts = {t: 0 for t in league_teams}
    remaining_h2h_counts = {
        t: {o: 0 for o in league_teams if o != t} for t in league_teams
    }

    for fp in future_probs:
        h = fp["match"]["home"]
        a = fp["match"]["away"]
        h_in = h in remaining_counts
        a_in = a in remaining_counts

        if h_in:
            remaining_counts[h] += 1
        if a_in:
            remaining_counts[a] += 1

        if h_in and a_in:
            remaining_league_counts[h] += 1
            remaining_league_counts[a] += 1
            remaining_h2h_counts[h][a] += 1
            remaining_h2h_counts[a][h] += 1

    for d in sorted_dates:
        for fp in matches_by_date[d]:
            h = fp["match"]["home"]
            a = fp["match"]["away"]
            h_in = h in remaining_counts
            a_in = a in remaining_counts

            if h_in:
                remaining_counts[h] -= 1
            if a_in:
                remaining_counts[a] -= 1

            if h_in and a_in:
                remaining_league_counts[h] -= 1
                remaining_league_counts[a] -= 1
                remaining_h2h_counts[h][a] -= 1
                remaining_h2h_counts[a][h] -= 1

        future_after[d] = dict(remaining_counts)
        future_league_after[d] = dict(remaining_league_counts)
        future_h2h_after[d] = {
            t: dict(remaining_h2h_counts[t]) for t in league_teams
        }

    for _ in range(num_sims):
        sim_w = dict(base_wins)
        sim_l = dict(base_losses)
        sim_league_w = dict(base_league_wins)
        sim_league_l = dict(base_league_losses)
        sim_league_d = dict(base_league_draws)
        sim_h2h = {
            t: {o: dict(base_h2h[t][o]) for o in base_h2h[t]}
            for t in base_h2h
        }
        clinched_day = {t: None for t in league_teams}

        for d in sorted_dates:
            for fp in matches_by_date[d]:
                h = fp["match"]["home"]
                a = fp["match"]["away"]
                h_in = h in league_teams
                a_in = a in league_teams
                rnd = rng.random()
                if rnd < fp["p_draw"]:
                    if h_in and a_in:
                        sim_league_d[h] += 1
                        sim_league_d[a] += 1
                        sim_h2h[h][a]["draw"] += 1
                        sim_h2h[a][h]["draw"] += 1
                    continue

                home_wins = rng.random() < (
                    fp["p_home"] / max(1e-9, fp["p_home"] + fp["p_away"])
                )
                if home_wins:
                    if h_in:
                        sim_w[h] += 1
                    if a_in:
                        sim_l[a] += 1
                    if h_in and a_in:
                        sim_league_w[h] += 1
                        sim_league_l[a] += 1
                        sim_h2h[h][a]["win"] += 1
                        sim_h2h[a][h]["lose"] += 1
                else:
                    if a_in:
                        sim_w[a] += 1
                    if h_in:
                        sim_l[h] += 1
                    if h_in and a_in:
                        sim_league_w[a] += 1
                        sim_league_l[h] += 1
                        sim_h2h[a][h]["win"] += 1
                        sim_h2h[h][a]["lose"] += 1

            ranked = _rank_simulated_teams(
                league_teams,
                sim_w,
                sim_l,
                sim_league_w,
                sim_league_l,
                sim_league_d,
                sim_h2h,
                previous_rank_map,
            )
            leader = ranked[0]
            remaining_after = dict(future_after.get(d, {t: 0 for t in league_teams}))
            remaining_league_after = dict(
                future_league_after.get(d, {t: 0 for t in league_teams})
            )
            remaining_h2h_after = {
                t: dict(
                    future_h2h_after.get(d, {}).get(
                        t, {o: 0 for o in league_teams if o != t}
                    )
                )
                for t in league_teams
            }
            if (
                determine_clinched(
                    leader,
                    league_teams,
                    sim_w,
                    sim_l,
                    sim_league_w,
                    sim_league_l,
                    sim_h2h,
                    remaining_after,
                    remaining_league_after,
                    remaining_h2h_after,
                    previous_rank_map,
                )
                and clinched_day[leader] is None
            ):
                clinched_day[leader] = d

        final_order = _rank_simulated_teams(
            league_teams,
            sim_w,
            sim_l,
            sim_league_w,
            sim_league_l,
            sim_league_d,
            sim_h2h,
            previous_rank_map,
        )
        champ = final_order[0]
        if clinched_day[champ] is not None:
            clinch_date_counts[champ][clinched_day[champ]] = (
                clinch_date_counts[champ].get(clinched_day[champ], 0) + 1
            )
        for idx, team in enumerate(final_order):
            rank_counts[team][idx + 1] += 1

    champ_raw = {t: rank_counts[t][1] / num_sims * 100.0 for t in league_teams}
    champ_norm = normalize_probabilities_to_100(champ_raw)

    final_rank_matrix = {}
    for t in league_teams:
        final_rank_matrix[t] = {1: champ_norm[t]}
        for r in range(2, 7):
            final_rank_matrix[t][r] = int(round(rank_counts[t][r] / num_sims * 100.0))

    for t in league_teams:
        total = sum(final_rank_matrix[t].values())
        if total != 100:
            modal_rank = max(final_rank_matrix[t], key=final_rank_matrix[t].get)
            final_rank_matrix[t][modal_rank] += 100 - total

    clinch_date_probs = {
        t: {d: (count / num_sims) * 100.0 for d, count in clinch_date_counts[t].items()}
        for t in league_teams
    }

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
    draw_rate,
    seed_offset=0,
    previous_rank_map=None,
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
            draw_rate,
            UNCERTAINTY_SEASON_SIMS,
            rng=rng,
            previous_rank_map=previous_rank_map,
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
    """Number of teams that can still win 1st place by their own wins alone.

    This is the key trigger for the Championship Number (CN).  A numeric
    magic_1st value is considered active only when it can actually be achieved
    within the team's remaining games.  The current rank is irrelevant: a
    trailing team may still be the only team with a self-clinchable path.
    """
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


def format_league(records, league_teams, h2h_played, h2h_details, previous_rank_map):
    table = []
    for team in league_teams:
        r = records[team]
        r["remaining"] = TOTAL_GAMES - r["games"]
        r["rate"] = calc_win_rate(r["win"], r["lose"])
        r["h2h"] = {opp: h2h_details[team][opp] for opp in league_teams}
        table.append(r)

    ordered = rank_teams_official(league_teams, {t["team"]: t for t in table}, h2h_details, previous_rank_map)
    table = [next(t for t in table if t["team"] == team) for team in ordered]
    top_w, top_l = table[0]["win"], table[0]["lose"]
    for idx, t in enumerate(table):
        t["rank"] = idx + 1
        diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
        t["diff"] = max(0.0, diff) if idx > 0 else 0.0

    magic_names = {1: "magic_1st", 2: "magic_2nd", 3: "magic_3rd", 4: "magic_4th", 5: "magic_5th"}
    for t in table:
        for rank in range(1, 6):
            t[magic_names[rank]] = evaluate_clinch_target(
            t, rank, table, h2h_played, previous_rank_map
        )
    return validate_and_assert_standings(table)

# ------------------------------------------------------------
# Full history build
# ------------------------------------------------------------

def build_all_history_with_predictions(historical_games, games_2026):
    prior = estimate_multi_year_prior(historical_games)
    environment = estimate_environment(historical_games)
    rest_effect = estimate_rest_effect(historical_games)
    draw_baseline_rate = estimate_historical_draw_rate(historical_games)

    # Undated postponed games are real future games but must not create a
    # selectable historical snapshot date.
    all_dates = sorted({g["date"] for g in games_2026 if g.get("date")})
    history_snapshots = {}
    random.seed(RANDOM_SEED)
    central_previous_ranks = previous_season_ranks_for_target(2026, historical_games, CENTRAL_TEAMS)

    # Historical uncertainty bands are expensive. They are useful for the
    # current evaluation point, but are not needed for every past/future
    # snapshot. Compute them only for the latest date with an actual result.
    last_eval_date = max(
        (g["date"] for g in games_2026 if is_finished(g)),
        default=all_dates[0],
    )
    pacific_previous_ranks = previous_season_ranks_for_target(2026, historical_games, PACIFIC_TEAMS)

    for target_date in all_dates:
        snapshot_games = load_2026_games_as_of_date(target_date)
        games_for_date = snapshot_games if snapshot_games is not None else games_2026
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
                "league": {"win": 0, "lose": 0, "draw": 0},
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
        pitcher_stats = build_pitcher_start_stats(games_for_date, target_date)
        model = fit_run_model(games_for_date, target_date, prior, environment)
        draw_rate_target = estimate_current_draw_rate(games_for_date, target_date)

        for g in games_for_date:
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
                    else:
                        records[h]["league"]["win"] += 1
                        records[a]["league"]["lose"] += 1
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
                        records[a]["league"]["win"] += 1
                        records[h]["league"]["lose"] += 1
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
                    else:
                        records[h]["league"]["draw"] += 1
                        records[a]["league"]["draw"] += 1

        c_table = format_league(records, CENTRAL_TEAMS, h2h_played, h2h_details, central_previous_ranks)
        p_table = format_league(records, PACIFIC_TEAMS, h2h_played, h2h_details, pacific_previous_ranks)

        day_predictions = []
        processed_pairs = set()
        for g in reversed(games_for_date):
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
            rest_diff = rest_difference_for_game(g, games_for_date, as_of_date=target_date)
            probs = predict_game(model, h, a, stadium, h_start, a_start, pitcher_stats, rest_diff, rest_effect, draw_rate_target)

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
        if target_date == last_eval_date and c_self_clinchable != 1:
            c_future_d = [
                g for g in games_for_date
                if (g.get("date") is None or g["date"] > target_date)
                and (g["home"] in CENTRAL_TEAMS or g["away"] in CENTRAL_TEAMS)
                and g.get("home_score") is None and g.get("away_score") is None
                and not is_cancelled(g)
            ]
            if c_future_d:
                c_band = simulate_championship_probability_band(
                    CENTRAL_TEAMS, c_table, c_future_d, model, pitcher_stats, rest_effect,
                    games_for_date, draw_rate_target, seed_offset=int(target_date[5:7] + target_date[8:10]),
                    previous_rank_map=central_previous_ranks,
                )

        if target_date == last_eval_date and p_self_clinchable != 1:
            p_future_d = [
                g for g in games_for_date
                if (g.get("date") is None or g["date"] > target_date)
                and (g["home"] in PACIFIC_TEAMS or g["away"] in PACIFIC_TEAMS)
                and g.get("home_score") is None and g.get("away_score") is None
                and not is_cancelled(g)
            ]
            if p_future_d:
                p_band = simulate_championship_probability_band(
                    PACIFIC_TEAMS, p_table, p_future_d, model, pitcher_stats, rest_effect,
                    games_for_date, draw_rate_target, seed_offset=int(target_date[5:7] + target_date[8:10]) + 500,
                    previous_rank_map=pacific_previous_ranks,
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
        history_snapshots[target_date]["_draw_rate"] = draw_rate_target

    # Latest evaluation point.
    dates_with_finished = [
        d for d in all_dates if any(g["date"] == d and is_finished(g) for g in games_2026)
    ]
    last_eval_date = dates_with_finished[-1] if dates_with_finished else all_dates[0]
    latest_snapshot = history_snapshots[last_eval_date]
    latest_model = latest_snapshot["_model"]
    latest_pitcher_stats = latest_snapshot["_pitcher_stats"]
    latest_draw_rate = latest_snapshot["_draw_rate"]

    # Current/future matches = games without results and not cancelled.
    future_matches = [
        g for g in games_2026
        if g.get("home_score") is None and g.get("away_score") is None and not is_cancelled(g)
    ]
    c_future = [g for g in future_matches if g["home"] in CENTRAL_TEAMS or g["away"] in CENTRAL_TEAMS]
    p_future = [g for g in future_matches if g["home"] in PACIFIC_TEAMS or g["away"] in PACIFIC_TEAMS]

    c_rank_matrix, c_clinch_dates = simulate_full_season_probabilities(
        CENTRAL_TEAMS,
        latest_snapshot["central"],
        c_future,
        latest_model,
        latest_pitcher_stats,
        rest_effect,
        games_2026,
        latest_draw_rate,
        MAIN_NUM_SIMS,
        previous_rank_map=central_previous_ranks,
    )
    p_rank_matrix, p_clinch_dates = simulate_full_season_probabilities(
        PACIFIC_TEAMS,
        latest_snapshot["pacific"],
        p_future,
        latest_model,
        latest_pitcher_stats,
        rest_effect,
        games_2026,
        latest_draw_rate,
        MAIN_NUM_SIMS,
        previous_rank_map=pacific_previous_ranks,
    )

    # Historical as-of-date champion/CS probabilities.
    # These are now genuine model-based probabilities rather than a heuristic.
    for d in all_dates:
        snap = history_snapshots[d]
        snapshot_games = load_2026_games_as_of_date(d)
        games_for_date = snapshot_games if snapshot_games is not None else games_2026
        model_d = snap["_model"]
        pitcher_d = snap["_pitcher_stats"]
        draw_rate_d = snap["_draw_rate"]
        # Historical simulation uses the Git-tracked schedule/result state that
        # existed by the end of the target date.  Thus later schedule changes,
        # later result entries, and later starter announcements cannot leak in.
        future_d = []
        for g in games_for_date:
            if is_cancelled(g):
                continue
            if g.get("date") is not None and g["date"] <= d:
                continue
            future_copy = dict(g)
            future_copy["home_score"] = None
            future_copy["away_score"] = None
            future_copy["status"] = "scheduled"
            future_copy["starter_confirmed"] = False
            future_copy["home_starter"] = "未定"
            future_copy["away_starter"] = "未定"
            future_d.append(future_copy)
        c_future_d = [g for g in future_d if g["home"] in CENTRAL_TEAMS or g["away"] in CENTRAL_TEAMS]
        p_future_d = [g for g in future_d if g["home"] in PACIFIC_TEAMS or g["away"] in PACIFIC_TEAMS]

        snapshot_future_keys = {
            (g["date"], g["home"], g["away"])
            for g in future_d
            if g.get("status") == "scheduled"
        }
        current_future_keys = {
            (g["date"], g["home"], g["away"])
            for g in future_matches
            if g.get("status") == "scheduled"
        }

        if d == last_eval_date and snapshot_future_keys == current_future_keys:
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
                games_for_date,
                draw_rate_d,
                HISTORICAL_NUM_SIMS,
                previous_rank_map=central_previous_ranks,
            )
            p_mat, _ = simulate_full_season_probabilities(
                PACIFIC_TEAMS,
                snap["pacific"],
                p_future_d,
                model_d,
                pitcher_d,
                rest_effect,
                games_for_date,
                draw_rate_d,
                HISTORICAL_NUM_SIMS,
                previous_rank_map=pacific_previous_ranks,
            )

        # Raw Monte-Carlo championship probabilities.
        c_raw = {t["team"]: (100.0 if t.get("magic_1st") == "確定" else float(c_mat[t["team"]][1])) for t in snap["central"]}
        p_raw = {t["team"]: (100.0 if t.get("magic_1st") == "確定" else float(p_mat[t["team"]][1])) for t in snap["pacific"]}

        # Apply the early-season historical prior/shrinkage ONLY to the
        # championship forecast.  The underlying game probabilities remain
        # the existing Poisson model.
        c_band_raw = {}
        p_band_raw = {}
        for t in snap["central"]:
            if "champ_prob_low" in t and "champ_prob_high" in t:
                c_band_raw[t["team"]] = {
                    "low": float(t["champ_prob_low"]),
                    "high": float(t["champ_prob_high"]),
                }
        for t in snap["pacific"]:
            if "champ_prob_low" in t and "champ_prob_high" in t:
                p_band_raw[t["team"]] = {
                    "low": float(t["champ_prob_low"]),
                    "high": float(t["champ_prob_high"]),
                }

        c_prior, c_weight = apply_championship_prior_shrinkage(
            snap["central"], CENTRAL_TEAMS, c_raw, c_band_raw, int(d[:4]), historical_games
        )
        p_prior, p_weight = apply_championship_prior_shrinkage(
            snap["pacific"], PACIFIC_TEAMS, p_raw, p_band_raw, int(d[:4]), historical_games
        )

        for t in snap["central"]:
            mat = c_mat[t["team"]]
            t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else sum(mat[r] for r in (1, 2, 3))
        for t in snap["pacific"]:
            mat = p_mat[t["team"]]
            t["cs_prob"] = 100 if t.get("magic_3rd") == "確定" else sum(mat[r] for r in (1, 2, 3))

        del snap["_model"]
        del snap["_pitcher_stats"]
        del snap["_draw_rate"]

    # Latest schedules for championship-clinch cards.
    def build_filtered_clinch_schedule(team_name, future_matches_local, clinch_date_map, champ_prob, model, pitcher_stats, draw_rate):
        known_dates = {
            m["date"] for m in future_matches_local
            if m.get("date") and (m["home"] == team_name or m["away"] == team_name)
        }
        has_undated = any(
            m.get("undated_postponed")
            and (m["home"] == team_name or m["away"] == team_name)
            for m in future_matches_local
        )
        all_future_dates = sorted(known_dates | set(clinch_date_map.keys()))
        if has_undated:
            all_future_dates.append("9999-12-31")
        # Remove duplicates while preserving order.
        all_future_dates = list(dict.fromkeys(all_future_dates))
        rows = []
        cumulative = 0.0
        for d in all_future_dates:
            if d == "9999-12-31":
                undated = [
                    m for m in future_matches_local
                    if m.get("undated_postponed")
                    and (m["home"] == team_name or m["away"] == team_name)
                ]
                match = undated[0] if undated else None
            else:
                match = next(
                    (m for m in future_matches_local if m.get("date") == d and (m["home"] == team_name or m["away"] == team_name)),
                    None,
                )
            prob_raw = clinch_date_map.get(d, 0.0)
            if d == "9999-12-31":
                date_display = "未定（2試合）"
            else:
                m_int, d_int = int(d[5:7]), int(d[8:10])
                is_tentative = (m_int == 10 and d_int >= 7)
                date_display = f"({m_int}/{d_int})" if is_tentative else f"{m_int}/{d_int}"

            if match:
                is_home = match["home"] == team_name
                undated_group = (
                    d == "9999-12-31"
                    and match.get("undated_postponed")
                )
                if undated_group:
                    undated_all = [
                        m for m in future_matches_local
                        if m.get("undated_postponed")
                        and (m["home"] == team_name or m["away"] == team_name)
                    ]
                    opponents = sorted(
                        set(
                            m["away"] if m["home"] == team_name else m["home"]
                            for m in undated_all
                        )
                    )
                    opp = (
                        f"{'・'.join(opponents)}（{len(undated_all)}試合）"
                        if opponents else f"未定（{len(undated_all)}試合）"
                    )
                    ground = STADIUM_NAMES.get(match["home"], "球場")
                    win_expect_str = "-"
                else:
                    opp = match["away"] if is_home else match["home"]
                    host = match["home"]
                    ground = STADIUM_NAMES.get(host, "球場")
                    stadium = STADIUM_NAMES.get(host, "東京D")
                    h_start = match.get("home_starter") if match.get("starter_confirmed") else "未定"
                a_start = match.get("away_starter") if match.get("starter_confirmed") else "未定"
                if d != "9999-12-31":
                    rest_diff = rest_difference_for_game(match, games_2026, as_of_date=None)
                    probs = predict_game(
                        model,
                        match["home"],
                        match["away"],
                        stadium,
                        h_start or "未定",
                        a_start or "未定",
                        pitcher_stats,
                        rest_diff,
                        rest_effect,
                        draw_rate,
                    )
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
            t["team"], c_future, c_clinch_dates.get(t["team"], {}), t["champ_prob"], latest_model, latest_pitcher_stats, latest_draw_rate
        )
        for t in latest_c
    }
    p_schedules = {
        t["team"]: build_filtered_clinch_schedule(
            t["team"], p_future, p_clinch_dates.get(t["team"], {}), t["champ_prob"], latest_model, latest_pitcher_stats, latest_draw_rate
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
            "draw_calibrated_rate_latest": round(latest_draw_rate * 100.0, 2),
            "draw_prior_rate": DRAW_PRIOR_RATE * 100.0,
            "draw_prior_effective_games": DRAW_PRIOR_EFFECTIVE_GAMES,
            "draw_rate_bounds": [DRAW_RATE_MIN * 100.0, DRAW_RATE_MAX * 100.0],
            "draw_baseline_period": "2016-2025実績（参考値）",
            "prior_season_decay": PRIOR_SEASON_DECAY,
            "rest_effect_logit_per_day": round(rest_effect, 6),
            "environment": {
                "home_adv_log": round(environment["home_adv_log"], 6),
                "league_run_per_team_game": round(environment["league_rpg"], 4),
                "park_log": {k: round(v, 6) for k, v in environment["park_log"].items()},
            },
            "starter_policy": "confirmed manual starter information only; strong shrinkage",
            "draw_model": "Poisson score model for decisive-game odds + calibrated empirical draw rate",
            "ranking_rules": {
                "central": "勝率 → 勝数 → 当該球団間対戦 → 前年度順位",
                "pacific": "勝率 → 当該球団間対戦 → リーグ内対戦勝率 → 前年度順位",
                "simulation_tiebreak_aware": True,
            },
            "historical_snapshot": {
                "method": "Git tracked inputs as of 23:59:59 JST on each target date when repository history is available",
                "schedule_changes_protected": True,
                "future_result_and_starter_leakage_protected": True,
            },
            "championship_band": {
                "method": "historical-prior shrinkage + parameter-uncertainty scenarios + reduced Monte Carlo",
                "historical_prior_period": "2005-2024 rank-history reference; historical snapshots use only years available before the target season",
                "historical_rank_prior_mix": HISTORICAL_RANK_PRIOR_MIX,
                "historical_team_prior_mix": HISTORICAL_TEAM_PRIOR_MIX,
                "historical_prior_smoothing": HISTORICAL_PRIOR_SMOOTHING,
                "current_weight_min": EARLY_SEASON_CURRENT_WEIGHT_MIN,
                "current_weight_max": EARLY_SEASON_CURRENT_WEIGHT_MAX,
                "current_weight_scale_games": EARLY_SEASON_CURRENT_WEIGHT_SCALE,
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

    print("最新基準日の確認：")
    for league_key, league_label in (("central", "セ"), ("pacific", "パ")):
        latest_rows = history[default_latest][league_key]
        summary = " / ".join(
            f"{r['team']} {r['win']}-{r['lose']}-{r['draw']} 残{r['remaining']} CN1={r['magic_1st']} 優勝{float(r.get('champ_prob', 0.0)):.1f}%"
            for r in latest_rows
        )
        print(f"{league_label}：{summary}")
    undated_count = sum(1 for g in games_2026 if g.get("undated_postponed"))
    print(f"日程未定振替試合：{undated_count}試合")

    print(
        "解析・予測更新完了："
        f"{dates[0]} ～ {dates[-1]} / "
        f"Poisson攻守モデル + 複数年prior + recency + park/home + starter + rest / "
        f"Monte Carlo {MAIN_NUM_SIMS}回"
    )


if __name__ == "__main__":
    main()
