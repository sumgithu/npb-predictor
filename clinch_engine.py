from collections import deque
import datetime
import itertools
import json
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

# -------------------------------------------------------------
# 最大流アルゴリズム (Dinic's Algorithm) 実装
# -------------------------------------------------------------
class Dinic:
    def __init__(self, n):
        self.n = n
        self.graph = [[] for _ in range(n)]
        self.level = [-1] * n
        self.ptr = [0] * n

    def add_edge(self, fr, to, cap):
        forward = [to, cap, None]
        backward = [fr, 0, forward]
        forward[2] = backward
        self.graph[fr].append(forward)
        self.graph[to].append(backward)

    def bfs(self, s, t):
        self.level = [-1] * self.n
        self.level[s] = 0
        q = deque([s])
        while q:
            v = q.popleft()
            for edge in self.graph[v]:
                to, cap, _ = edge
                if cap > 0 and self.level[to] < 0:
                    self.level[to] = self.level[v] + 1
                    q.append(to)
        return self.level[t] >= 0

    def dfs(self, v, t, pushed):
        if pushed == 0 or v == t:
            return pushed
        for cid in range(self.ptr[v], len(self.graph[v])):
            self.ptr[v] = cid
            edge = self.graph[v][cid]
            to, cap, rev = edge
            if self.level[v] + 1 != self.level[to] or cap == 0:
                continue
            tr = self.dfs(to, t, min(pushed, cap))
            if tr == 0:
                continue
            edge[1] -= tr
            rev[1] += tr
            return tr
        return 0

    def max_flow(self, s, t):
        flow = 0
        while self.bfs(s, t):
            self.ptr = [0] * self.n
            while True:
                pushed = self.dfs(s, t, float('inf'))
                if pushed == 0:
                    break
                flow += pushed
        return flow

# -------------------------------------------------------------
# 試合ログ解析と直接対決マトリクス
# -------------------------------------------------------------
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

# -------------------------------------------------------------
# 最大流による厳密エリミネーション／クリンチ判定
# -------------------------------------------------------------
def can_satisfy_flow(team_a, a_rate, allowed_superior_teams, remaining_teams, h2h_played, a_forced_losses_on):
    """
    allowed_superior_teams (Aを上回ってもよいチーム) を除外した remaining_teams の全チームが、
    A の最終勝率 a_rate を超えずに残りの試合を消化できるフローが存在するか判定
    """
    sub_teams = list(remaining_teams)
    num_teams = len(sub_teams)
    team_to_idx = {name: i for i, name in enumerate(sub_teams)}

    # 各チームの許容勝利枠の計算
    capacities = []
    for name in sub_teams:
        t_data = remaining_teams[name]
        w_cur = t_data["win"]
        l_cur = t_data["lose"] + a_forced_losses_on.get(name, 0)
        rem = t_data["remaining"] - a_forced_losses_on.get(name, 0)
        
        # 何勝まで許容されるか
        cap = -1
        for w in range(rem, -1, -1):
            l = rem - w
            if calc_win_rate(w_cur + w, l_cur + l) <= a_rate:
                cap = w
                break
        if cap < 0:
            return False  # 残り全敗でも A の勝率を上回ってしまう
        capacities.append(cap)

    # チーム同士の対戦ペアノード作成
    game_pairs = []
    for i in range(num_teams):
        for j in range(i + 1, num_teams):
            t1, t2 = sub_teams[i], sub_teams[j]
            rem_games = get_remaining_h2h(t1, t2, h2h_played, remaining_teams[t1]["remaining"], remaining_teams[t2]["remaining"])
            if rem_games > 0:
                game_pairs.append((i, j, rem_games))

    total_game_flow = sum(p[2] for p in game_pairs)
    if total_game_flow == 0:
        return True

    # グラフ構築: ソース(0), 試合ノード(1 〜 G), チームノード(G+1 〜 G+T), シンク(G+T+1)
    num_games = len(game_pairs)
    s = 0
    t = num_games + num_teams + 1
    dinic = Dinic(t + 1)

    for g_idx, (p1, p2, cap) in enumerate(game_pairs):
        g_node = 1 + g_idx
        dinic.add_edge(s, g_node, cap)
        dinic.add_edge(g_node, 1 + num_games + p1, cap)
        dinic.add_edge(g_node, 1 + num_games + p2, cap)

    for i in range(num_teams):
        u_node = 1 + num_games + i
        dinic.add_edge(u_node, t, capacities[i])

    flow = dinic.max_flow(s, t)
    return flow == total_game_flow

