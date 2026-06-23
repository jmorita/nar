"""L901 NAR 地方競馬 オッズ・出走表・結果 常時起動スクレイパー

#L901_odds_snapshot_v01.ipynb を .py 化。
Selenium Chrome (headless) を使用。単勝/複勝/馬連オッズ + 出走表 + レース結果を取得。

done.csv キー規則:
    (race_id, 'shutsuba')   : 出走表取得済
    (race_id, 'T60') etc.   : オッズスナップショット取得済
    (race_id, 'result')     : レース結果取得済

usage:
    python scripts/L901_realtime_scraper_v01.py
    python scripts/L901_realtime_scraper_v01.py --hd 20260622
    python scripts/L901_realtime_scraper_v01.py --once
"""
import argparse
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException,
)

try:
    from racing_common import BaseScraper
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / 'racing-common'))
    from racing_common import BaseScraper

# ────────────────────────────────────────────────────────────
# パス解決
# ────────────────────────────────────────────────────────────
def _resolve_nar_root() -> Path:
    g = Path('G:/マイドライブ/workspace/nar')
    if Path('G:/マイドライブ').exists():
        return g
    return Path(__file__).resolve().parents[1]   # nar/


_NAR_ROOT    = _resolve_nar_root()
_DATA_DIR    = _NAR_ROOT / 'data'
_SCHEDULE_DIR = _DATA_DIR / 'schedule'
_SHUTSUBA_DIR = _DATA_DIR / 'shutsuba'
_SNAPSHOT_DIR = _DATA_DIR / 'odds_snapshots'
_RESULT_DIR   = _DATA_DIR / 'results'
_ROOT         = Path(__file__).resolve().parents[1]
# サブPC は G:/マイドライブ 経由でメインPCとログ共有
_LOG_DIR = (Path('G:/マイドライブ/workspace/nar/logs/scraping_progress')
            if Path('G:/マイドライブ').is_dir()
            else _ROOT / 'logs' / 'scraping_progress')
_DONE_CSV     = str(_LOG_DIR / 'l901_done.csv')

for _d in [_SCHEDULE_DIR, _SHUTSUBA_DIR, _SNAPSHOT_DIR, _RESULT_DIR, _LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ────────────────────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────────────────────
SNAPSHOT_CONFIG = [
    (60, 2.0), (30, 1.0), (15, 0.7), (10, 0.7),
    (5, 0.5),  (3, 0.3),  (1, 0.3),
]
CHECK_INTERVAL_SEC = 5     # tick() 後の待機 (Selenium が重いので短め)
REQUEST_DELAY_SEC  = 3
RESULT_AFTER_MIN   = 5
ACTIVE_HOUR_START  = 10
ACTIVE_HOUR_END    = 24   # 深夜まで稼働
SCHEDULE_REFRESH_S = 600

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Accept-Language': 'ja-JP,ja;q=0.9',
}

NAR_VENUE_NAME = {
    '30': '門別', '35': '盛岡', '36': '水沢', '42': '浦和', '43': '船橋',
    '44': '大井',  '45': '川崎', '46': '金沢', '47': '笠松', '48': '名古屋',
    '50': '園田',  '51': '姫路', '54': '高知', '55': '佐賀', '65': '帯広(ばんえい)',
}

# ────────────────────────────────────────────────────────────
# Selenium driver
# ────────────────────────────────────────────────────────────
def _new_chrome_options() -> Options:
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--window-size=1280,1024')
    opts.add_argument('--log-level=3')
    opts.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')
    return opts

