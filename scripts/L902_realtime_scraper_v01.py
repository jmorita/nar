"""L902 NAR 地方競馬 オッズ・出走表・結果 常時起動スクレイパー (楽天競馬版)

#L902_odds_snapshot_rakuten_v01.ipynb を .py 化。#L901 (netkeiba + Selenium) の後継。

主な特徴:
- Selenium 不要。requests + BeautifulSoup で完結 (Chrome WebDriver 起動不要)
- 3連単 (sanrentan) も取得 (#L901 は tanfuku/umaren のみ)
- race_id は楽天 18 桁形式 (YYYY MMDD VVVV KK NN RR)
- L905 自動投票の san/san2 (3連単) シグナルはこのスクリプトが供給する

usage:
    python scripts/L902_realtime_scraper_v01.py
    python scripts/L902_realtime_scraper_v01.py --hd 20260707
    python scripts/L902_realtime_scraper_v01.py --once
"""
import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from time import sleep

import pandas as pd

# 標準出力を行バッファに (バックグラウンド/リダイレクト実行時もログを即時フラッシュ)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# racing-common パッケージへのパスを追加 (editable install 未実施想定)
_RC_DIR = Path('C:/Users/ppny9/workspace/racing-common')
if str(_RC_DIR) not in sys.path:
    sys.path.insert(0, str(_RC_DIR))

from racing_common import rakuten_keiba as rk

# ════════════════════════════════════════════════════════════
# 設定
# ════════════════════════════════════════════════════════════
# スナップショット取得タイミング (発走前 分, ± 許容窓 分) — #L901 と同一
SNAPSHOT_CONFIG = [
    (60, 2.0),
    (30, 1.0),
    (15, 0.7),
    (10, 0.7),
    ( 5, 0.5),
    ( 3, 0.3),
    ( 1, 0.3),
]
SNAPSHOT_OFFSETS_MIN = [c[0] for c in SNAPSHOT_CONFIG]

# 取得オッズ種別 (kind → 内部表記 / 既存データ命名に合わせる: 楽天 馬複→umaren, 枠複→wakuren)
KIND_MAP = {
    rk.KIND_TANFUKU:    'tanfuku',
    rk.KIND_UMAFUKU:    'umaren',      # 馬連で統一 (#L901 互換)
    rk.KIND_SANRENTAN:  'sanrentan',
    rk.KIND_WAKUFUKU:   'wakuren',     # 枠連 (2026-07-09 取得再開)
    rk.KIND_UMATAN:     'umatan',      # 馬単 (同上)
    rk.KIND_SANRENPUKU: 'sanrenpuku',  # 3連複 (同上)
    rk.KIND_WIDE:       'wide',        # ワイド (オッズは下限-上限レンジ、同上)
}

# 結果取得タイミング
RESULT_AFTER_MIN = 5
RESULT_RETRY_MIN = 5

# ループ間隔
CHECK_INTERVAL_SEC = 30
REQUEST_DELAY_SEC = 2          # rakuten_keiba 内部 sleep と別に追加で挟む


def _resolve_nar_root() -> Path:
    """NAR ルートを解決。メインPC は D 優先、サブPC は G:/マイドライブ にフォールバック。"""
    if Path('D:/workspace').exists():
        return Path('D:/workspace/nar')
    if Path('G:/マイドライブ').exists():
        return Path('G:/マイドライブ/workspace/nar')
    return Path(__file__).resolve().parents[1]   # nar/


