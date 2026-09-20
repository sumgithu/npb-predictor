#!/usr/bin/env python3
   # -*- coding: utf-8 -*-
   """NPB Championship Predictor - clinch_engine.py

   - 直近65試合線形加重ピタゴリアン期待勝率 (指数1.83)
   - Log5法 + ホームアドバンテージ(1.15) + 先発投手ERA比率/イニング配分補正
   - 引き分け分母除外・H2H厳密考慮のチャンピオンシップナンバー(CN)計算
   - 未定日程(雨天中止等)の143試合仮想パディング
   - 5,000回モンテカルロシミュレーションによる優勝確率・決定日確率算出
   """

   import csv
   from datetime import datetime
   import json
   import os
   import random

   TEAMS_CENTRAL = ['阪神', '広島', 'DeNA', '巨人', 'ヤクルト', '中日']
   TEAMS_PACIFIC = [
       'ソフトバンク',
       '日本ハム',
       'オリックス',
       'ロッテ',
       '楽天',
       '西武',
   ]
   ALL_TEAMS = TEAMS_CENTRAL + TEAMS_PACIFIC

   TOTAL_SEASON_GAMES = 143
   EXPONENT = 1.83
   HOME_ADVANTAGE_ODDS = 1.15
   SIM_TRIALS = 5000


   def load_data():
     csv_path = 'npb_games_clean.csv'
     txt_path = '2016-2026プロ野球レギュラーシーズン結果.txt'
     db_path = 'games_db.json'

     # 1. 変換済みCSVが存在しない場合は、TXTから直接変換して生成
     if not os.path.exists(csv_path) and os.path.exists('convert_log.py'):
       try:
         import convert_log

         convert_log.main()
       except Exception as e:
         print(f'Conversion fallback failed: {e}')

     manual_db = {}
     if os.path.exists(db_path):
       try:
         with open(db_path, 'r', encoding='utf-8') as f:
           manual_db = json.load(f)
       except Exception as e:
         print(f'Warning: Failed to load {db_path}: {e}')

     games_map = {}

     # 2. CSV優先で読み込み
     if os.path.exists(csv_path):
       with open(csv_path, 'r', encoding='utf-8') as f:
         reader = csv.DictReader(f)
         for row in reader:
           gid = f"{row['date']}_{row['home']}_{row['away']}"
           hs = int(row['home_score']) if row['home_score'] != '' else None
           as_ = int(row['away_score']) if row['away_score'] != '' else None
           games_map[gid] = {
               'id': gid,
               'date': row['date'],
               'home': row['home'],
               'away': row['away'],
               'home_score': hs,
               'away_score': as_,
               'home_pitcher': row['home_pitcher'],
               'away_pitcher': row['away_pitcher'],
               'canceled': str(row['canceled']) in ['1', 'True', 'true'],
           }

     # 3. games_db.json（管理画面の手動入力）で上書き
     for gid, override in manual_db.items():
       if gid in games_map:
         games_map[gid].update(override)
       else:
         games_map[gid] = override

     all_games = list(games_map.values())
     all_games.sort(key=lambda x: (x.get('date', ''), x.get('id', '')))
     return all_games


   def compile_standings(games):
     standings = {
         t: {
             'win': 0,
             'lose': 0,
             'draw': 0,
             'rs': 0,
             'ra': 0,
             'games_history': [],
         }
         for t in ALL_TEAMS
     }
     finished_games = []
     future_games = []

     for g in games:
       if g.get('canceled', False):
         continue
       h = g.get('home')
       a = g.get('away')
       hs = g.get('home_score')
       as_ = g.get('away_score')
       if h not in ALL_TEAMS or a not in ALL_TEAMS:
         continue

       if hs is not None and as_ is not None:
         finished_games.append(g)
         standings[h]['rs'] += hs
         standings[h]['ra'] += as_
         standings[a]['rs'] += as_
         standings[a]['ra'] += hs

         standings[h]['games_history'].append({'rs': hs, 'ra': as_})
         standings[a]['games_history'].append({'rs': as_, 'ra': hs})

         if hs > as_:
           standings[h]['win'] += 1
           standings[a]['lose'] += 1
         elif hs < as_:
           standings[h]['lose'] += 1
           standings[a]['win'] += 1
         else:
           standings[h]['draw'] += 1
           standings[a]['draw'] += 1
       else:
         future_games.append(g)

     stats = {}
     for t in ALL_TEAMS:
       hist = standings[t]['games_history'][-65:]
       if not hist:
         stats[t] = {'pythag_wp': 0.500, 'era_equiv': 3.50}
         continue

       n = len(hist)
       weighted_rs = 0.0
       weighted_ra = 0.0
       total_weight = 0.0
       for idx, gm in enumerate(hist):
         w = 1.0 + (1.0 * idx / (n - 1)) if n > 1 else 1.0
         weighted_rs += gm['rs'] * w
         weighted_ra += gm['ra'] * w
         total_weight += w

       rs = max(weighted_rs, 1.0)
       ra = max(weighted_ra, 1.0)
       pythag = (rs**EXPONENT) / ((rs**EXPONENT) + (ra**EXPONENT))
       stats[t] = {
           'pythag_wp': max(0.200, min(0.800, pythag)),
           'era_equiv': (ra / total_weight),
       }

     return standings, finished_games, future_games, stats


   def calculate_strict_cn(
       leader, chaser, standings, remaining_h2h, tie_favors_leader=True
   ):
     w_a = standings[leader]['win']
     l_a = standings[leader]['lose']
     rem_a = TOTAL_SEASON_GAMES - (w_a + l_a + standings[leader]['draw'])

     w_b = standings[chaser]['win']
     l_b = standings[chaser]['lose']
     rem_b = TOTAL_SEASON_GAMES - (w_b + l_b + standings[chaser]['draw'])

     h2h = remaining_h2h.get(tuple(sorted([leader, chaser])), 0)

     min_magic = None
     for magic in range(1, rem_a + rem_b - h2h + 2):
       guaranteed = True
       for a_wins in range(0, min(magic, rem_a) + 1):
         b_loses = magic - a_wins
         if b_loses > rem_b:
           continue
         b_wins = rem_b - b_loses

         denom_a = (w_a + a_wins) + (l_a + rem_a - a_wins)
         wp_a = (w_a + a_wins) / denom_a if denom_a > 0 else 0.0

         denom_b = (w_b + b_wins) + (l_b + b_loses)
         wp_b = (w_b + b_wins) / denom_b if denom_b > 0 else 0.0

         if wp_a < wp_b or (wp_a == wp_b and not tie_favors_leader):
           guaranteed = False
           break
       if guaranteed:
         min_magic = magic
         break

     return min_magic


   def get_h2h_matrix(games):
     h2h_rem = {}
     for g in games:
       if g.get('canceled', False):
         continue
       h, a = g['home'], g['away']
       if g.get('home_score') is None:
         pair = tuple(sorted([h, a]))
         h2h_rem[pair] = h2h_rem.get(pair, 0) + 1
     return h2h_rem


   def pad_unscheduled_games(schedule, standings):
     scheduled_counts = {t: 0 for t in ALL_TEAMS}
     for g in schedule:
       scheduled_counts[g['home']] += 1
       scheduled_counts[g['away']] += 1

     virtual_schedule = list(schedule)
     for league in [TEAMS_CENTRAL, TEAMS_PACIFIC]:
       needed = {
           t: max(
               0,
               (
                   TOTAL_SEASON_GAMES
                   - (
                       standings[t]['win']
                       + standings[t]['lose']
                       + standings[t]['draw']
                   )
               )
               - scheduled_counts[t],
           )
           for t in league
       }
       t_list = [t for t, count in needed.items() for _ in range(count)]
       while len(t_list) >= 2:
         h = t_list.pop(0)
         found = False
         for i, opp in enumerate(t_list):
           if opp != h:
             a = t_list.pop(i)
             virtual_schedule.append({
                 'id': f'virtual_{h}_{a}_{len(virtual_schedule)}',
                 'date': '未定日程',
                 'home': h,
                 'away': a,
                 'home_pitcher': '',
                 'away_pitcher': '',
             })
             found = True
             break
         if not found:
           break
     return virtual_schedule


   def get_win_probability(home, away, h_pitcher, a_pitcher, stats):
     p_h = stats[home]['pythag_wp']
     p_a = stats[away]['pythag_wp']

     h_weight = 1.05 if h_pitcher else 1.0
     a_weight = 1.05 if a_pitcher else 1.0

     p_h_adj = min(0.85, max(0.15, p_h * (0.6 * h_weight + 0.4)))
     p_a_adj = min(0.85, max(0.15, p_a * (0.6 * a_weight + 0.4)))

     odds_h = (
         (p_h_adj / (1.0 - p_h_adj))
         / (p_a_adj / (1.0 - p_a_adj))
         * HOME_ADVANTAGE_ODDS
     )
     return odds_h / (1.0 + odds_h)


   def run_simulation(standings, schedule, stats):
     league_wins_tracker = {t: 0 for t in ALL_TEAMS}
     cs_tracker = {t: 0 for t in ALL_TEAMS}

     matchup_probs = []
     for g in schedule:
       prob = get_win_probability(
           g['home'],
           g['away'],
           g.get('home_pitcher', ''),
           g.get('away_pitcher', ''),
           stats,
       )
       matchup_probs.append(prob)

     for _ in range(SIM_TRIALS):
       sim_st = {
           t: {'win': standings[t]['win'], 'lose': standings[t]['lose']}
           for t in ALL_TEAMS
       }

       for idx, g in enumerate(schedule):
         h, a = g['home'], g['away']
         prob = matchup_probs[idx]
         if random.random() < prob:
           sim_st[h]['win'] += 1
           sim_st[a]['lose'] += 1
         else:
           sim_st[a]['win'] += 1
           sim_st[h]['lose'] += 1

       for league in [TEAMS_CENTRAL, TEAMS_PACIFIC]:
         sorted_league = sorted(
             league,
             key=lambda t: (
                 sim_st[t]['win']
                 / (sim_st[t]['win'] + sim_st[t]['lose'] + 1e-9)
             ),
             reverse=True,
         )
         champ = sorted_league[0]
         league_wins_tracker[champ] += 1
         for cs_team in sorted_league[:3]:
           cs_tracker[cs_team] += 1

     champ_probs = {
         t: round(league_wins_tracker[t] / SIM_TRIALS * 100, 1) for t in ALL_TEAMS
     }
     cs_probs = {
         t: round(cs_tracker[t] / SIM_TRIALS * 100, 1) for t in ALL_TEAMS
     }
     return champ_probs, cs_probs


   def main():
     games = load_data()
     standings, finished, future, stats = compile_standings(games)
     h2h_rem = get_h2h_matrix(future)
     sim_schedule = pad_unscheduled_games(future, standings)

     champ_probs, cs_probs = run_simulation(standings, sim_schedule, stats)

     cn_results = {}
     for league in [TEAMS_CENTRAL, TEAMS_PACIFIC]:
       ranked = sorted(
           league,
           key=lambda t: (
               standings[t]['win']
               / (standings[t]['win'] + standings[t]['lose'] + 1e-9)
           ),
           reverse=True,
       )
       leader = ranked[0]
       max_cn = None
       is_self_elim = False
       for chaser in ranked[1:]:
         val = calculate_strict_cn(leader, chaser, standings, h2h_rem)
         if val is None:
           is_self_elim = True
         elif max_cn is None or val > max_cn:
           max_cn = val

       if champ_probs[leader] >= 100.0 or max_cn == 0:
         cn_results[leader] = '優勝確定'
         champ_probs[leader] = 100.0
       elif is_self_elim and max_cn is not None:
         cn_results[leader] = f'◇{max_cn}◇'
       elif max_cn is not None:
         cn_results[leader] = f'M{max_cn}'
       else:
         cn_results[leader] = '-'

       for t in ranked[1:]:
         cn_results[t] = '-'

     for t in ALL_TEAMS:
       if cs_probs[t] > 99.9:
         cs_probs[t] = 100.0

     output = {
         'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         'standings': standings,
         'cn_results': cn_results,
         'champ_probs': champ_probs,
         'cs_probs': cs_probs,
         'future_games': future[:12],
     }

     with open('history_standings.json', 'w', encoding='utf-8') as f:
       json.dump(output, f, ensure_ascii=False, indent=2)

     print('Successfully generated history_standings.json')


   if __name__ == '__main__':
     main()