# ────────────────────────────────────────────────────────────
# スケジュール取得 (requests)
# ────────────────────────────────────────────────────────────
def _get_schedule(race_date: str) -> pd.DataFrame:
    url = (f'https://nar.netkeiba.com/top/race_list_sub.html'
           f'?kaisai_date={race_date}')
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return pd.DataFrame(columns=['race_id', 'start_time', 'venue_code', 'venue'])

    soup  = BeautifulSoup(resp.text, 'html.parser')
    races, seen = [], set()
    for a in soup.find_all('a', href=True):
        m = re.search(r'race_id=(\d{12})', a['href'])
        if not m:
            continue
        race_id = m.group(1)
        if race_id[:4] != race_date[:4] or race_id in seen:
            continue
        seen.add(race_id)
        start_time = ''
        parent = a.parent
        for _ in range(6):
            if parent is None:
                break
            m2 = re.search(r'(\d{1,2}:\d{2})', parent.get_text())
            if m2:
                start_time = m2.group(1)
                break
            parent = parent.parent
        venue_code = race_id[4:6]
        races.append({'race_id': race_id, 'start_time': start_time,
                      'venue_code': venue_code,
                      'venue': NAR_VENUE_NAME.get(venue_code, venue_code)})

    df = pd.DataFrame(races).drop_duplicates('race_id').reset_index(drop=True)
    cache = _SCHEDULE_DIR / f'{race_date}.csv'
    df.to_csv(cache, index=False, encoding='utf-8-sig')
    return df

# ────────────────────────────────────────────────────────────
# 出走表取得 (Selenium)
# ────────────────────────────────────────────────────────────
def _fetch_shutsuba(drv, race_id: str) -> pd.DataFrame:
    drv.get(f'https://nar.netkeiba.com/race/shutuba.html?race_id={race_id}')
    time.sleep(REQUEST_DELAY_SEC)

    race_name = venue = race_num = ''
    try:
        race_num = drv.find_element(By.CLASS_NAME, 'Race_Num').text.strip()
    except NoSuchElementException:
        pass
    try:
        race_name = (drv.find_element(By.CLASS_NAME, 'RaceName')
                        .get_attribute('textContent').strip().split('\n')[0])
    except NoSuchElementException:
        pass
    try:
        spans = drv.find_elements(By.CSS_SELECTOR, '.RaceData02 span')
        if len(spans) >= 2:
            venue = spans[1].text.strip()
    except Exception:
        pass

    entries = []
    for i, row in enumerate(
            drv.find_elements(By.CSS_SELECTOR, 'table.ShutubaTable > tbody > tr'), 1):
        try:
            try:
                uma_no = row.find_element(By.CSS_SELECTOR, 'td[class*="Umaban"]').text.strip()
            except NoSuchElementException:
                cells  = row.find_elements(By.TAG_NAME, 'td')
                uma_no = cells[1].text.strip() if len(cells) >= 2 else str(i)
            horse  = row.find_element(By.CLASS_NAME, 'HorseInfo').text.strip()
            jockey = row.find_element(By.CLASS_NAME, 'Jockey').text.strip()
            try:
                trainer = row.find_element(By.CLASS_NAME, 'Trainer').text.strip()
            except NoSuchElementException:
                trainer = ''
            try:
                odds = row.find_element(By.CSS_SELECTOR, 'td.Popular.Txt_R').text.strip()
            except NoSuchElementException:
                odds = ''
            entries.append([uma_no, horse, jockey, trainer, odds])
        except Exception:
            pass

    df = pd.DataFrame(entries, columns=['馬番', '馬名', '騎手', '調教師', '単勝オッズ'])
    df[['race_id', 'race_name', 'venue', 'race_num', 'fetched_at']] = (
        race_id, race_name, venue, race_num,
        datetime.now().isoformat(timespec='seconds'))
    return df

# ────────────────────────────────────────────────────────────
# オッズ取得 (Selenium)
# ────────────────────────────────────────────────────────────
_TANFUKU_SELECTORS = [
    'table.RaceOdds_HorseList_Table > tbody > tr',
    'table.Odds_Table > tbody > tr',
    'table.Tansho > tbody > tr',
    '#odds-data-table tbody tr',
]

