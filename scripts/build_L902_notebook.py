"""#L902_odds_snapshot_rakuten_v01.ipynb を生成するスクリプト。

楽天競馬を取得元とした NAR オッズ常時取得スクリプト。
#L901 (netkeiba + Selenium) の後継。

主な変更点:
- Selenium → requests + BeautifulSoup (高速化、Chrome 依存解消)
- 3連単 (sanrentan) を新規追加 (#L901 は tanfuku/umaren のみだった)
- race_id を楽天 18 桁形式に変更 (`YYYY MMDD VVVV KK NN RR`)
"""
from pathlib import Path

import nbformat as nbf

NB_PATH = Path(__file__).resolve().parents[1] / 'notebooks' / '#L902_odds_snapshot_rakuten_v01.ipynb'

CELLS: list[tuple[str, str]] = []


def md(src: str):
    CELLS.append(('markdown', src.strip('\n')))


def code(src: str):
    CELLS.append(('code', src.strip('\n')))


# ─────────────────────────────────────────────────────────────────────────────
md("""
# #L902 NAR オッズ・結果 常時取得 (楽天競馬版) v01

NAR (地方競馬) の全レースを対象に、楽天競馬 (https://keiba.rakuten.co.jp/) から
オッズスナップショット + 出走表 + 結果 を取得する常時実行スクリプト。

`#L901` (netkeiba + Selenium 版) の後継。

## 主な改善点
- **Selenium 不要** → `requests + BeautifulSoup` で完結 (Chrome WebDriver 起動不要)
- **3連単 (sanrentan) を追加取得** (#L901 は単勝/複勝/馬連のみ)
- **高速化** — 1 レース 3 種オッズで Selenium 比 約 3倍速
- **race_id を楽天 18 桁形式** (`YYYY MMDD VVVV KK NN RR`) で保存

## 取得タイミング (#L901 と同一の 7 ポイント)
| タイミング | ± 許容窓 |
|---|---|
| T-60 | ±2.0 分 |
| T-30 | ±1.0 分 |
| T-15 | ±0.7 分 |
| T-10 | ±0.7 分 |
| T-5  | ±0.5 分 |
| T-3  | ±0.3 分 |
| T-1  | ±0.3 分 |

## ディレクトリ構成 (#L901 と互換)
```
data/
├── shutsuba/{rakuten_race_id}_shutsuba.csv         T-60 時点の単複・人気 (馬名込)
├── odds_snapshots/{rakuten_race_id}_T{XX}_{kind}_{YYYYMMDD-HHMM}.csv
│                                                    kind ∈ {tanfuku, umaren, sanrentan}
└── results/{rakuten_race_id}_{result|payout}.csv    確定着順 + 払戻
```

> 既存の netkeiba 12 桁 race_id データはそのまま保持され、楽天 18 桁の新規データと
> 並列に蓄積される。`#L909` 集計は race_id の長さに非依存で動作する。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

import pandas as pd

# racing-common パッケージへのパスを追加 (editable install 未実施想定)
_RC_DIR = Path('C:/Users/ppny9/workspace/racing-common')
if str(_RC_DIR) not in sys.path:
    sys.path.insert(0, str(_RC_DIR))

from racing_common import rakuten_keiba as rk
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 設定
# ════════════════════════════════════════════════════════════
RACE_DATE = datetime.now().strftime('%Y-%m-%d')   # 当日自動
# RACE_DATE = '2026-06-22'   # 手動指定 (デバッグ用)

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

# 取得オッズ種別 (kind → 内部表記)
KIND_MAP = {
    rk.KIND_TANFUKU:   'tanfuku',
    rk.KIND_UMAFUKU:   'umaren',     # 馬連で統一 (#L901 互換)
    rk.KIND_SANRENTAN: 'sanrentan',
}

# 結果取得タイミング
RESULT_AFTER_MIN = 5
RESULT_RETRY_MIN = 5

# ループ間隔
CHECK_INTERVAL_SEC = 30
REQUEST_DELAY_SEC = 2          # rakuten_keiba 内部 sleep と別に追加で挟む

# パス — D ドライブが存在すればそちらを優先 (ユーザ環境)
_D_ROOT = Path('D:/workspace/nar')
NAR_ROOT = _D_ROOT if Path('D:/workspace').exists() else Path('C:/Users/ppny9/workspace/nar')
DATA_DIR = NAR_ROOT / 'data'
SHUTSUBA_DIR = DATA_DIR / 'shutsuba'
SNAPSHOT_DIR = DATA_DIR / 'odds_snapshots'
RESULT_DIR   = DATA_DIR / 'results'
LOG_DIR      = Path('C:/Users/ppny9/workspace/nar') / 'logs'
for d in [SHUTSUBA_DIR, SNAPSHOT_DIR, RESULT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

print(f'対象日       : {RACE_DATE}')
print(f'取得タイミング: {[f"T-{o}分(±{w}分)" for o, w in SNAPSHOT_CONFIG]}')
print(f'取得オッズ種別: {list(KIND_MAP.values())}')
print(f'保存ルート   : {DATA_DIR}')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 出走表 (shutsuba) 保存 — 楽天 tanfuku ページから馬名+単勝オッズを抽出
# ════════════════════════════════════════════════════════════
def save_shutsuba(rakuten_race_id: str, race_info: dict) -> Path | None:
    '''T-60 タイミングで初回のみ実行。
    tanfuku オッズ DataFrame に レースメタ情報を結合して保存。
    '''
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
def fetch_and_save_snapshot(rakuten_race_id: str, label: str) -> list[Path]:
    '''指定 race_id × label で tanfuku/umafuku/sanrentan を取得・保存。'''
    saved = []
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    ts_fn  = datetime.now().strftime('%Y%m%d-%H%M')
    fetchers = [
        (rk.KIND_TANFUKU,   rk.fetch_tanfuku),
        (rk.KIND_UMAFUKU,   rk.fetch_umafuku),
        (rk.KIND_SANRENTAN, rk.fetch_sanrentan),
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
def fetch_and_save_result(rakuten_race_id: str) -> list[Path]:
    try:
        data = rk.fetch_result(rakuten_race_id)
    except Exception as e:
        print(f'  [ERROR result] {rakuten_race_id}: {e}')
        return []
    if not data['ok']:
        return []
    saved = []
    ts_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    for tag, df in [('result', data['result_df']), ('payout', data['payout_df'])]:
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, 'race_id', rakuten_race_id)
        df.insert(1, 'fetched_at', ts_iso)
        out = RESULT_DIR / f'{rakuten_race_id}_{tag}.csv'
        df.to_csv(out, index=False, encoding='utf-8-sig')
        saved.append(out)
        print(f'  [SAVE result] {out.name} ({len(df)}行)')
    return saved
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 当日スケジュール取得 (楽天カレンダー → 場日別レース一覧)
# ════════════════════════════════════════════════════════════
def get_today_schedule(race_date: str) -> pd.DataFrame:
    '''Returns DataFrame: rakuten_race_id, race_date, venue, race_num, start_time, race_name, distance, n_horses'''
    try:
        return rk.fetch_schedule(race_date, delay_sec=REQUEST_DELAY_SEC)
    except Exception as e:
        print(f'[WARN] スケジュール取得失敗: {e}')
        return pd.DataFrame()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# メインループ — 常時起動
# ════════════════════════════════════════════════════════════
print(f'=== L902 楽天競馬 オッズ取得監視開始 {datetime.now():%Y-%m-%d %H:%M:%S} ===')

processed_shutsuba = set()    # race_id
processed_snapshot = set()    # (race_id, label)
processed_result   = set()    # race_id


def _restore_processed():
    '''当日ファイルから処理済みセットを復元。'''
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


_restore_processed()

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

        df_schedule = get_today_schedule(RACE_DATE)
        if df_schedule.empty:
            print(f'[{datetime.now():%H:%M:%S}] スケジュール空。{CHECK_INTERVAL_SEC}秒後リトライ')
            sleep(CHECK_INTERVAL_SEC)
            continue

        now = datetime.now()
        any_action = False

        for _, row in df_schedule.iterrows():
            rid = row['rakuten_race_id']
            start_time = row['start_time']
            venue = row.get('venue', '')
            race_num = row.get('race_num', '')
            if not rid or not start_time:
                continue
            try:
                start_dt = datetime.strptime(f'{RACE_DATE} {start_time}', '%Y-%m-%d %H:%M')
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

        sleep(CHECK_INTERVAL_SEC)

except KeyboardInterrupt:
    print(f'[INFO] キーボード割り込みで監視終了 {datetime.now():%H:%M:%S}')
except Exception as _err:
    print(f'[FATAL] {_err}')
    traceback.print_exc()
    raise

print(f'=== L902 監視終了 {datetime.now():%H:%M:%S} ===')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 集計ヘルパ (オプション)
# ════════════════════════════════════════════════════════════
def aggregate_snapshots(race_date: str = None) -> pd.DataFrame:
    '''指定日 (or 全日) のオッズスナップショットを集計。'''
    if race_date:
        ymd = race_date.replace('-', '')
        pattern = f'{ymd[:4]}*_T*_*.csv'
    else:
        pattern = '*_T*_*.csv'
    files = sorted(SNAPSHOT_DIR.glob(pattern))
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_csv(f, encoding='utf-8-sig'))
        except Exception as e:
            print(f'  [WARN] {f.name} 読込失敗: {e}')
    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    print(f'集計: {len(df):,} 行 / {df["race_id"].nunique() if not df.empty else 0} レース')
    return df


def aggregate_results(race_date: str = None) -> tuple:
    if race_date:
        ymd = race_date.replace('-', '')
        p_res = f'{ymd[:4]}*_result.csv'
        p_pay = f'{ymd[:4]}*_payout.csv'
    else:
        p_res = '*_result.csv'
        p_pay = '*_payout.csv'
    res_dfs = [pd.read_csv(f, encoding='utf-8-sig') for f in RESULT_DIR.glob(p_res)]
    pay_dfs = [pd.read_csv(f, encoding='utf-8-sig') for f in RESULT_DIR.glob(p_pay)]
    df_res = pd.concat(res_dfs, ignore_index=True) if res_dfs else pd.DataFrame()
    df_pay = pd.concat(pay_dfs, ignore_index=True) if pay_dfs else pd.DataFrame()
    return df_res, df_pay


# 使用例 (メインループを止めてから実行)
# df_snap = aggregate_snapshots(RACE_DATE)
# df_res, df_pay = aggregate_results(RACE_DATE)
""")


def main():
    nb = nbf.v4.new_notebook()
    nb_cells = []
    for ct, src in CELLS:
        if ct == 'markdown':
            nb_cells.append(nbf.v4.new_markdown_cell(src))
        else:
            nb_cells.append(nbf.v4.new_code_cell(src))
    nb['cells'] = nb_cells
    nb['metadata'] = {
        'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'},
        'language_info': {'name': 'python'},
    }
    NB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NB_PATH, 'w', encoding='utf-8') as f:
        nbf.write(nb, f)
    print(f'wrote {NB_PATH}  ({len(nb_cells)} cells)')


if __name__ == '__main__':
    main()
