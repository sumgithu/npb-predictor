#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv
import os
import re

INPUT_FILE = "2016-2026プロ野球レギュラーシーズン結果.txt"
OUTPUT_FILE = "npb_games_clean.csv"


def clean_name(s):
  return re.sub(r'[\s ]+', '', s) if s else ''


def main():
  if not os.path.exists(INPUT_FILE):
    print(f'Error: {INPUT_FILE} not found.')
    return

  current_year = ''
  current_date = ''
  rows = []

  with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    for line in f:
      raw_line = line.rstrip('\r\n')
      if not raw_line or raw_line.startswith('#'):
        continue

      # 西暦の判定 (例: 2016, 2017)
      if re.match(r'^\d{4}$', raw_line.strip()):
        current_year = raw_line.strip()
        continue

      parts = [p.strip() for p in raw_line.split('\t') if p.strip()]
      if not parts:
        continue

      # 日付判定 (例: 3/25（金）)
      date_match = re.match(r'^(\d{1,2})/(\d{1,2})', parts[0])
      if date_match:
        month = int(date_match.group(1))
        day = int(date_match.group(2))
        current_date = f'{current_year}-{month:02d}-{day:02d}'
        game_parts = parts[1:]
      else:
        game_parts = parts

      if not game_parts:
        continue

      match_info = game_parts[0]
      extra_info = game_parts[1] if len(game_parts) > 1 else ''
      pitcher_info = game_parts[2] if len(game_parts) > 2 else ''

      # スコア判定
      m = re.match(r'^([^\s]+)\s+(\d+)\s*-\s*(\d+)\s+([^\s]+)$', match_info)
      if m:
        home, h_score, a_score, away = (
            m.group(1),
            int(m.group(2)),
            int(m.group(3)),
            m.group(4),
        )
        canceled = 0
      else:
        m_cancel = re.match(r'^([^\s]+)\s+(中止|ノーゲーム)\s+([^\s]+)$', match_info)
        if m_cancel:
          home, away = m_cancel.group(1), m_cancel.group(3)
          h_score, a_score = '', ''
          canceled = 1
        else:
          continue

      h_pitcher = ''
      a_pitcher = ''
      win_m = re.search(r'勝：([^\s ]+)', clean_name(pitcher_info))
      lose_m = re.search(r'敗：([^\s ]+)', clean_name(pitcher_info))
      if win_m:
        h_pitcher = win_m.group(1)
      if lose_m:
        a_pitcher = lose_m.group(1)

      rows.append({
          'date': current_date,
          'home': home,
          'away': away,
          'home_score': h_score,
          'away_score': a_score,
          'home_pitcher': h_pitcher,
          'away_pitcher': a_pitcher,
          'canceled': canceled,
      })

  fieldnames = [
      'date',
      'home',
      'away',
      'home_score',
      'away_score',
      'home_pitcher',
      'away_pitcher',
      'canceled',
  ]
  with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

  print(
      f'Successfully created {OUTPUT_FILE} with {len(rows)} games.'
  )


if __name__ == '__main__':
  main()