def _fetch_odds_tan_fuku(drv, race_id: str) -> pd.DataFrame:
    drv.get(f'https://nar.netkeiba.com/odds/index.html?type=b1&race_id={race_id}')
    time.sleep(REQUEST_DELAY_SEC)

    rows = []
    for sel in _TANFUKU_SELECTORS:
        rows = drv.find_elements(By.CSS_SELECTOR, sel)
        if rows:
            break

    entries = []
    for row in rows:
        try:
            cells = row.find_elements(By.TAG_NAME, 'td')
            if len(cells) < 3:
                continue
            uma_no = None
            for c in cells:
                if re.fullmatch(r'\d{1,2}', c.text.strip()) and uma_no is None:
                    uma_no = int(c.text.strip())
                    break
            if uma_no is None:
                continue
            tan = fk_lo = fk_hi = None
            for c in cells:
                txt = c.text.strip()
                m_range = re.fullmatch(
                    r'(\d+(?:\.\d+)?)\s*[-~–～〜]\s*(\d+(?:\.\d+)?)', txt)
                m_odds  = re.fullmatch(r'\d+\.\d+', txt)
                if m_range and fk_lo is None:
                    fk_lo, fk_hi = float(m_range.group(1)), float(m_range.group(2))
                elif m_odds and tan is None:
                    tan = float(txt)
            entries.append({'umaban': uma_no, 'odds_tan': tan,
                            'odds_fukusho_lo': fk_lo, 'odds_fukusho_hi': fk_hi})
        except Exception:
            pass

    if not entries:
        return pd.DataFrame()
    df = (pd.DataFrame(entries).drop_duplicates('umaban')
            .sort_values('umaban').reset_index(drop=True))
    if 'odds_tan' in df.columns:
        df['pop_tan'] = df['odds_tan'].rank(method='min').astype('Int64')
    return df


_UMAREN_SELECTORS = [
    'table.Odds_Table',
    'table.RaceOdds_HorseList_Table',
    'table.Umaren',
]

def _fetch_odds_umaren(drv, race_id: str) -> pd.DataFrame:
    drv.get(f'https://nar.netkeiba.com/odds/index.html?type=b4&race_id={race_id}')
    time.sleep(REQUEST_DELAY_SEC)

    tables = []
    for sel in _UMAREN_SELECTORS:
        tables = drv.find_elements(By.CSS_SELECTOR, sel)
        if tables:
            break

    rows_out = []
    # アプローチ1: td[0] が "N-M" ペア
    for tbl in tables:
        for tr in tbl.find_elements(By.TAG_NAME, 'tr'):
            tds = tr.find_elements(By.TAG_NAME, 'td')
            if len(tds) < 2:
                continue
            m = re.match(r'(\d+)\s*[-‐ー]\s*(\d+)', tds[0].text.strip())
            if not m:
                continue
            p1, p2 = sorted([int(m.group(1)), int(m.group(2))])
            try:
                rows_out.append({'P1': p1, 'P2': p2,
                                  'odds_umaren': float(tds[1].text.strip())})
            except ValueError:
                pass

    # アプローチ2: 各テーブル=1基準馬
    if not rows_out:
        for tbl in tables:
            base_uma = None
            for tr in tbl.find_elements(By.TAG_NAME, 'tr'):
                ths = [t.text.strip() for t in tr.find_elements(By.TAG_NAME, 'th')]
                tds = [t.text.strip() for t in tr.find_elements(By.TAG_NAME, 'td')]
                if ths and not tds:
                    m = re.fullmatch(r'(\d{1,2})', ths[0])
                    if m:
                        base_uma = int(m.group(1))
                elif not ths and len(tds) >= 2 and base_uma is not None:
                    m = re.fullmatch(r'(\d{1,2})', tds[0])
                    if not m:
                        continue
                    p1, p2 = min(base_uma, int(m.group(1))), max(base_uma, int(m.group(1)))
                    try:
                        rows_out.append({'P1': p1, 'P2': p2,
                                          'odds_umaren': float(tds[1])})
                    except ValueError:
                        pass

    if not rows_out:
        return pd.DataFrame(columns=['P1', 'P2', 'odds_umaren'])
    return (pd.DataFrame(rows_out).drop_duplicates(['P1', 'P2'])
              .sort_values(['P1', 'P2']).reset_index(drop=True))

# ────────────────────────────────────────────────────────────
# 保存
# ────────────────────────────────────────────────────────────
def _save_shutsuba(race_id: str, df: pd.DataFrame):
    out = _SHUTSUBA_DIR / f'{race_id}_shutsuba.csv'
    df.to_csv(out, index=False, encoding='utf-8-sig')


