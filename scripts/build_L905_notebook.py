"""#L905_auto_bettor_v01.ipynb を生成するスクリプト。

T-3 が保存されたタイミングで T-10 → T-3 の下落率を判定し、
閾値を超える買い目を SPAT4 で投票する。
"""
from pathlib import Path

import nbformat as nbf

NB_PATH = Path(__file__).resolve().parents[1] / 'notebooks' / '#L905_auto_bettor_v01.ipynb'

CELLS: list[tuple[str, str]] = []


def md(src: str):
    CELLS.append(('markdown', src.strip('\n')))


def code(src: str):
    CELLS.append(('code', src.strip('\n')))


# ─────────────────────────────────────────────────────────────────────────────
md("""
# #L905 自動投票 (SPAT4) v01

楽天競馬で取得した T-10/T-3 オッズの **下落率シグナル** で SPAT4 に自動投票する。

## 動作フロー
1. `data/odds_snapshots/` を `poll_interval_sec` ごとに監視
2. 新規 T-3 ファイルを検出 → 同 race_id の T-10 を探す
3. `(odds_T3 - odds_T10) / odds_T10 * 100` を計算
4. 設定閾値以下の買い目を抽出
   - 単勝: `tan_change_rate_max` 以下 (デフォルト -40%)
   - 馬連: `uma_change_rate_max` 以下 (デフォルト -50%)
5. オッズ範囲・1レース上限・1日上限を適用してフィルタ
6. SPAT4 で投票 (DRY-RUN モードでは画面操作のみ、確定はしない)

## 設定
すべての閾値・上限は `config/spat4_credentials.json` で変更可能。
gitignore 済みなので機密情報はリポジトリに入らない。

| キー | デフォルト | 説明 |
|---|---|---|
| `thresholds.T_START` | T10 | 比較元 T-label |
| `thresholds.T_END` | T3 | 比較先 T-label (= 発火タイミング) |
| `thresholds.tan_change_rate_max` | -30.0 | 単勝の change_rate(%) 上限 (これ以下で買う) — #L909 集計の最適 -32% / 期待回収率 138% を踏まえた暫定 |
| `thresholds.uma_change_rate_max` | -30.0 | 馬連の change_rate(%) 上限 (これ以下で買う) — #L909 集計の最適 -32% / 期待回収率 193% を踏まえた暫定 |
| `betting.stake_yen` | 100 | 1 買い目あたりの賭け金 |
| `betting.dry_run` | true | 投票を確定しない (ログ出力のみ) |
| `betting.max_bets_per_race_tan` | 1 | 単勝の 1 レースあたり最大買い目数 (下落率最大のものを残す) |
| `betting.max_bets_per_race_uma` | 10 | 馬連の 1 レースあたり最大買い目数 (下落率最大のものを残す) |
| `betting.max_total_bets_per_day` | 500 | 1 日累計上限 |
| `betting.min_odds_to_buy` | 1.5 | この単勝オッズ未満は買わない |
| `betting.max_odds_to_buy` | 2000.0 | この単勝オッズ超過は買わない |
| `operation.poll_interval_sec` | 10 | スナップショット監視ポーリング間隔 |
| `operation.data_root` | (自動検出, 最低優先) | データルート。G:/マイドライブ/ → D:/ → C:/ の順に **実在チェック** で自動採用 (= サブPC では config を無視して G ドライブを選ぶ)。明示指定したい場合は `NAR_DATA_ROOT` 環境変数 |

## ⚠ 安全運用
- **DRY-RUN がデフォルト**。`dry_run: false` に手動切替するまで投票は確定されない
- LIVE 運用前に手動で SPAT4 にログインして残高・買い目を確認
- 初日は `stake_yen: 100`、`max_total_bets_per_day: 5` 程度から開始
- 異常検出時は Ctrl+C で即停止可能
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
import os
import sys
import json
import time
import traceback
from datetime import datetime, date
from pathlib import Path

import pandas as pd

# racing-common パッケージへのパス
_RC_DIR = Path('C:/Users/ppny9/workspace/racing-common')
if str(_RC_DIR) not in sys.path:
    sys.path.insert(0, str(_RC_DIR))

from racing_common import spat4
from racing_common.notify import send_email_if_configured
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 設定ロード
# ════════════════════════════════════════════════════════════
NAR_ROOT = Path('C:/Users/ppny9/workspace/nar')
CONFIG_PATH = NAR_ROOT / 'config' / 'spat4_credentials.json'

config = spat4.load_config(CONFIG_PATH)

# 閾値
T_START = config['thresholds']['T_START']
T_END   = config['thresholds']['T_END']
TAN_CHANGE_RATE_MAX = config['thresholds']['tan_change_rate_max']
UMA_CHANGE_RATE_MAX = config['thresholds']['uma_change_rate_max']

# 投票設定
STAKE_YEN              = config['betting']['stake_yen']
DRY_RUN                = config['betting']['dry_run']
MAX_BETS_PER_RACE_TAN  = config['betting']['max_bets_per_race_tan']
MAX_BETS_PER_RACE_UMA  = config['betting']['max_bets_per_race_uma']
MAX_TOTAL_BETS_PER_DAY = config['betting']['max_total_bets_per_day']
MIN_ODDS_TO_BUY        = config['betting']['min_odds_to_buy']
MAX_ODDS_TO_BUY        = config['betting']['max_odds_to_buy']

# 運用設定 — data_root はマシン環境を自動判定
def _resolve_data_root() -> Path:
    \"\"\"NAR データルートを解決。優先順:
    1. 環境変数 NAR_DATA_ROOT (最優先、明示指定)
    2. G:/マイドライブ/workspace/nar/data があれば → サブPC と判定
    3. D:/workspace/nar/data があれば → メインPC と判定
    4. C:/Users/ppny9/workspace/nar/data
    5. config.operation.data_root (上記いずれも存在しない場合の fallback)

    サブPC では config に D ドライブのパスが書かれていても **無視して G ドライブを採用** する。
    意図的に D ドライブを参照したい場合は環境変数 NAR_DATA_ROOT で明示指定する。
    \"\"\"
    import os as _os
    env = _os.environ.get('NAR_DATA_ROOT', '')
    if env and Path(env).is_dir():
        return Path(env)
    for p in [
        'G:/マイドライブ/workspace/nar/data',
        'D:/workspace/nar/data',
        'C:/Users/ppny9/workspace/nar/data',
    ]:
        if Path(p).is_dir():
            return Path(p)
    return Path(config.get('operation', {}).get('data_root', 'D:/workspace/nar/data'))


DATA_ROOT          = _resolve_data_root()
POLL_INTERVAL_SEC  = config['operation']['poll_interval_sec']
LOG_DIR            = Path('C:/Users/ppny9/workspace/nar') / config['operation'].get('log_dir', 'logs/')
LOG_DIR.mkdir(parents=True, exist_ok=True)

SNAPSHOT_DIR = DATA_ROOT / 'odds_snapshots'

print(f'CONFIG_PATH: {CONFIG_PATH}')
print(f'T_START → T_END: {T_START} → {T_END}')
print(f'閾値: 単勝 ≤ {TAN_CHANGE_RATE_MAX}% / 馬連 ≤ {UMA_CHANGE_RATE_MAX}%')
print(f'STAKE: {STAKE_YEN}円  /  DRY_RUN: {DRY_RUN}')
print(f'上限: 単勝{MAX_BETS_PER_RACE_TAN}点/race  馬連{MAX_BETS_PER_RACE_UMA}点/race  累計{MAX_TOTAL_BETS_PER_DAY}/day')
print(f'オッズ範囲フィルタ (単勝): {MIN_ODDS_TO_BUY} 〜 {MAX_ODDS_TO_BUY}')
print(f'監視dir: {SNAPSHOT_DIR}')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# シグナル抽出ロジック
# ════════════════════════════════════════════════════════════
def list_snapshot_files(directory: Path, race_id: str = None, label: str = None,
                        kind: str = None) -> list[Path]:
    \"\"\"snapshot ファイルを filter してリスト化。\"\"\"
    files = list(directory.glob('*.csv'))
    out = []
    import re
    for p in files:
        m = re.match(r'(\\d{12,18})_(T\\d+)_(tanfuku|umaren|sanrentan)_', p.name)
        if not m:
            continue
        if race_id and m.group(1) != race_id:
            continue
        if label and m.group(2) != label:
            continue
        if kind and m.group(3) != kind:
            continue
        out.append(p)
    return out


def load_latest_snapshot(race_id: str, label: str, kind: str) -> pd.DataFrame:
    \"\"\"指定 (race_id, label, kind) の最新スナップショット 1 件を読込み。\"\"\"
    files = list_snapshot_files(SNAPSHOT_DIR, race_id=race_id, label=label, kind=kind)
    if not files:
        return pd.DataFrame()
    latest = sorted(files, key=lambda p: p.stat().st_mtime)[-1]
    return pd.read_csv(latest, encoding='utf-8-sig')


def compute_signals_for_race(race_id: str) -> dict:
    \"\"\"指定 race_id について T_START → T_END の change_rate を計算し、シグナル買い目を返す。

    Returns: {'tan_bets': [...], 'uma_bets': [...], 'race_id': ...}
    \"\"\"
    bets = {'race_id': race_id, 'tan_bets': [], 'uma_bets': []}

    # ── 単勝 (tanfuku) ──
    df_t_start = load_latest_snapshot(race_id, T_START, 'tanfuku')
    df_t_end   = load_latest_snapshot(race_id, T_END,   'tanfuku')
    if not df_t_start.empty and not df_t_end.empty:
        m = df_t_start[['umaban', 'odds_tan']].rename(columns={'odds_tan': 'odds_start'}) \\
              .merge(df_t_end[['umaban', 'odds_tan']].rename(columns={'odds_tan': 'odds_end'}),
                     on='umaban')
        m = m.dropna(subset=['odds_start', 'odds_end'])
        m = m[m['odds_start'] > 0]
        m['change_rate'] = (m['odds_end'] - m['odds_start']) / m['odds_start'] * 100
        # 閾値判定 + オッズ範囲フィルタ
        hits = m[(m['change_rate'] <= TAN_CHANGE_RATE_MAX)
                 & (m['odds_end'] >= MIN_ODDS_TO_BUY)
                 & (m['odds_end'] <= MAX_ODDS_TO_BUY)]
        for _, r in hits.iterrows():
            bets['tan_bets'].append({
                'umaban': int(r['umaban']),
                'odds_start': float(r['odds_start']),
                'odds_end': float(r['odds_end']),
                'change_rate': float(r['change_rate']),
            })

    # ── 馬連 (umaren) ──
    df_u_start = load_latest_snapshot(race_id, T_START, 'umaren')
    df_u_end   = load_latest_snapshot(race_id, T_END,   'umaren')
    if not df_u_start.empty and not df_u_end.empty:
        cols_start = ['P1', 'P2', 'odds_umaren']
        cols_end   = ['P1', 'P2', 'odds_umaren']
        m = df_u_start[cols_start].rename(columns={'odds_umaren': 'odds_start'}) \\
              .merge(df_u_end[cols_end].rename(columns={'odds_umaren': 'odds_end'}),
                     on=['P1', 'P2'])
        m = m.dropna(subset=['odds_start', 'odds_end'])
        m = m[m['odds_start'] > 0]
        m['change_rate'] = (m['odds_end'] - m['odds_start']) / m['odds_start'] * 100
        hits = m[m['change_rate'] <= UMA_CHANGE_RATE_MAX]
        for _, r in hits.iterrows():
            bets['uma_bets'].append({
                'P1': int(r['P1']),
                'P2': int(r['P2']),
                'odds_start': float(r['odds_start']),
                'odds_end': float(r['odds_end']),
                'change_rate': float(r['change_rate']),
            })

    return bets
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# race_id からレース情報 (venue, race_num, race_date) を解決
# ════════════════════════════════════════════════════════════
SHUTSUBA_DIR = DATA_ROOT / 'shutsuba'


def resolve_race_info(race_id: str) -> dict:
    \"\"\"race_id → {venue, race_num, race_date, place_id}\"\"\"
    info = {'race_id': race_id}
    # netkeiba 12 桁: YYYY VV MMDD RR
    # 楽天 18 桁:    YYYY MMDD VVVV KK NN RR
    if len(race_id) == 12:
        info['race_date'] = f'{race_id[:4]}-{race_id[6:8]}-{race_id[8:10]}'
        info['race_num']  = int(race_id[10:12])
        # 場名は shutsuba CSV からlookup (netkeiba 12 桁データはここに保存される)
    elif len(race_id) == 18:
        info['race_date'] = f'{race_id[:4]}-{race_id[4:6]}-{race_id[6:8]}'
        info['race_num']  = int(race_id[16:18])
    else:
        info['race_date'] = '?'
        info['race_num']  = 0

    # venue は shutsuba から
    sh_file = SHUTSUBA_DIR / f'{race_id}_shutsuba.csv'
    if sh_file.exists():
        try:
            df_sh = pd.read_csv(sh_file, encoding='utf-8-sig', nrows=1)
            if 'venue' in df_sh.columns and len(df_sh) > 0:
                info['venue'] = str(df_sh['venue'].iloc[0])
            else:
                info['venue'] = '?'
        except Exception:
            info['venue'] = '?'
    else:
        info['venue'] = '?'

    info['place_id'] = spat4.venue_to_place_id(info['venue'])
    return info
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# 投票実行ラッパ
# ════════════════════════════════════════════════════════════
class DailyBetCounter:
    \"\"\"1 日累計の投票数を追跡 (max_total_bets_per_day 超過防止)\"\"\"
    def __init__(self, max_per_day: int):
        self.max = max_per_day
        self.date = date.today()
        self.count = 0

    def reset_if_new_day(self):
        if date.today() != self.date:
            print(f'  [INFO] 日付変更 {self.date} → {date.today()}、カウンタリセット')
            self.date = date.today()
            self.count = 0

    def can_bet(self, n: int = 1) -> bool:
        self.reset_if_new_day()
        return self.count + n <= self.max

    def add(self, n: int = 1):
        self.count += n


daily_counter = DailyBetCounter(MAX_TOTAL_BETS_PER_DAY)


def _format_bet_notification(race_info: dict, tan_bets: list, uma_bets: list,
                              t_start: str, t_end: str) -> tuple:
    \"\"\"買い目通知のメール件名・本文を生成。\"\"\"
    n_tan, n_uma = len(tan_bets), len(uma_bets)
    subj = (f'[NAR L905] 買い目 {race_info.get("venue","?")} '
            f'R{race_info.get("race_num","?")} 単勝{n_tan} 馬連{n_uma}'
            + (' [DRY-RUN]' if DRY_RUN else ''))

    lines = []
    lines.append(f'race_id : {race_info.get("race_id","")}')
    lines.append(f'venue   : {race_info.get("venue","")}  R{race_info.get("race_num","")}')
    lines.append(f'race_date: {race_info.get("race_date","")}')
    lines.append(f'mode    : {"DRY-RUN" if DRY_RUN else "LIVE"}')
    lines.append(f'閾値    : {t_start} → {t_end}  /  単勝 ≤ {TAN_CHANGE_RATE_MAX}%  馬連 ≤ {UMA_CHANGE_RATE_MAX}%')
    lines.append('')

    if tan_bets:
        lines.append(f'■ 単勝 ({n_tan} 点)')
        for b in tan_bets:
            lines.append(f'  馬番 {b["umaban"]:>2}  '
                          f'{t_start}={b["odds_start"]:>7.1f}  →  '
                          f'{t_end}={b["odds_end"]:>7.1f}  '
                          f'({b["change_rate"]:+.2f}%)')
        lines.append('')

    if uma_bets:
        lines.append(f'■ 馬連 ({n_uma} 点)')
        for b in uma_bets:
            lines.append(f'  {b["P1"]:>2}-{b["P2"]:<2}  '
                          f'{t_start}={b["odds_start"]:>7.1f}  →  '
                          f'{t_end}={b["odds_end"]:>7.1f}  '
                          f'({b["change_rate"]:+.2f}%)')
        lines.append('')

    lines.append(f'stake   : {STAKE_YEN}円 × {n_tan + n_uma} 点')
    return subj, '\\n'.join(lines)


def execute_bets_for_race(sess: spat4.Spat4Session, driver, race_info: dict, signals: dict):
    \"\"\"1 レース分のシグナルから実際の投票呼出し。

    change_rate が小さいもの (= 下落率が大きいもの) から優先して採用し、
    kind ごとに max_bets_per_race_* で打ち切る。
    \"\"\"
    # change_rate 昇順 (最も負 = 下落率最大 が先頭) でソート
    tan_sorted = sorted(signals['tan_bets'], key=lambda b: b['change_rate'])
    uma_sorted = sorted(signals['uma_bets'], key=lambda b: b['change_rate'])
    tan_bets = tan_sorted[:MAX_BETS_PER_RACE_TAN]
    uma_bets = uma_sorted[:MAX_BETS_PER_RACE_UMA]

    # ── メール通知 (DRY-RUN/LIVE 両方で送信) ──
    if tan_bets or uma_bets:
        subj, body = _format_bet_notification(race_info, tan_bets, uma_bets, T_START, T_END)
        try:
            sent = send_email_if_configured(subj, body)
            if sent:
                print(f'  [MAIL] 通知送信: {subj}')
            else:
                print(f'  [MAIL] notify 環境変数未設定のためスキップ')
        except Exception as e:
            print(f'  [MAIL ERROR] {e}')

    total_to_bet = len(tan_bets) + len(uma_bets)
    if total_to_bet == 0:
        return

    if not daily_counter.can_bet(total_to_bet):
        print(f'  [SKIP] 日次上限 {MAX_TOTAL_BETS_PER_DAY} に到達 (現在 {daily_counter.count})')
        return

    # 投票ログ
    log_path = LOG_DIR / f'L905_bet_log_{date.today():%Y%m%d}.csv'
    log_exists = log_path.exists()

    with open(log_path, 'a', encoding='utf-8-sig') as f:
        if not log_exists:
            f.write('timestamp,race_id,venue,race_num,kenshu,combo,odds_start,odds_end,change_rate,stake,dry_run,result\\n')

        for b in tan_bets:
            r = sess.place_tan_bet(driver, race_info, umaban=b['umaban'], stake_yen=STAKE_YEN)
            f.write(f'{datetime.now().isoformat(timespec="seconds")},{race_info["race_id"]},'
                    f'{race_info["venue"]},{race_info["race_num"]},tan,{b["umaban"]},'
                    f'{b["odds_start"]},{b["odds_end"]},{b["change_rate"]:.2f},'
                    f'{STAKE_YEN},{DRY_RUN},{r["ok"]}\\n')
            daily_counter.add()

        for b in uma_bets:
            r = sess.place_uma_bet(driver, race_info, p1=b['P1'], p2=b['P2'], stake_yen=STAKE_YEN)
            f.write(f'{datetime.now().isoformat(timespec="seconds")},{race_info["race_id"]},'
                    f'{race_info["venue"]},{race_info["race_num"]},uma,{b["P1"]}-{b["P2"]},'
                    f'{b["odds_start"]},{b["odds_end"]},{b["change_rate"]:.2f},'
                    f'{STAKE_YEN},{DRY_RUN},{r["ok"]}\\n')
            daily_counter.add()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# シグナルの単発テスト (実投票なし)
# ════════════════════════════════════════════════════════════
# 動作確認: 既存の race_id の T-10 → T-3 シグナルを計算
def test_signal_for_race(race_id: str):
    info = resolve_race_info(race_id)
    print(f'race_id={race_id}  venue={info["venue"]}  R{info["race_num"]}  date={info["race_date"]}')
    sig = compute_signals_for_race(race_id)
    print(f'  単勝シグナル: {len(sig["tan_bets"])} 件')
    for b in sig['tan_bets']:
        print(f'    馬番{b["umaban"]}: {b["odds_start"]:.1f} → {b["odds_end"]:.1f}  ({b["change_rate"]:+.1f}%)')
    print(f'  馬連シグナル: {len(sig["uma_bets"])} 件')
    for b in sig['uma_bets']:
        print(f'    {b["P1"]}-{b["P2"]}: {b["odds_start"]:.1f} → {b["odds_end"]:.1f}  ({b["change_rate"]:+.1f}%)')


# 既存 race_id のサンプルで確認
sample_ids = [p.name.split('_')[0] for p in SNAPSHOT_DIR.glob('*_T3_*.csv')]
sample_ids = sorted(set(sample_ids))[-5:]   # 最新 5 件
print(f'=== 最新の T-3 を持つ race_id (last 5) ===')
for rid in sample_ids:
    test_signal_for_race(rid)
    print()
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## メインループ — 監視 + 自動投票

新しい T-3 ファイルを検出するたびに信号を計算し、SPAT4 へ投票する。

**起動前チェックリスト:**
1. `config/spat4_credentials.json` の `dry_run` が `true` になっていることを確認 (初回は必須)
2. SPAT4 残高に投票可能な金額があるか確認
3. `MAX_TOTAL_BETS_PER_DAY` がリスク許容範囲内か確認
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ════════════════════════════════════════════════════════════
# メインループ
# ════════════════════════════════════════════════════════════
def main_loop():
    print(f'=== L905 自動投票監視 開始  {datetime.now():%Y-%m-%d %H:%M:%S} ===')
    print(f'    DRY_RUN={DRY_RUN}  STAKE={STAKE_YEN}円  上限={MAX_TOTAL_BETS_PER_DAY}/day')

    processed_race_ids = set()
    # 起動時、当日分の処理済みレースを log から復元
    log_path = LOG_DIR / f'L905_bet_log_{date.today():%Y%m%d}.csv'
    if log_path.exists():
        try:
            df_log = pd.read_csv(log_path, encoding='utf-8-sig')
            processed_race_ids.update(df_log['race_id'].astype(str).unique())
            daily_counter.count = len(df_log)
            print(f'    log復元: 処理済み {len(processed_race_ids)} race / 累計投票 {daily_counter.count}')
        except Exception as e:
            print(f'    [WARN] log復元失敗: {e}')

    sess = spat4.Spat4Session(config, headless=False)

    # DRY-RUN 時は driver を起動しない (Selenium 不要)
    driver = None
    if not DRY_RUN:
        from contextlib import ExitStack
        stack = ExitStack()
        driver = stack.enter_context(sess.driver())
        if not sess.login(driver):
            print('  [FATAL] SPAT4 login 失敗、終了')
            return
    else:
        print('  [INFO] DRY_RUN モード — Selenium driver 起動せず、シグナルログのみ')

    try:
        while True:
            # 当日生成された T-3 ファイルをスキャン
            today_str = datetime.now().strftime('%Y%m%d')
            t3_files = [p for p in SNAPSHOT_DIR.glob(f'*_T{T_END[1:]}_*_{today_str}-*.csv')]

            for p in t3_files:
                rid = p.name.split('_')[0]
                if rid in processed_race_ids:
                    continue
                race_info = resolve_race_info(rid)
                signals = compute_signals_for_race(rid)
                n_tan, n_uma = len(signals['tan_bets']), len(signals['uma_bets'])
                if n_tan + n_uma == 0:
                    print(f'[{datetime.now():%H:%M:%S}] {rid} {race_info["venue"]} R{race_info["race_num"]}: シグナルなし')
                else:
                    print(f'[{datetime.now():%H:%M:%S}] {rid} {race_info["venue"]} R{race_info["race_num"]}: '
                          f'シグナル 単勝{n_tan} 馬連{n_uma}')
                    execute_bets_for_race(sess, driver, race_info, signals)
                processed_race_ids.add(rid)

            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print(f'[{datetime.now():%H:%M:%S}] Ctrl+C で停止')
    except Exception as e:
        print(f'[FATAL] {e}')
        traceback.print_exc()
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        print(f'=== L905 終了  累計投票 {daily_counter.count} ===')


# 実行するときは以下のコメントを外す:
# main_loop()
print('main_loop() を呼ぶと監視開始。最初は DRY_RUN=true で動作確認すること。')
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 動作確認手順

### 前提
- 取得側 (`scripts/L902_realtime_scraper_v01.py` または `#L901` ノートブック) が常時起動して
  `data/odds_snapshots/` に T-10 / T-3 を書き出していること
- メール通知用の環境変数 (`NOTIFY_SMTP_USER`, `NOTIFY_SMTP_PASS`, `NOTIFY_EMAIL_TO` 等) が設定済みであること
  - JRA #J90x 系と同じ環境変数。未設定でもエラーにはならず、通知だけスキップされる

### ステップ

1. **シグナル検出のみテスト (Selenium 不要)**
   - 上のセル `test_signal_for_race(rid)` で過去レースのシグナル抽出を確認
   - 期待する閾値で買い目が出るか目視確認

2. **メール通知の単独テスト**
   ```python
   from racing_common.notify import send_email_if_configured
   ok = send_email_if_configured('[NAR L905 テスト] 件名',
                                   '本文テスト\\n複数行も可')
   print(f'sent={ok}')   # True なら環境変数 OK
   ```

3. **DRY-RUN フルラン**
   - `config.betting.dry_run = true` のまま `main_loop()` を実行
   - 買い目が出たら以下が同時に発生することを確認:
     - 標準出力に `[DRY-RUN BET]` が表示される
     - `logs/L905_bet_log_YYYYMMDD.csv` に行が追記される
     - メールが届く (件名: `[NAR L905] 買い目 ... [DRY-RUN]`)
   - Selenium driver は起動しないので副作用なし

4. **SPAT4 ログインだけ単独テスト** (Selenium 起動)
   ```python
   sess = spat4.Spat4Session(config, headless=False)
   with sess.driver() as drv:
       sess.login(drv)
       time.sleep(60)  # 60 秒間ブラウザ表示
   ```
   → ブラウザでログイン後の画面が見えれば OK

5. **LIVE 運用前の最終チェック**
   - `dry_run: false` に切替えるのは **最後**
   - 初回 LIVE は `stake_yen: 100`、`max_total_bets_per_day: 3` 程度に絞る
   - 投票結果を SPAT4 サイトで都度目視確認
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