NAR_ROOT     = _resolve_nar_root()
DATA_DIR     = NAR_ROOT / 'data'
SHUTSUBA_DIR = DATA_DIR / 'shutsuba'
SNAPSHOT_DIR = DATA_DIR / 'odds_snapshots'
RESULT_DIR   = DATA_DIR / 'results'
LOG_DIR      = Path('C:/Users/ppny9/workspace/nar') / 'logs'
for _d in [SHUTSUBA_DIR, SNAPSHOT_DIR, RESULT_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# 実行時に main() で確定 (--hd で上書き可)
RACE_DATE = datetime.now().strftime('%Y-%m-%d')

# 処理済みセット (当日ファイルから復元)
processed_shutsuba = set()    # race_id
processed_snapshot = set()    # (race_id, label)
processed_result   = set()    # race_id


# ════════════════════════════════════════════════════════════
# 出走表 (shutsuba) 保存 — 楽天 tanfuku ページから馬名+単勝オッズを抽出
# ════════════════════════════════════════════════════════════
def save_shutsuba(rakuten_race_id: str, race_info: dict):
    """T-60 タイミングで初回のみ実行。
    tanfuku オッズ DataFrame に レースメタ情報を結合して保存。
    """
    try:
        df_tf = rk.fetch_tanfuku(rakuten_race_id)
    except Exception as e:
        print(f'  [ERROR shutsuba/{rakuten_race_id}] {e}')
        return None
    if df_tf.empty:
        return None
    df = df_tf.copy()
    df['race_id'] = rakuten_race_id
    df['race_name'] = race_info.get('race_name', '')
    df['venue'] = race_info.get('venue', '')
    df['race_num'] = race_info.get('race_num', '')
    df['start_time'] = race_info.get('start_time', '')
    df['fetched_at'] = datetime.now().isoformat(timespec='seconds')
    out = SHUTSUBA_DIR / f'{rakuten_race_id}_shutsuba.csv'
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'  [SAVE shutsuba] {out.name} ({len(df)}頭)')
    return out


# ════════════════════════════════════════════════════════════
# オッズスナップショット保存
# ════════════════════════════════════════════════════════════
def fetch_and_save_snapshot(rakuten_race_id: str, label: str):
    """指定 race_id × label で 単複/馬連/馬単/枠連/ワイド/3連複/3連単 を取得・保存。"""
    saved = []
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    ts_fn  = datetime.now().strftime('%Y%m%d-%H%M')
    # 投票クリティカルな券種を先に取得 (L905 が必要とする T3/T10 sanrentan を最速で書き出す)。
    # 分析専用の4券種(枠連/馬単/3連複/ワイド)は最後に回し、締切に効く早期書き出しを優先。
    fetchers = [
        (rk.KIND_SANRENTAN,  rk.fetch_sanrentan),  # san①/② 投票クリティカル → 最優先
        (rk.KIND_UMAFUKU,    rk.fetch_umafuku),     # 馬連
        (rk.KIND_TANFUKU,    rk.fetch_tanfuku),     # 単勝・複勝
        (rk.KIND_WAKUFUKU,   rk.fetch_wakufuku),    # ── 以下 分析専用 (最後) ──
        (rk.KIND_UMATAN,     rk.fetch_umatan),
        (rk.KIND_SANRENPUKU, rk.fetch_sanrenpuku),
        (rk.KIND_WIDE,       rk.fetch_wide),
    ]
    for kind_url, fn in fetchers:
        kind_save = KIND_MAP[kind_url]
        try:
            df = fn(rakuten_race_id)
        except Exception as e:
            print(f'  [ERROR odds] {rakuten_race_id} {label} {kind_save}: {e}')
            time.sleep(REQUEST_DELAY_SEC)
            continue
        if df is None or df.empty:
            print(f'  [WARN odds] {rakuten_race_id} {label} {kind_save}: 空')
            time.sleep(REQUEST_DELAY_SEC)
            continue
        df = df.copy()
        df.insert(0, 'race_id', rakuten_race_id)
        df.insert(1, 'snapshot_time', ts_iso)
        df.insert(2, 'label', label)
        df.insert(3, 'kind', kind_save)
        out = SNAPSHOT_DIR / f'{rakuten_race_id}_{label}_{kind_save}_{ts_fn}.csv'
        df.to_csv(out, index=False, encoding='utf-8-sig')
        saved.append(out)
        print(f'  [SAVE odds] {out.name} ({len(df)}行)')
        time.sleep(REQUEST_DELAY_SEC)
    return saved