def _save_snapshot(race_id: str, label: str,
                   df_tf: pd.DataFrame, df_um: pd.DataFrame):
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    ts_fn  = datetime.now().strftime('%Y%m%d-%H%M')
    for kind, df in [('tanfuku', df_tf), ('umaren', df_um)]:
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, 'race_id',       race_id)
        df.insert(1, 'snapshot_time', ts_iso)
        df.insert(2, 'label',         label)
        df.insert(3, 'kind',          kind)
        out = _SNAPSHOT_DIR / f'{race_id}_{label}_{kind}_{ts_fn}.csv'
        df.to_csv(out, index=False, encoding='utf-8-sig')


def _fetch_result(drv, race_id: str) -> dict:
    drv.get(f'https://nar.netkeiba.com/race/result.html?race_id={race_id}')
    time.sleep(REQUEST_DELAY_SEC)

    result_rows = []
    for row in drv.find_elements(By.CSS_SELECTOR, 'table.RaceTable01 > tbody > tr'):
        try:
            cells = row.find_elements(By.TAG_NAME, 'td')
            if len(cells) < 5:
                continue
            rank = cells[0].text.strip()
            uma  = cells[2].text.strip() if len(cells) > 2 else ''
            horse_name = odds = ''
            try:
                horse_name = row.find_element(By.CLASS_NAME, 'Horse_Info').text.strip()
            except NoSuchElementException:
                pass
            try:
                odds = row.find_element(By.CSS_SELECTOR, 'td.Odds.Txt_R').text.strip()
            except NoSuchElementException:
                pass
            result_rows.append({'rank': rank, 'umaban': uma,
                                  'horse_name': horse_name, 'odds_final': odds})
        except Exception:
            continue

    payout_rows = []
    try:
        for tbl in drv.find_elements(By.CSS_SELECTOR, 'table.Payout_Detail_Table'):
            for tr in tbl.find_elements(By.TAG_NAME, 'tr'):
                tds = tr.find_elements(By.TAG_NAME, 'td')
                if len(tds) < 2:
                    continue
                kenshu = ''
                try:
                    kenshu = tr.find_element(By.TAG_NAME, 'th').text.strip()
                except NoSuchElementException:
                    pass
                combo = tds[0].text.strip().replace('\n', '|')
                pay   = (tds[1].text.strip().replace('\n', '|')
                         .replace('円', '').replace(',', ''))
                pop   = tds[2].text.strip().replace('\n', '|') if len(tds) > 2 else ''
                payout_rows.append({'kenshu': kenshu, 'combo': combo,
                                      'payout': pay, 'popular': pop})
    except Exception:
        pass

    return {
        'result_df': pd.DataFrame(result_rows),
        'payout_df': pd.DataFrame(payout_rows),
        'ok':        bool(result_rows),
    }


def _save_result(race_id: str, data: dict):
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    for tag, df in [('result', data['result_df']), ('payout', data['payout_df'])]:
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, 'race_id',    race_id)
        df.insert(1, 'fetched_at', ts_iso)
        (df.to_csv(_RESULT_DIR / f'{race_id}_{tag}.csv',
                   index=False, encoding='utf-8-sig'))

