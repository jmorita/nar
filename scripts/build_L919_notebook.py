"""#L919_odds_analysis_rakuten_v01.ipynb を生成するスクリプト。

#L909 (netkeiba 12 桁データ用) の楽天版。
- race_id 18 桁形式に対応
- 単勝・馬連 (umaren) に加えて **3連単 (sanrentan)** の集計を新規追加
- T-2 (#L901 に追加された) を T_LABELS に含める
"""
from pathlib import Path

import nbformat as nbf

NB_PATH = Path(__file__).resolve().parents[1] / 'notebooks' / '#L919_odds_analysis_rakuten_v01.ipynb'

CELLS: list[tuple[str, str]] = []


def md(src: str):
    CELLS.append(('markdown', src.strip('\n')))


def code(src: str):
    CELLS.append(('code', src.strip('\n')))


# ─────────────────────────────────────────────────────────────────────────────
md("""
# #L919 NAR 楽天競馬 オッズスナップショット分析 v01

`#L909` の **楽天競馬版** (18桁 race_id、3連単対応)。

## 違い (#L909 との比較)
| 項目 | #L909 | #L919 (本ノートブック) |
|---|---|---|
| 取得元 | netkeiba | 楽天競馬 |
| race_id | 12 桁 (`YYYY VV MMDD RR`) | **18 桁** (`YYYY MMDD VVVV KK NN RR`) |
| 取得オッズ | 単勝・複勝・馬連 | + **3連単 (sanrentan)** |
| T_LABELS | T-60〜T-1 (7点) | + **T-2** (8点) |
| venue | race_id 5-6 桁目から直接 | **shutsuba CSV の venue 列から lookup** |

## 分析項目 (#L909 と同等 + 3連単拡張)
1. 取得状況 (日付ごとレース数、欠損)
2. T-X → T-3 / T-1 オッズ変化率の 10 区分集計
3. 区分別の的中・回収統計 — **単勝・馬連・3連単** すべて
4. 閾値スイープによる最適下落率カットオフ
5. T-3 終端 vs T-1 終端 の比較
6. **3連単独自の特性分析** (高配当・低的中率)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
import os
import re
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np
import pandas as pd
import seaborn as sns

warnings.filterwarnings('ignore', category=FutureWarning)
pd.set_option('display.max_rows', 200)
pd.set_option('display.max_columns', 50)
pd.set_option('display.width', 200)

# 日本語フォント (Windows)
mpl.rcParams['font.family'] = ['Meiryo', 'Yu Gothic', 'MS Gothic', 'DejaVu Sans']
mpl.rcParams['axes.unicode_minus'] = False

DATA_ROOT = Path('D:/workspace/nar/data')
SNAPSHOT_DIR = DATA_ROOT / 'odds_snapshots'
RESULT_DIR   = DATA_ROOT / 'results'
SHUTSUBA_DIR = DATA_ROOT / 'shutsuba'

# T-label (L902 / L901 v2: T-2 追加済み)
T_LABELS = ['T60', 'T30', 'T15', 'T10', 'T5', 'T3', 'T2', 'T1']
T_OFFSETS = {'T60': 60, 'T30': 30, 'T15': 15, 'T10': 10, 'T5': 5, 'T3': 3, 'T2': 2, 'T1': 1}

# オッズ種別 (Rakuten 内部表記)
KINDS = ['tanfuku', 'umaren', 'sanrentan']

print(f'SNAPSHOT_DIR: {SNAPSHOT_DIR}  (exists={SNAPSHOT_DIR.exists()})')
print(f'RESULT_DIR  : {RESULT_DIR}    (exists={RESULT_DIR.exists()})')
print(f'SHUTSUBA_DIR: {SHUTSUBA_DIR}  (exists={SHUTSUBA_DIR.exists()})')
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 1. 取得状況の把握

ファイル名形式: `{race_id_18digit}_T{XX}_{kind}_{YYYYMMDD-HHMM}.csv`
- race_id 18 桁: `YYYY MMDD VVVV KK NN RR`
- venue 名は shutsuba CSV から lookup (race_id → venue マッピング作成)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def parse_snapshot_filename(p: Path) -> dict | None:
    m = re.match(r'(\\d{18})_(T\\d+)_(tanfuku|umaren|sanrentan)_(\\d{8})-(\\d{4})\\.csv', p.name)
    if not m:
        return None
    race_id, label, kind, snap_date, snap_hhmm = m.groups()
    return {
        'race_id': race_id,
        'label': label,
        'kind': kind,
        'race_date': f'{race_id[:4]}-{race_id[4:6]}-{race_id[6:8]}',
        'venue_code': race_id[8:12],      # 4桁 venue code (4桁)
        'race_num': int(race_id[16:18]),
        'snapshot_at': pd.to_datetime(f'{snap_date}T{snap_hhmm[:2]}:{snap_hhmm[2:]}'),
        'path': p,
    }


snap_records = [r for r in (parse_snapshot_filename(p) for p in SNAPSHOT_DIR.glob('*.csv')) if r]
df_inv = pd.DataFrame(snap_records)
print(f'18桁 race_id スナップショット: {len(df_inv):,} 件')
if df_inv.empty:
    print('⚠ 楽天 (18桁) データなし — L902 が稼働してデータが溜まるまで本ノートブックの集計部分はスキップしてください')
else:
    print(f'対象 race_id 数: {df_inv["race_id"].nunique():,}')
    print(f'対象日           : {sorted(df_inv["race_date"].unique())}')
df_inv.head()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# venue 名を shutsuba CSV から構築 (race_id → venue マッピング)
venue_map = {}
for p in SHUTSUBA_DIR.glob('*_shutsuba.csv'):
    try:
        # race_id 18桁のみ対象
        m = re.match(r'(\\d{18})_shutsuba\\.csv', p.name)
        if not m:
            continue
        rid = m.group(1)
        df = pd.read_csv(p, encoding='utf-8-sig', nrows=1)
        if 'venue' in df.columns and not df['venue'].empty:
            venue_map[rid] = str(df['venue'].iloc[0])
    except Exception:
        pass
print(f'venue マッピング: {len(venue_map)} race')

if not df_inv.empty:
    df_inv['venue'] = df_inv['race_id'].map(venue_map).fillna('?')
    print(df_inv[['race_id', 'race_date', 'venue', 'label', 'kind']].head(10))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 日付 × 場所ごとのレース数 / 日付 × T-label カバレッジ
if not df_inv.empty:
    by_date_venue = (df_inv.groupby(['race_date', 'venue'])['race_id']
                     .nunique().unstack(fill_value=0))
    print('=== 日付×場所 レース数 ===')
    display(by_date_venue.style.background_gradient(cmap='Blues'))

    for kind, cmap in [('tanfuku', 'Blues'), ('umaren', 'Greens'), ('sanrentan', 'Oranges')]:
        cov = (df_inv[df_inv['kind'] == kind]
               .groupby(['race_date', 'label'])['race_id'].nunique()
               .unstack(fill_value=0))
        cov = cov.reindex(columns=T_LABELS, fill_value=0)
        print(f'=== {kind}: 日付×T-label のレース数 ===')
        display(cov.style.background_gradient(cmap=cmap))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── T-X & T-3 / T-X & T-1 が両方揃ったレース数の確認 (有効レース母集団)
if not df_inv.empty:
    rows = []
    for kind in KINDS:
        sub = df_inv[df_inv['kind'] == kind]
        labels_per_race = sub.groupby('race_id')['label'].apply(set)
        n_total = len(labels_per_race)
        rows.append({
            'kind': kind,
            '全レース': n_total,
            'T-10 & T-3 揃い': int((labels_per_race.apply(lambda s: 'T10' in s and 'T3' in s)).sum()),
            'T-10 & T-1 揃い': int((labels_per_race.apply(lambda s: 'T10' in s and 'T1' in s)).sum()),
            'T-3 & T-1 揃い':  int((labels_per_race.apply(lambda s: 'T3' in s and 'T1' in s)).sum()),
        })
    display(pd.DataFrame(rows))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 2. オッズスナップショット読込

各 kind ごとに wide テーブルを作成。
- `tanfuku`: index=(race_id, umaban), columns=label, values=odds_tan
- `umaren`:  index=(race_id, P1, P2), columns=label, values=odds_umaren
- `sanrentan`: index=(race_id, P1, P2, P3), columns=label, values=odds_sanrentan
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def load_snapshots(kind: str, labels: list[str]) -> pd.DataFrame:
    if df_inv.empty:
        return pd.DataFrame()
    target = df_inv[(df_inv['kind'] == kind) & (df_inv['label'].isin(labels))]
    dfs = []
    for p in target['path']:
        try:
            dfs.append(pd.read_csv(p, encoding='utf-8-sig'))
        except Exception as e:
            print(f'  [WARN] {p.name}: {e}')
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def build_wide(kind: str, labels: list[str]) -> pd.DataFrame:
    '''指定 kind / labels の wide DataFrame を返す。'''
    raw = load_snapshots(kind, labels)
    if raw.empty:
        return pd.DataFrame()
    if kind == 'tanfuku':
        key_cols, odds_col = ['race_id', 'umaban'], 'odds_tan'
    elif kind == 'umaren':
        key_cols, odds_col = ['race_id', 'P1', 'P2'], 'odds_umaren'
    elif kind == 'sanrentan':
        key_cols, odds_col = ['race_id', 'P1', 'P2', 'P3'], 'odds_sanrentan'
    else:
        return pd.DataFrame()
    dedup = (raw.sort_values('snapshot_time')
                .drop_duplicates(key_cols + ['label'], keep='last'))
    wide = (dedup.pivot_table(index=key_cols, columns='label',
                              values=odds_col, aggfunc='last')
                 .reset_index())
    return wide
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 3. 結果・払戻データの結合

`{race_id}_result.csv` から 1着・2着・3着 を、`{race_id}_payout.csv` から
単勝・馬連・**3連単** の払戻を取得。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 結果
res_files = sorted(RESULT_DIR.glob('*_result.csv'))
# 18桁 race_id のみ対象
res_files = [p for p in res_files if re.match(r'\\d{18}_result\\.csv', p.name)]
res_dfs = [pd.read_csv(p, encoding='utf-8-sig') for p in res_files]
df_res = pd.concat(res_dfs, ignore_index=True) if res_dfs else pd.DataFrame()
print(f'result 行数: {len(df_res):,}  / 対象 race_id: {df_res["race_id"].nunique() if not df_res.empty else 0}')

if not df_res.empty:
    df_res['rank'] = pd.to_numeric(df_res['rank'], errors='coerce').astype('Int64')
    df_res['umaban'] = pd.to_numeric(df_res['umaban'], errors='coerce').astype('Int64')
    df_res_clean = df_res.dropna(subset=['rank', 'umaban'])
    # 1着 / 2着 / 3着 馬番を race_id ごとに抽出
    winners = (df_res_clean[df_res_clean['rank'].isin([1, 2, 3])]
                .pivot(index='race_id', columns='rank', values='umaban')
                .rename(columns={1: 'win_1st', 2: 'win_2nd', 3: 'win_3rd'})
                .reset_index())
    print(f'1-3着 揃ったレース: {winners.dropna(subset=["win_1st","win_2nd","win_3rd"]).shape[0]:,}')
else:
    df_res_clean = pd.DataFrame()
    winners = pd.DataFrame()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 払戻
pay_files = sorted(RESULT_DIR.glob('*_payout.csv'))
pay_files = [p for p in pay_files if re.match(r'\\d{18}_payout\\.csv', p.name)]
pay_dfs = [pd.read_csv(p, encoding='utf-8-sig') for p in pay_files]
df_pay = pd.concat(pay_dfs, ignore_index=True) if pay_dfs else pd.DataFrame()
print(f'payout 行数: {len(df_pay):,}')

# 単勝
if not df_pay.empty:
    df_pay_tan = df_pay[df_pay['kenshu'] == '単勝'].copy()
    df_pay_tan['win_umaban'] = pd.to_numeric(df_pay_tan['combo'], errors='coerce').astype('Int64')
    df_pay_tan['payout_tan'] = pd.to_numeric(df_pay_tan['payout'], errors='coerce')
    df_pay_tan = df_pay_tan[['race_id', 'win_umaban', 'payout_tan']]

    # 馬連
    df_pay_uma = df_pay[df_pay['kenshu'] == '馬連'].copy()
    def _parse_uma(c):
        if not isinstance(c, str): return (pd.NA, pd.NA)
        parts = re.findall(r'\\d+', c)
        if len(parts) < 2: return (pd.NA, pd.NA)
        return (min(int(parts[0]), int(parts[1])), max(int(parts[0]), int(parts[1])))
    pairs = df_pay_uma['combo'].apply(_parse_uma)
    df_pay_uma['win_P1'] = pairs.apply(lambda t: t[0]).astype('Int64')
    df_pay_uma['win_P2'] = pairs.apply(lambda t: t[1]).astype('Int64')
    df_pay_uma['payout_uma'] = pd.to_numeric(df_pay_uma['payout'], errors='coerce')
    df_pay_uma = df_pay_uma[['race_id', 'win_P1', 'win_P2', 'payout_uma']]

    # 3連単 (combo = "1-10-8" など順序保持)
    df_pay_san = df_pay[df_pay['kenshu'] == '3連単'].copy()
    def _parse_san(c):
        if not isinstance(c, str): return (pd.NA, pd.NA, pd.NA)
        parts = re.findall(r'\\d+', c)
        if len(parts) < 3: return (pd.NA, pd.NA, pd.NA)
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    trips = df_pay_san['combo'].apply(_parse_san)
    df_pay_san['win_P1'] = trips.apply(lambda t: t[0]).astype('Int64')
    df_pay_san['win_P2'] = trips.apply(lambda t: t[1]).astype('Int64')
    df_pay_san['win_P3'] = trips.apply(lambda t: t[2]).astype('Int64')
    df_pay_san['payout_san'] = pd.to_numeric(df_pay_san['payout'], errors='coerce')
    df_pay_san = df_pay_san[['race_id', 'win_P1', 'win_P2', 'win_P3', 'payout_san']]

    print(f'単勝 payout: {len(df_pay_tan):,} レース')
    print(f'馬連 payout: {len(df_pay_uma):,} レース')
    print(f'3連単 payout: {len(df_pay_san):,} レース')
else:
    df_pay_tan = df_pay_uma = df_pay_san = pd.DataFrame()
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 4. パターン構築関数 (TXX → end_label) — 単勝・馬連・3連単 共通

`build_pattern(start_label, kind, end_label)` で change_rate + hit + payout を返す。
有効レースは (start ∩ end ∩ payout) を動的に計算 (#L909 と同じ方針)。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def build_pattern(start_label: str, kind: str, end_label: str = 'T3') -> pd.DataFrame:
    '''指定 (start, end) の change_rate × hit × payout を返す。'''
    raw = load_snapshots(kind, [start_label, end_label])
    if raw.empty:
        return pd.DataFrame()

    if kind == 'tanfuku':
        key_cols, odds_col = ['race_id', 'umaban'], 'odds_tan'
        pay_df = df_pay_tan.dropna(subset=['win_umaban', 'payout_tan']) if not df_pay_tan.empty else pd.DataFrame()
        pay_col = 'payout_tan'
    elif kind == 'umaren':
        key_cols, odds_col = ['race_id', 'P1', 'P2'], 'odds_umaren'
        pay_df = df_pay_uma.dropna(subset=['win_P1', 'win_P2', 'payout_uma']) if not df_pay_uma.empty else pd.DataFrame()
        pay_col = 'payout_uma'
    elif kind == 'sanrentan':
        key_cols, odds_col = ['race_id', 'P1', 'P2', 'P3'], 'odds_sanrentan'
        pay_df = df_pay_san.dropna(subset=['win_P1', 'win_P2', 'win_P3', 'payout_san']) if not df_pay_san.empty else pd.DataFrame()
        pay_col = 'payout_san'
    else:
        return pd.DataFrame()

    if pay_df.empty:
        return pd.DataFrame()

    # 動的 valid_races
    races_start = set(raw[raw['label'] == start_label]['race_id'])
    races_end   = set(raw[raw['label'] == end_label]['race_id'])
    races_pay   = set(pay_df['race_id'])
    valid_races = races_start & races_end & races_pay
    if not valid_races:
        return pd.DataFrame()

    dedup = (raw.sort_values('snapshot_time')
                .drop_duplicates(key_cols + ['label'], keep='last'))
    wide = (dedup.pivot_table(index=key_cols, columns='label',
                              values=odds_col, aggfunc='last')
                 .reset_index())
    if start_label not in wide.columns or end_label not in wide.columns:
        return pd.DataFrame()
    wide = wide.dropna(subset=[start_label, end_label])
    wide = wide[wide['race_id'].isin(valid_races)]
    wide['change_rate'] = (wide[end_label] - wide[start_label]) / wide[start_label] * 100
    wide = wide[np.isfinite(wide['change_rate'])]

    merged = wide.merge(pay_df, on='race_id', how='inner')
    if kind == 'tanfuku':
        merged['hit'] = (merged['umaban'] == merged['win_umaban']).astype(int)
    elif kind == 'umaren':
        merged['hit'] = ((merged['P1'] == merged['win_P1'])
                         & (merged['P2'] == merged['win_P2'])).astype(int)
    else:  # sanrentan: 順序保持
        merged['hit'] = ((merged['P1'] == merged['win_P1'])
                         & (merged['P2'] == merged['win_P2'])
                         & (merged['P3'] == merged['win_P3'])).astype(int)
    merged['payout'] = np.where(merged['hit'] == 1, merged[pay_col], 0)
    return merged
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 5. 区分集計関数 + 閾値スイープ
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def bin_stats(df: pd.DataFrame, value_col: str = 'change_rate', n_bins: int = 10) -> pd.DataFrame:
    sub = df[np.isfinite(df[value_col])].copy()
    if sub.empty:
        return pd.DataFrame()
    sub['bin'] = pd.qcut(sub[value_col], q=n_bins, duplicates='drop')
    agg = (sub.groupby('bin', observed=True)
             .agg(母数=('hit', 'size'),
                  的中数=('hit', 'sum'),
                  回収額=('payout', 'sum'),
                  平均配当=('payout', lambda s: s[s > 0].mean() if (s > 0).any() else np.nan))
             .reset_index())
    agg['区分下限(%)'] = agg['bin'].apply(lambda x: float(x.left)).round(2)
    agg['区分上限(%)'] = agg['bin'].apply(lambda x: float(x.right)).round(2)
    agg['的中率(%)']   = (agg['的中数'] / agg['母数'] * 100).round(2)
    agg['回収率(%)']   = (agg['回収額'] / (agg['母数'] * 100) * 100).round(1)
    agg['平均配当(¥)'] = agg['平均配当'].round(0)
    return agg[['区分下限(%)', '区分上限(%)', '母数', '的中数', '的中率(%)',
                '回収額', '回収率(%)', '平均配当(¥)']]


def style_stats(df: pd.DataFrame):
    def color_rec(v):
        if pd.isna(v): return ''
        if v >= 120: return 'background-color:#2e7d32;color:#fff'
        if v >= 100: return 'background-color:#66bb6a;color:#000'
        if v >=  80: return 'background-color:#a5d6a7;color:#000'
        if v >=  60: return 'background-color:#fff176;color:#000'
        return 'background-color:#e57373;color:#000'

    def color_hit(v):
        if pd.isna(v): return ''
        if v >= 30: return 'background-color:#2e7d32;color:#fff'
        if v >= 20: return 'background-color:#66bb6a;color:#000'
        if v >= 15: return 'background-color:#a5d6a7;color:#000'
        if v >= 10: return 'background-color:#fff176;color:#000'
        return 'background-color:#ef9a9a;color:#000'

    return (df.style
              .map(color_hit, subset=['的中率(%)'])
              .map(color_rec, subset=['回収率(%)'])
              .format({'回収額': '{:,.0f}', '平均配当(¥)': '{:,.0f}'}))


def threshold_sweep(df: pd.DataFrame, thresholds: np.ndarray) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for th in thresholds:
        sub = df[df['change_rate'] <= th]
        n = len(sub)
        if n == 0:
            rows.append({'threshold': th, 'n': 0, 'hit_rate': np.nan, 'roi': np.nan, 'pl': 0})
            continue
        rev = sub['payout'].sum()
        rows.append({
            'threshold': th, 'n': n,
            'hit_rate': sub['hit'].sum() / n * 100,
            'roi':  rev / (n * 100) * 100,
            'pl':   int(rev - n * 100),
        })
    return pd.DataFrame(rows)


THRESHOLDS = np.arange(-50, 1, 1.0)
MIN_N = 20
START_LABELS = ['T60', 'T30', 'T15', 'T10', 'T5']
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 6. TXX → T-3 全パターンの集計 (単勝・馬連・**3連単**)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# パターン × kind を一括計算
pat = {kind: {s: build_pattern(s, kind, 'T3') for s in START_LABELS} for kind in KINDS}

summary = pd.DataFrame([
    {
        'pattern': f'{s}→T3',
        **{f'{kind}_n': len(pat[kind][s]) for kind in KINDS},
        **{f'{kind}_的中': int(pat[kind][s]['hit'].sum()) if not pat[kind][s].empty else 0
           for kind in KINDS},
    }
    for s in START_LABELS
])
print('=== TXX → T-3 各パターンの母数 ===')
display(summary)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 各 kind × start_label の 10 区分テーブル
for kind in KINDS:
    print(f'\\n========== {kind} ==========')
    for s in START_LABELS:
        df = pat[kind][s]
        if df.empty:
            print(f'  [SKIP] {kind} {s}→T3: データなし')
            continue
        stats = bin_stats(df, 'change_rate', n_bins=10)
        if stats.empty:
            continue
        print(f'=== {kind}: {s} → T-3   n={len(df)} / 的中={int(df["hit"].sum())} ===')
        display(style_stats(stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 7. 閾値スイープ + 最適閾値 (kind 別)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
sweep = {kind: {s: threshold_sweep(pat[kind][s], THRESHOLDS) for s in START_LABELS}
         for kind in KINDS}


def plot_sweep(sweeps: dict, title_prefix: str, end_label: str = 'T3', min_n: int = 20):
    cmap = plt.colormaps['viridis']
    colors = [cmap(i / max(1, len(START_LABELS) - 1)) for i in range(len(START_LABELS))]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ax_roi, ax_pl, ax_n = axes

    for color, start in zip(colors, START_LABELS):
        s = sweeps.get(start, pd.DataFrame())
        if s.empty:
            continue
        ok = s['n'] >= min_n
        tag = f'{start}→{end_label}'
        ax_roi.plot(s.loc[ok, 'threshold'], s.loc[ok, 'roi'], 'o-', color=color, label=tag, ms=4)
        ax_pl .plot(s.loc[ok, 'threshold'], s.loc[ok, 'pl'],  'o-', color=color, label=tag, ms=4)
        ax_n  .plot(s['threshold'], s['n'], '-', color=color, label=tag)

    ax_roi.axhline(100, color='gray', linestyle='--', alpha=0.5, label='損益分岐 100%')
    ax_pl.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax_n.axhline(min_n, color='red', linestyle=':', alpha=0.6, label=f'min_n={min_n}')

    for ax, ylabel, title in [
        (ax_roi, '回収率 (%)', f'{title_prefix}: 回収率 vs 閾値'),
        (ax_pl,  'P/L (¥)',    f'{title_prefix}: 累積損益 vs 閾値'),
        (ax_n,   '母数 n',     f'{title_prefix}: サンプル数 vs 閾値'),
    ]:
        ax.set_xlabel('change_rate 閾値 (%) — 閾値以下を買う')
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(loc='best', fontsize=9)
        ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()


for kind in KINDS:
    plot_sweep(sweep[kind], kind, end_label='T3', min_n=MIN_N)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def best_thresholds(sweeps: dict, min_n: int = 20, end_label: str = 'T3') -> pd.DataFrame:
    rows = []
    for start, s in sweeps.items():
        if s.empty:
            continue
        ok = s[(s['n'] >= min_n) & s['roi'].notna()]
        if ok.empty:
            continue
        best_roi = ok.loc[ok['roi'].idxmax()]
        best_pl  = ok.loc[ok['pl'].idxmax()]
        rows.append({
            'pattern': f'{start}→{end_label}',
            '[ROI最大] 閾値(%)':  float(best_roi['threshold']),
            '[ROI最大] 母数':      int(best_roi['n']),
            '[ROI最大] 的中率(%)': round(float(best_roi['hit_rate']), 2),
            '[ROI最大] 回収率(%)': round(float(best_roi['roi']), 1),
            '[ROI最大] P/L(¥)':    int(best_roi['pl']),
            '[PL最大] 閾値(%)':   float(best_pl['threshold']),
            '[PL最大] 母数':       int(best_pl['n']),
            '[PL最大] 的中率(%)':  round(float(best_pl['hit_rate']), 2),
            '[PL最大] 回収率(%)':  round(float(best_pl['roi']), 1),
            '[PL最大] P/L(¥)':     int(best_pl['pl']),
        })
    return pd.DataFrame(rows)


for kind in KINDS:
    bt = best_thresholds(sweep[kind], min_n=MIN_N, end_label='T3')
    print(f'\\n=== {kind}: パターン別 最適閾値 (T-3 終端, min_n={MIN_N}) ===')
    display(bt)
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 8. 終端を T-1 / T-2 に変えた検証

L902 (= 楽天) は T-2 も取得しているため、`T-2` 終端も比較対象に加える。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
END_LABELS_OTHER = ['T2', 'T1']
START_LABELS_T1 = ['T60', 'T30', 'T15', 'T10', 'T5', 'T3']
START_LABELS_T2 = ['T60', 'T30', 'T15', 'T10', 'T5', 'T3']

pat_other = {
    end_lbl: {
        kind: {s: build_pattern(s, kind, end_lbl) for s in (START_LABELS_T1 if end_lbl == 'T1' else START_LABELS_T2)}
        for kind in KINDS
    }
    for end_lbl in END_LABELS_OTHER
}

sweep_other = {
    end_lbl: {
        kind: {s: threshold_sweep(pat_other[end_lbl][kind][s], THRESHOLDS)
               for s in pat_other[end_lbl][kind]}
        for kind in KINDS
    }
    for end_lbl in END_LABELS_OTHER
}

for end_lbl in END_LABELS_OTHER:
    print(f'\\n############ 終端 = {end_lbl} ############')
    for kind in KINDS:
        bt = best_thresholds(sweep_other[end_lbl][kind], min_n=MIN_N, end_label=end_lbl)
        print(f'\\n=== {kind}: 最適閾値 ({end_lbl} 終端) ===')
        display(bt)
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 9. 終端 T-3 / T-2 / T-1 比較 (kind 別)

同じ start_label で終端だけ変えたときの 最適 P/L を横並びで比較。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def compare_endpoints_3way(kind: str) -> pd.DataFrame:
    bt_t3 = best_thresholds(sweep[kind],       min_n=MIN_N, end_label='T3')
    bt_t2 = best_thresholds(sweep_other['T2'][kind], min_n=MIN_N, end_label='T2')
    bt_t1 = best_thresholds(sweep_other['T1'][kind], min_n=MIN_N, end_label='T1')

    def _start(p): return p.split('→')[0]
    def _index(df): return df.assign(start=df['pattern'].apply(_start)).set_index('start') if not df.empty else df

    t3, t2, t1 = _index(bt_t3), _index(bt_t2), _index(bt_t1)
    starts = sorted(set(t3.index) | set(t2.index) | set(t1.index),
                    key=lambda s: int(s.lstrip('T')), reverse=True)
    rows = []
    for s in starts:
        row = {'start': s}
        for tag, src in [('T3', t3), ('T2', t2), ('T1', t1)]:
            if s in src.index:
                row[f'{tag}:閾値(%)']  = src.loc[s, '[PL最大] 閾値(%)']
                row[f'{tag}:母数']     = int(src.loc[s, '[PL最大] 母数'])
                row[f'{tag}:回収率(%)']= src.loc[s, '[PL最大] 回収率(%)']
                row[f'{tag}:P/L(¥)']  = int(src.loc[s, '[PL最大] P/L(¥)'])
            else:
                row[f'{tag}:閾値(%)']  = np.nan
                row[f'{tag}:母数']     = 0
                row[f'{tag}:回収率(%)']= np.nan
                row[f'{tag}:P/L(¥)']  = 0
        rows.append(row)
    return pd.DataFrame(rows)


for kind in KINDS:
    print(f'\\n=== {kind}: T-3 / T-2 / T-1 終端 最適 P/L 比較 ===')
    display(compare_endpoints_3way(kind))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 10. 3連単 独自の特性分析

3連単は組合せ数が圧倒的に多い (馬数×(馬数-1)×(馬数-2)) ため、ベース的中率は 1% 未満になる。
このセクションでは 3連単に固有の問題 (高配当・低的中率) を確認する。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 3連単の閾値別 回収率 / 平均配当の特性を確認
san_pat_t3 = pat.get('sanrentan', {})
all_rows = []
for s, df in san_pat_t3.items():
    if df.empty:
        continue
    df = df.copy()
    df['start_label'] = s
    all_rows.append(df)
if all_rows:
    san_all = pd.concat(all_rows, ignore_index=True)
    print(f'3連単 全パターン累計: {len(san_all):,} 行 / {san_all["race_id"].nunique()} レース')
    print('=== 全体統計 ===')
    print(f'  ベース的中率: {san_all["hit"].mean() * 100:.3f} %')
    print(f'  ベース回収率: {san_all["payout"].sum() / (len(san_all) * 100) * 100:.1f} %')
    print(f'  的中時の平均配当: {san_all.loc[san_all["hit"]==1, "payout"].mean():.0f} ¥' if (san_all['hit']==1).any() else '  (的中ゼロ)')

    # 閾値別 ROI 推移 (start_label を集約)
    bins_threshold = [-50, -40, -30, -20, -10, -5, 0, 5, 10, 20, 30, 50, 100, 1000]
    san_all['change_bin'] = pd.cut(san_all['change_rate'], bins=bins_threshold)
    agg = (san_all.groupby('change_bin', observed=True)
                 .agg(母数=('hit', 'size'),
                      的中数=('hit', 'sum'),
                      回収額=('payout', 'sum'),
                      平均配当=('payout', lambda s: s[s>0].mean() if (s>0).any() else np.nan))
                 .reset_index())
    agg['的中率(%)'] = (agg['的中数'] / agg['母数'] * 100).round(3)
    agg['回収率(%)'] = (agg['回収額'] / (agg['母数'] * 100) * 100).round(1)
    print('\\n=== 3連単 全 start_label 集約 change_rate ビン別 ===')
    display(agg)
else:
    print('3連単データなし')
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## まとめ

L909 (netkeiba) と L919 (楽天) のすみ分け:
- **L909**: 過去 netkeiba 12 桁データ蓄積分の継続観察用 (歴史データの集計)
- **L919**: L902 稼働後の楽天 18 桁データ用 (3連単含む将来運用基盤)

> L902 が 1 週間程度稼働した時点で本ノートブックを実行し、3連単シグナルの有効性 (特に高配当 × 低的中率の妥当性) を確認する。
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