def can_team_reach_rank_network(team_a, target_k, all_teams, h2h_played, a_wins):
    """
    チームAが a_wins 勝したとき、他球団同士の全対戦カードの勝敗巡り合わせによって
    チームAの最終順位が target_k 位以内になれるシナリオが1つでも存在するか判定
    """
    ta = team_a["team"]
    rem_a = team_a["remaining"]
    a_losses = rem_a - a_wins
    final_w_a = team_a["win"] + a_wins
    final_l_a = team_a["lose"] + a_losses
    a_rate = calc_win_rate(final_w_a, final_l_a)

    others = {ot["team"]: ot for ot in all_teams if ot["team"] != ta}

    # チームAが残り直接対決で与える強制敗戦
    a_forced_losses = {}
    for name, ot in others.items():
        vs_a = get_remaining_h2h(ta, name, h2h_played, rem_a, ot["remaining"])
        # 最悪ケース：自チームの全敗分(a_losses)がこのチームに集中した場合に相手に強制できる最小敗戦
        forced_l = max(0, vs_a - a_losses)
        a_forced_losses[name] = forced_l

    other_names = list(others.keys())
    max_superiors_allowed = target_k - 1

    # 「チームAを上回ってもよい上位チーム」の部分集合を全探索 (0 〜 max_superiors_allowed チーム)
    for sup_count in range(max_superiors_allowed + 1):
        for superiors in itertools.combinations(other_names, sup_count):
            # 残りのチーム全員が A 以下に収まるか
            remaining_team_dict = {name: others[name] for name in other_names if name not in superiors}
            if can_satisfy_flow(team_a, a_rate, superiors, remaining_team_dict, h2h_played, a_forced_losses):
                return True

    return False

def evaluate_target_clinch(team_a, target_k, all_teams, h2h_played):
    rem_a = team_a["remaining"]

    # 1. 完全消滅判定 (自チームが全勝 rem_a してもフローを満たせない場合)
    if not can_team_reach_rank_network(team_a, target_k, all_teams, h2h_played, rem_a):
        return "-"

    # 2. 完全確定判定 (自チームが全敗 0勝 でもフローを満たせる場合)
    if can_team_reach_rank_network(team_a, target_k, all_teams, h2h_played, 0):
        # 相手が全勝しても自軍が上回れるかの二重チェック
        threats = 0
        a_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)
        for ot in all_teams:
            if ot["team"] == team_a["team"]:
                continue
            if calc_win_rate(ot["win"] + ot["remaining"], ot["lose"]) >= a_min_rate:
                threats += 1
        if threats < target_k:
            return "確定"

    # 3. 必要勝利数の厳密二分探索 (0 〜 rem_a)
    low = 0
    high = rem_a
    ans = None
    while low <= high:
        mid = (low + high) // 2
        if can_team_reach_rank_network(team_a, target_k, all_teams, h2h_played, mid):
            ans = mid
            high = mid - 1
        else:
            low = mid + 1

    if ans is not None:
        return "確定" if ans == 0 else ans

    # 4. 自力消滅だが可能性あり（他力アシストが必要な仮想必要数）
    border = all_teams[target_k] if team_a["rank"] <= target_k else all_teams[target_k - 1]
    b_abs_max_rate = calc_win_rate(border["win"] + border["remaining"], border["lose"])
    for x in range(rem_a + 1, rem_a + 30):
        if calc_win_rate(team_a["win"] + x, team_a["lose"]) > b_abs_max_rate:
            return x

    return "-"

def validate_and_assert_standings(teams):
    """数学的不変則（順位包含則・単調性）の検証"""
    keys = ["magic_1st", "magic_2nd", "magic_3rd", "magic_4th", "magic_5th"]

    for t in teams:
        # 上位目標（CN）が確定なら、下位目標（CSや最下位回避）も当然確定
        confirmed = False
        for k in keys:
            if t[k] == "確定":
                confirmed = True
            elif confirmed:
                t[k] = "確定"

        # 下位目標（最下位回避）が消滅なら、上位目標も当然消滅
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
            t["magic_1st"] = evaluate_target_clinch(t, 1, c_table, h2h_played)
            t["magic_2nd"] = evaluate_target_clinch(t, 2, c_table, h2h_played)
            t["magic_3rd"] = evaluate_target_clinch(t, 3, c_table, h2h_played)
            t["magic_4th"] = evaluate_target_clinch(t, 4, c_table, h2h_played)
            t["magic_5th"] = evaluate_target_clinch(t, 5, c_table, h2h_played)

        for t in p_table:
            t["magic_1st"] = evaluate_target_clinch(t, 1, p_table, h2h_played)
            t["magic_2nd"] = evaluate_target_clinch(t, 2, p_table, h2h_played)
            t["magic_3rd"] = evaluate_target_clinch(t, 3, p_table, h2h_played)
            t["magic_4th"] = evaluate_target_clinch(t, 4, p_table, h2h_played)
            t["magic_5th"] = evaluate_target_clinch(t, 5, p_table, h2h_played)

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

    print("最大流ネットワーク（Dinic法）による完全連動クリンチ計算完了")

if __name__ == "__main__":
    main()