# ════════════════════════════════════════════════════════════
# レース結果 + 払戻保存
# ════════════════════════════════════════════════════════════
def fetch_and_save_result(rakuten_race_id: str):
    try:
        data = rk.fetch_result(rakuten_race_id)
    except Exception as e:
        print(f'  [ERROR result] {rakuten_race_id}: {e}')
        return []
    if not data['ok']:
        return []
    saved = []
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    # レース条件 (馬場状態/天候/距離/クラス) は競走成績ページから追加取得ゼロで取れる。
    # result の各行に同じ値を付与しておくと、分析側で race_id join 不要で使える。
    meta = data.get('meta', {}) or {}
    for tag, df in [('result', data['result_df']), ('payout', data['payout_df'])]:
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, 'race_id', rakuten_race_id)
        df.insert(1, 'fetched_at', ts_iso)
        if tag == 'result':
            for col in ['surface', 'distance_m', 'going', 'weather', 'start_time', 'race_class']:
                df[col] = meta.get(col, '')
        out = RESULT_DIR / f'{rakuten_race_id}_{tag}.csv'
        df.to_csv(out, index=False, encoding='utf-8-sig')
        saved.append(out)
        print(f'  [SAVE result] {out.name} ({len(df)}行)')
    # レース条件を race_meta にも単独保存 (レース単位で扱いたい分析用)
    if meta:
        mrow = {'race_id': rakuten_race_id, 'fetched_at': ts_iso, **meta}
        mout = RESULT_DIR / f'{rakuten_race_id}_meta.csv'
        pd.DataFrame([mrow]).to_csv(mout, index=False, encoding='utf-8-sig')
        saved.append(mout)
    return saved


# ════════════════════════════════════════════════════════════
# 当日スケジュール取得 (楽天カレンダー → 場日別レース一覧)
# ════════════════════════════════════════════════════════════
def get_today_schedule(race_date: str) -> pd.DataFrame:
    """Returns DataFrame: rakuten_race_id, race_date, venue, race_num, start_time, race_name, distance, n_horses"""
    try:
        return rk.fetch_schedule(race_date, delay_sec=REQUEST_DELAY_SEC)
    except Exception as e:
        print(f'[WARN] スケジュール取得失敗: {e}')
        return pd.DataFrame()


def _restore_processed():
    """当日ファイルから処理済みセットを復元。"""
    processed_shutsuba.clear(); processed_snapshot.clear(); processed_result.clear()
    yyyy = RACE_DATE.replace('-', '')[:4]
    # shutsuba
    for f in SHUTSUBA_DIR.glob('*_shutsuba.csv'):
        rid = f.stem.replace('_shutsuba', '')
        if rid.startswith(yyyy):
            processed_shutsuba.add(rid)
    # snapshot
    for f in SNAPSHOT_DIR.glob('*_T*_*.csv'):
        parts = f.stem.split('_')
        if len(parts) >= 3 and parts[0].startswith(yyyy):
            processed_snapshot.add((parts[0], parts[1]))
    # result
    for f in RESULT_DIR.glob('*_result.csv'):
        rid = f.stem.replace('_result', '')
        if rid.startswith(yyyy):
            processed_result.add(rid)
    print(f'復元: 出走表 {len(processed_shutsuba)} / スナップ {len(processed_snapshot)} / 結果 {len(processed_result)}')


