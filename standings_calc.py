import datetime
import json
import math
import re
import requests
from bs4 import BeautifulSoup

TOTAL_GAMES = 143


def calculate_rate(w, l):
    decided = w + l
    return round(w / decided, 3) if decided > 0 else 0.000


def get_magic_number(team_a, team_b):
    """チームAがチームBを最終勝率で自力で上回るためのマジックナンバーを算出

    M = チームAの必要勝利数 + チームBの敗戦数
    """
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - team_b["games"]

    # チームBが残り全勝した場合の最高勝率
    b_max_win = team_b["win"] + rem_b
    b_max_rate = calculate_rate(b_max_win, team_b["lose"])

    # チームAが残り全勝しても届かない場合（自力可能性消滅）
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calculate_rate(a_max_win, team_a["lose"])
    if a_max_rate < b_max_rate:
        return "-"

    # チームAが残りX勝（rem_a - X敗）したときの勝率が、Bの最高勝率を上回る最小のXを探索
    for x in range(0, rem_a + 1):
        target_rate = calculate_rate(
            team_a["win"] + x, team_a["lose"] + (rem_a - x)
        )
        if target_rate > b_max_rate:
            m = x + 1
            return 0 if m <= 0 else m

    # 同率タイの場合
    return 1


def evaluate_clinch(team, sorted_teams, target_rank):
    """target_rank（1:優勝, 2:2位以上, 3:3位以上, 4:4位以上, 5:5位以上）に対するクリンチ状態を判定"""
    # 比較対象は「target_rank + 1」位のチーム（例：優勝なら2位、3位確定なら4位）
    competitors = sorted_teams[target_rank:]
    if not competitors:
        return "確定"

    # すでに下位チームが残り全勝しても自軍の現在勝利数・勝率に届かない場合
    already_clinched = True
    for comp in competitors:
        comp_rem = TOTAL_GAMES - comp["games"]
        comp_max_rate = calculate_rate(comp["win"] + comp_rem, comp["lose"])
        cur_rate = calculate_rate(team["win"], team["lose"])
        if cur_rate <= comp_max_rate:
            already_clinched = False
            break

    if already_clinched and team["games"] > 0:
        return "確定"

    # 競合チーム全体に対して必要な最大マジックを算出
    max_m = 0
    eliminated = True
    for comp in competitors:
        m = get_magic_number(team, comp)
        if m != "-":
            eliminated = False
            if isinstance(m, int) and m > max_m:
                max_m = m

    if eliminated:
        return "-"
    if max_m == 0:
        return "確定"
    return max_m


def scrape_league_standings():
    url = "https://baseball.yahoo.co.jp/npb/standings/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }

    res = requests.get(url, headers=headers, timeout=10)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")

    tables = soup.find_all(
        "table", class_=lambda c: c and "bb-rankTable" in c
    )

    leagues = {"central": [], "pacific": []}
    league_keys = ["central", "pacific"]

    for idx, tbl in enumerate(tables[:2]):
        l_key = league_keys[idx]
        tbody = tbl.find("tbody")
        if not tbody:
            continue

        rows = tbody.find_all("tr")
        rank_counter = 1
        for r in rows:
            cols = [td.get_text().strip() for td in r.find_all(["td", "th"])]
            if len(cols) < 8:
                continue

            # 球団名、試合数、勝、敗、分、勝率、差
            team_name = re.sub(r"[\d\s]+", "", cols[1])
            games = int(cols[2])
            win = int(cols[3])
            lose = int(cols[4])
            draw = int(cols[5])
            rate = float(cols[6])
            diff = float(cols[7]) if cols[7] != "-" else 0.0

            leagues[l_key].append(
                {
                    "rank": rank_counter,
                    "team": team_name,
                    "games": games,
                    "win": win,
                    "lose": lose,
                    "draw": draw,
                    "rate": rate,
                    "diff": diff,
                }
            )
            rank_counter += 1

    return leagues


def main():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    updated_at_str = jst_now.strftime("%Y-%m-%d %H:%M JST")
    print(f"[{updated_at_str}] 順位表・クリンチナンバー算出開始")

    data = scrape_league_standings()

    result = {"updated_at": updated_at_str, "central": [], "pacific": []}

    for l_key in ["central", "pacific"]:
        teams = data[l_key]
        for t in teams:
            t["magic_1st"] = evaluate_clinch(t, teams, 1)  # 優勝 (1st)
            t["magic_2nd"] = evaluate_clinch(
                t, teams, 2
            )  # CS本拠地 / 2位以上 (2nd)
            t["magic_3rd"] = evaluate_clinch(
                t, teams, 3
            )  # CS進出 / 3位以上 (3rd)
            t["magic_4th"] = evaluate_clinch(t, teams, 4)  # 4位以上 (4th)
            t["magic_5th"] = evaluate_clinch(
                t, teams, 5
            )  # 最下位回避 / 5位以上 (5th)
            result[l_key].append(t)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("standings.json の出力が完了しました。")


if __name__ == "__main__":
    main()