# ────────────────────────────────────────────────────────────
# スクレイパークラス
# ────────────────────────────────────────────────────────────
class NARScraper(BaseScraper):
    def __init__(self, hd: str = None, once: bool = False):
        super().__init__(
            name='L901',
            log_dir=str(_LOG_DIR),
            done_csv=_DONE_CSV,
            active_start_h=ACTIVE_HOUR_START,
            active_end_h=ACTIVE_HOUR_END,
            loop_interval_sec=CHECK_INTERVAL_SEC,
            night_poll_sec=1800,
            error_notify_threshold=5,
        )
        self._hd          = hd
        self._once        = once
        self._race_date   = hd or datetime.now().strftime('%Y%m%d')
        self._schedule    = pd.DataFrame()
        self._schedule_ts = 0.0
        self._driver      = None

    # ── driver 管理 ──────────────────────────────────────────
    def _get_driver(self):
        if self._driver is None:
            self._driver = webdriver.Chrome(options=_new_chrome_options())
            self._driver.set_page_load_timeout(30)
            self.log('Chrome driver 起動')
        return self._driver

    def _reset_driver(self):
        try:
            if self._driver:
                self._driver.quit()
        except Exception:
            pass
        self._driver = None
        return self._get_driver()

    def _safe_get(self, url: str, retries: int = 2):
        drv = self._get_driver()
        for attempt in range(retries + 1):
            try:
                drv.get(url)
                return drv
            except (TimeoutException, WebDriverException) as e:
                self.log(f'get失敗 (試行{attempt+1}): {e}', 'WARN')
                drv = self._reset_driver()
                time.sleep(REQUEST_DELAY_SEC)
        raise RuntimeError(f'safe_get 失敗: {url}')

    # ── スケジュール ──────────────────────────────────────────
    def _refresh_schedule(self):
        if (time.time() - self._schedule_ts < SCHEDULE_REFRESH_S
                and not self._schedule.empty):
            return
        df = _get_schedule(self._race_date)
        if not df.empty:
            self._schedule    = df
            self._schedule_ts = time.time()
            self.log(f'スケジュール更新: {len(df)}レース')
        else:
            self.log('スケジュール取得不可', 'WARN')

    # ── tick ─────────────────────────────────────────────────
    def tick(self):
        new_date = self._hd or datetime.now().strftime('%Y%m%d')
        if new_date != self._race_date:
            self._race_date   = new_date
            self._schedule    = pd.DataFrame()
            self._schedule_ts = 0.0
            self.log(f'対象日切替: hd={new_date}')

        self._refresh_schedule()
        if self._schedule.empty:
            return

        now = datetime.now()
        drv = self._get_driver()

        for _, row in self._schedule.iterrows():
            race_id    = row['race_id']
            start_time = row['start_time']
            venue      = row.get('venue', '')
            if not race_id or not start_time:
                continue
            try:
                start_dt  = datetime.strptime(
                    f'{self._race_date} {start_time}', '%Y%m%d %H:%M')
            except ValueError:
                continue
            delta_min = (start_dt - now).total_seconds() / 60.0

            # 出走表 (発走 90分〜1分前)
            if not self.is_done(race_id, 'shutsuba') and 1 <= delta_min <= 90:
                try:
                    self.log(f'{venue} {race_id} 出走表取得')
                    df_sh = _fetch_shutsuba(drv, race_id)
                    _save_shutsuba(race_id, df_sh)
                    self.mark_done(race_id, 'shutsuba')
                    self.log(f'shutsuba 保存: {race_id} ({len(df_sh)}頭)')
                except Exception as e:
                    self.log(f'shutsuba 失敗 {race_id}: {e}', 'ERROR')
                    drv = self._reset_driver()

            # オッズスナップショット
            for offset, window in SNAPSHOT_CONFIG:
                label = f'T{offset}'
                if self.is_done(race_id, label):
                    continue
                if abs(delta_min - offset) <= window:
                    try:
                        self.log(f'{venue} {race_id} {start_time} (T-{offset}分) オッズ取得')
                        df_tf = _fetch_odds_tan_fuku(drv, race_id)
                        df_um = _fetch_odds_umaren(drv, race_id)
                        _save_snapshot(race_id, label, df_tf, df_um)
                        self.mark_done(race_id, label)
                        self.log(f'odds[{label}] 保存: {race_id}')
                    except Exception as e:
                        self.log(f'odds 失敗 {race_id} {label}: {e}', 'ERROR')
                        drv = self._reset_driver()

            # レース結果 (発走後 RESULT_AFTER_MIN 分以降)
            if not self.is_done(race_id, 'result') and delta_min <= -RESULT_AFTER_MIN:
                try:
                    self.log(f'{venue} {race_id} 結果取得試行')
                    data = _fetch_result(drv, race_id)
                    if data['ok']:
                        _save_result(race_id, data)
                        self.mark_done(race_id, 'result')
                        self.log(f'result 保存: {race_id}')
                except Exception as e:
                    self.log(f'result 失敗 {race_id}: {e}', 'ERROR')
                    drv = self._reset_driver()

        if self._once:
            self._stop = True

    def run(self):
        try:
            super().run()
        finally:
            try:
                if self._driver:
                    self._driver.quit()
                    self.log('Chrome driver 終了')
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description='L901 NARオッズスクレイパー')
    parser.add_argument('--hd',   default=None, help='対象日 (YYYYMMDD)')
    parser.add_argument('--once', action='store_true', help='1ループで終了')
    args = parser.parse_args()
    NARScraper(hd=args.hd, once=args.once).run()


if __name__ == '__main__':
    main()