def scan_once(race_date: str):
    """スケジュールを 1 巡し、タイミングに合致する取得を実行。any_action を返す。"""
    global RACE_DATE
    df_schedule = get_today_schedule(race_date)
    if df_schedule.empty:
        print(f'[{datetime.now():%H:%M:%S}] スケジュール空。{CHECK_INTERVAL_SEC}秒後リトライ')
        return False

    now = datetime.now()
    any_action = False

    for _, row in df_schedule.iterrows():
        rid = row['rakuten_race_id']
        start_time = row['start_time']
        venue = row.get('venue', '')
        if not rid or not start_time:
            continue
        try:
            start_dt = datetime.strptime(f'{race_date} {start_time}', '%Y-%m-%d %H:%M')
        except ValueError:
            continue
        delta_min = (start_dt - now).total_seconds() / 60.0

        # ── 出走表 (T-60〜T-90 で 1 回) ──
        if rid not in processed_shutsuba and 1 <= delta_min <= 90:
            try:
                print(f'[{now:%H:%M:%S}] {venue} {rid} 出走表取得 (T-{delta_min:.1f}分)')
                save_shutsuba(rid, row.to_dict())
                processed_shutsuba.add(rid)
                any_action = True
            except Exception as e:
                print(f'  [ERROR shutsuba] {rid}: {e}')

        # ── オッズスナップショット ──
        for offset, window in SNAPSHOT_CONFIG:
            label = f'T{offset}'
            if (rid, label) in processed_snapshot:
                continue
            if abs(delta_min - offset) <= window:
                try:
                    print(f'[{now:%H:%M:%S}] {venue} {rid} 発走 {start_time} (T-{offset}分) オッズ取得')
                    fetch_and_save_snapshot(rid, label)
                    processed_snapshot.add((rid, label))
                    any_action = True
                except Exception as e:
                    print(f'  [ERROR odds] {rid} {label}: {e}')
                    traceback.print_exc()

        # ── レース結果 (発走 +5 分以降) ──
        if rid not in processed_result and delta_min <= -RESULT_AFTER_MIN:
            try:
                print(f'[{now:%H:%M:%S}] {venue} {rid} 結果取得試行')
                saved = fetch_and_save_result(rid)
                if saved:
                    processed_result.add(rid)
                    any_action = True
            except Exception as e:
                print(f'  [ERROR result] {rid}: {e}')

    if not any_action:
        n_sh = sum(1 for _, r in df_schedule.iterrows() if r['rakuten_race_id'] not in processed_shutsuba)
        n_sn = len(df_schedule) * len(SNAPSHOT_OFFSETS_MIN) - len(processed_snapshot)
        n_rs = sum(1 for _, r in df_schedule.iterrows() if r['rakuten_race_id'] not in processed_result)
        print(f'[{now:%H:%M:%S}] 監視中 (待機: 出走表{n_sh}/スナップ{n_sn}/結果{n_rs})')
    return any_action


# ════════════════════════════════════════════════════════════
# メインループ — 常時起動
# ════════════════════════════════════════════════════════════
def main_loop(once: bool = False):
    global RACE_DATE
    print(f'=== L902 楽天競馬 オッズ取得監視開始 {datetime.now():%Y-%m-%d %H:%M:%S} ===')
    print(f'対象日       : {RACE_DATE}')
    print(f'取得タイミング: {[f"T-{o}分(±{w}分)" for o, w in SNAPSHOT_CONFIG]}')
    print(f'取得オッズ種別: {list(KIND_MAP.values())}')
    print(f'保存ルート   : {DATA_DIR}')
    _restore_processed()

    if once:
        scan_once(RACE_DATE)
        print(f'=== L902 単発スキャン完了 {datetime.now():%H:%M:%S} ===')
        return

    try:
        while True:
            # ── 夜間 (00:00〜10:00) はスリープ ──
            _now = datetime.now()
            if 0 <= _now.hour < 10:
                wake = _now.replace(hour=10, minute=0, second=0, microsecond=0)
                wait_sec = (wake - _now).total_seconds()
                print(f'[{_now:%H:%M:%S}] 夜間休止 → 10:00 再開 ({wait_sec/3600:.1f}h)')
                sleep(wait_sec)
                RACE_DATE = datetime.now().strftime('%Y-%m-%d')
                _restore_processed()
                print(f'[{datetime.now():%H:%M:%S}] 再開。対象日: {RACE_DATE}')
                continue

            scan_once(RACE_DATE)
            sleep(CHECK_INTERVAL_SEC)

    except KeyboardInterrupt:
        print(f'[INFO] キーボード割り込みで監視終了 {datetime.now():%H:%M:%S}')
    except Exception as _err:
        print(f'[FATAL] {_err}')
        traceback.print_exc()
        raise

    print(f'=== L902 監視終了 {datetime.now():%H:%M:%S} ===')


def main():
    global RACE_DATE
    ap = argparse.ArgumentParser(description='L902 楽天競馬 オッズ常時取得スクレイパー')
    ap.add_argument('--hd', metavar='YYYYMMDD', default=None,
                    help='対象日を明示指定 (省略時は当日)')
    ap.add_argument('--once', action='store_true',
                    help='スケジュールを 1 巡だけ処理して終了 (常駐しない)')
    args = ap.parse_args()

    if args.hd:
        RACE_DATE = f'{args.hd[:4]}-{args.hd[4:6]}-{args.hd[6:8]}'

    main_loop(once=args.once)


if __name__ == '__main__':
    main()
