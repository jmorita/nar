"""#L909_odds_analysis_v01.ipynb を生成する補助スクリプト。

セル本体は raw 文字列で記述し、nbformat で .ipynb に組み立てる。
ノートブックの中身を編集したい場合は、このスクリプトを編集して再実行する。
"""
from pathlib import Path

import nbformat as nbf

NB_PATH = Path(__file__).resolve().parents[1] / 'notebooks' / '#L909_odds_analysis_v01.ipynb'


# ============================================================================
# 各セル
# ============================================================================

CELLS: list[tuple[str, str]] = []   # (cell_type, source) のリスト


def md(src: str):
    CELLS.append(('markdown', src.strip('\n')))


def code(src: str):
    CELLS.append(('code', src.strip('\n')))


# ─────────────────────────────────────────────────────────────────────────────
md("""
# #L909 NAR オッズスナップショット分析 v01

NAR (地方競馬) のオッズスナップショットデータを分析する。

## 分析項目
1. **取得状況**: 日付ごとのレース数・T-label 別カバレッジ・欠損状況
2. **変化率**: T-10 → T-3 のオッズ変化率を単勝・馬連で 10 区分化
3. **区分別集計**: 変化率区分ごとの母数・的中数・的中率・回収額・回収率・平均配当
4. **勝ち馬の単勝オッズ推移**: 勝ち馬と非勝ち馬の T-60〜T-1 オッズ推移比較

## 入出力
- 入力: `D:/workspace/nar/data/{odds_snapshots,results}`
- 結果ベース: 各レースの `*_result.csv` (確定着順 + 確定オッズ) と `*_payout.csv` (払戻)
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

# データルート (D ドライブに移動済み)
DATA_ROOT = Path('D:/workspace/nar/data')
SNAPSHOT_DIR = DATA_ROOT / 'odds_snapshots'
RESULT_DIR   = DATA_ROOT / 'results'
SHUTSUBA_DIR = DATA_ROOT / 'shutsuba'

# NAR 場所コード
NAR_VENUE_NAME = {
    '30': '門別', '35': '盛岡', '36': '水沢', '42': '浦和', '43': '船橋',
    '44': '大井', '45': '川崎', '46': '金沢', '47': '笠松', '48': '名古屋',
    '50': '園田', '51': '姫路', '54': '高知', '55': '佐賀', '65': '帯広',
}

# 取得 T-label (#L901 の SNAPSHOT_CONFIG と同じ順序)
T_LABELS = ['T60', 'T30', 'T15', 'T10', 'T5', 'T3', 'T1']
T_OFFSETS = {'T60': 60, 'T30': 30, 'T15': 15, 'T10': 10, 'T5': 5, 'T3': 3, 'T1': 1}

print(f'SNAPSHOT_DIR: {SNAPSHOT_DIR}  (exists={SNAPSHOT_DIR.exists()})')
print(f'RESULT_DIR  : {RESULT_DIR}    (exists={RESULT_DIR.exists()})')
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 1. 取得状況の把握

各 snapshot ファイル名を解析して `(race_id, T-label, kind)` のインベントリ DataFrame を作る。

ファイル名形式: `{race_id}_T{XX}_{tanfuku|umaren}_{YYYYMMDD-HHMM}.csv`
- `race_id` (12桁): `YYYY VV MMDD RR`  (年 + 場 + 月日 + R)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def parse_snapshot_filename(p: Path) -> dict | None:
    m = re.match(r'(\\d{12})_(T\\d+)_(tanfuku|umaren)_(\\d{8})-(\\d{4})\\.csv', p.name)
    if not m:
        return None
    race_id, label, kind, snap_date, snap_hhmm = m.groups()
    return {
        'race_id': race_id,
        'label': label,
        'kind': kind,
        'race_date': f'{race_id[:4]}-{race_id[6:8]}-{race_id[8:10]}',
        'venue_code': race_id[4:6],
        'venue': NAR_VENUE_NAME.get(race_id[4:6], race_id[4:6]),
        'race_num': int(race_id[10:12]),
        'snapshot_at': pd.to_datetime(f'{snap_date}T{snap_hhmm[:2]}:{snap_hhmm[2:]}'),
        'path': p,
    }


snap_records = [r for r in (parse_snapshot_filename(p) for p in SNAPSHOT_DIR.glob('*.csv')) if r]
df_inv = pd.DataFrame(snap_records)
print(f'スナップショットファイル: {len(df_inv):,} 件')
print(f'対象 race_id 数        : {df_inv["race_id"].nunique():,}')
print(f'対象日                  : {sorted(df_inv["race_date"].unique())}')
df_inv.head()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 日付 × 場所ごとのレース数
by_date_venue = (df_inv.groupby(['race_date', 'venue'])['race_id']
                 .nunique().unstack(fill_value=0))
print('=== 日付×場所 レース数 ===')
display(by_date_venue.style.background_gradient(cmap='Blues'))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 日付 × T-label カバレッジ (tanfuku ベース)
cov_tan = (df_inv[df_inv['kind'] == 'tanfuku']
           .groupby(['race_date', 'label'])['race_id'].nunique()
           .unstack(fill_value=0))
cov_tan = cov_tan.reindex(columns=T_LABELS, fill_value=0)
print('=== tanfuku: 日付×T-label のレース数 ===')
display(cov_tan.style.background_gradient(cmap='Blues'))

cov_uma = (df_inv[df_inv['kind'] == 'umaren']
           .groupby(['race_date', 'label'])['race_id'].nunique()
           .unstack(fill_value=0))
cov_uma = cov_uma.reindex(columns=T_LABELS, fill_value=0)
print('=== umaren: 日付×T-label のレース数 ===')
display(cov_uma.style.background_gradient(cmap='Greens'))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── レース単位の取得カバレッジ (T-label が何個取れたか)
race_label_count = (df_inv[df_inv['kind'] == 'tanfuku']
                    .groupby('race_id')['label'].nunique()
                    .rename('n_labels'))
print(f'tanfuku の T-label 平均取得数: {race_label_count.mean():.2f} / {len(T_LABELS)}')

# 取得 T-label 数の分布
print('=== 取得 T-label 数の分布 (tanfuku) ===')
print(race_label_count.value_counts().sort_index().to_string())

# T-10, T-3 が両方揃っているレース数 (本分析の母集団)
both_ok_tan = (df_inv[(df_inv['kind'] == 'tanfuku') & (df_inv['label'].isin(['T10', 'T3']))]
               .groupby('race_id')['label'].nunique() == 2)
n_both = int(both_ok_tan.sum())
n_total = df_inv['race_id'].nunique()
print(f'T-10 & T-3 両方ある race: {n_both} / {n_total}  ({n_both/n_total*100:.1f}%)')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── カバレッジヒートマップ (race × T-label, tanfuku)
pv = (df_inv[df_inv['kind'] == 'tanfuku']
      .assign(has=1)
      .pivot_table(index='race_id', columns='label', values='has', aggfunc='max', fill_value=0)
      .reindex(columns=T_LABELS, fill_value=0)
      .sort_index())

fig, ax = plt.subplots(figsize=(10, max(4, len(pv) * 0.05)))
sns.heatmap(pv, cmap='YlGnBu', cbar=False, ax=ax,
            xticklabels=T_LABELS, yticklabels=False)
ax.set_title(f'取得カバレッジ (tanfuku): {len(pv)} レース × {len(T_LABELS)} T-label')
ax.set_xlabel('T-label')
ax.set_ylabel('race_id (alphabetical)')
plt.tight_layout()
plt.show()
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 2. T-10 → T-3 オッズ変化率の算出と区分化

### 算式
- 単勝: `change_rate_tan = (odds_T3 - odds_T10) / odds_T10 * 100`  (%)
- 馬連: `change_rate_uma = (odds_T3 - odds_T10) / odds_T10 * 100`  (%)

> 正値 = オッズ上昇 (人気↓)、負値 = オッズ下降 (人気↑、いわゆるスマートマネー流入)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def load_snapshots(kind: str, labels: list[str]) -> pd.DataFrame:
    target = df_inv[(df_inv['kind'] == kind) & (df_inv['label'].isin(labels))]
    dfs = []
    for p in target['path']:
        try:
            dfs.append(pd.read_csv(p, encoding='utf-8-sig'))
        except Exception as e:
            print(f'  [WARN] {p.name}: {e}')
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


# tanfuku (単勝) T-10 / T-3
df_tan_raw = load_snapshots('tanfuku', ['T10', 'T3'])
print(f'tanfuku T10/T3 行数: {len(df_tan_raw):,}')

# 1 race × 1 umaban × 1 label に対して 1 行 (重複時は最新を採用)
df_tan = (df_tan_raw
          .sort_values('snapshot_time')
          .drop_duplicates(['race_id', 'umaban', 'label'], keep='last'))

tan_wide = (df_tan.pivot_table(
                index=['race_id', 'umaban'], columns='label',
                values='odds_tan', aggfunc='last')
            .rename(columns=lambda c: f'odds_{c.lower()}')
            .reset_index())

# 両方揃っているレコードに絞る
tan_wide = tan_wide.dropna(subset=['odds_t10', 'odds_t3'])
tan_wide['change_rate'] = (tan_wide['odds_t3'] - tan_wide['odds_t10']) / tan_wide['odds_t10'] * 100
print(f'tanfuku 有効レコード: {len(tan_wide):,}')
print(f'change_rate(%) stats:')
display(tan_wide['change_rate'].describe(percentiles=[.05, .1, .25, .5, .75, .9, .95]))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# umaren (馬連) T-10 / T-3
df_uma_raw = load_snapshots('umaren', ['T10', 'T3'])
print(f'umaren T10/T3 行数: {len(df_uma_raw):,}')

df_uma = (df_uma_raw
          .sort_values('snapshot_time')
          .drop_duplicates(['race_id', 'P1', 'P2', 'label'], keep='last'))

uma_wide = (df_uma.pivot_table(
                index=['race_id', 'P1', 'P2'], columns='label',
                values='odds_umaren', aggfunc='last')
            .rename(columns=lambda c: f'odds_{c.lower()}')
            .reset_index())
uma_wide = uma_wide.dropna(subset=['odds_t10', 'odds_t3'])
uma_wide['change_rate'] = (uma_wide['odds_t3'] - uma_wide['odds_t10']) / uma_wide['odds_t10'] * 100
print(f'umaren 有効レコード: {len(uma_wide):,}')
print(f'change_rate(%) stats:')
display(uma_wide['change_rate'].describe(percentiles=[.05, .1, .25, .5, .75, .9, .95]))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 3. 結果・払戻データの結合

- `*_result.csv` から「1着馬番」「2着馬番」「単勝確定オッズ」を取得
- `*_payout.csv` から単勝・馬連の払戻 (¥/100円) を取得
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── result (全レース)
res_files = sorted(RESULT_DIR.glob('*_result.csv'))
res_dfs = [pd.read_csv(p, encoding='utf-8-sig') for p in res_files]
df_res = pd.concat(res_dfs, ignore_index=True) if res_dfs else pd.DataFrame()
print(f'result 行数: {len(df_res):,}  / 対象 race_id: {df_res["race_id"].nunique():,}')

# rank, umaban を数値化 (NAR は除外馬で空文字あり)
df_res['rank'] = pd.to_numeric(df_res['rank'], errors='coerce').astype('Int64')
df_res['umaban'] = pd.to_numeric(df_res['umaban'], errors='coerce').astype('Int64')
df_res['odds_final'] = pd.to_numeric(df_res['odds_final'], errors='coerce')

df_res_clean = df_res.dropna(subset=['rank', 'umaban'])
df_res.head()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── payout (全レース) → 単勝・馬連を抽出
pay_files = sorted(RESULT_DIR.glob('*_payout.csv'))
pay_dfs = [pd.read_csv(p, encoding='utf-8-sig') for p in pay_files]
df_pay = pd.concat(pay_dfs, ignore_index=True) if pay_dfs else pd.DataFrame()
print(f'payout 行数: {len(df_pay):,}')

# 単勝
df_pay_tan = df_pay[df_pay['kenshu'] == '単勝'].copy()
df_pay_tan['win_umaban'] = pd.to_numeric(df_pay_tan['combo'], errors='coerce').astype('Int64')
df_pay_tan['payout_tan'] = pd.to_numeric(df_pay_tan['payout'], errors='coerce')
df_pay_tan = df_pay_tan[['race_id', 'win_umaban', 'payout_tan']]
print(f'単勝 payout: {len(df_pay_tan):,} レース')

# 馬連 (combo は "6 11" のような空白区切り)
df_pay_uma = df_pay[df_pay['kenshu'] == '馬連'].copy()
def _parse_uma(c):
    if not isinstance(c, str):
        return (pd.NA, pd.NA)
    parts = re.findall(r'\\d+', c)
    if len(parts) < 2:
        return (pd.NA, pd.NA)
    a, b = int(parts[0]), int(parts[1])
    return (min(a, b), max(a, b))

uma_pairs = df_pay_uma['combo'].apply(_parse_uma)
df_pay_uma['win_P1'] = uma_pairs.apply(lambda t: t[0]).astype('Int64')
df_pay_uma['win_P2'] = uma_pairs.apply(lambda t: t[1]).astype('Int64')
df_pay_uma['payout_uma'] = pd.to_numeric(df_pay_uma['payout'], errors='coerce')
df_pay_uma = df_pay_uma[['race_id', 'win_P1', 'win_P2', 'payout_uma']]
print(f'馬連 payout: {len(df_pay_uma):,} レース')

display(df_pay_tan.head())
display(df_pay_uma.head())
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
### 3.1 有効レースの確定

T-10, T-3 オッズ、払戻のすべてが揃っているレースのみを以降の分析対象とする。
(セクション 1 の取得状況で集計済みの 「揃わなかったレース」 はここで除外する)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# race_id の型を一致させるため tan_wide / uma_wide (どちらも int64) を基準にする。
# tan_wide / uma_wide は T-10 と T-3 が両方揃ったレコードのみ含む。

races_both_tan = set(tan_wide['race_id'].unique())
races_pay_tan_ids = set(df_pay_tan.dropna(subset=['win_umaban', 'payout_tan'])['race_id'])
valid_races_tan = races_both_tan & races_pay_tan_ids

races_both_uma = set(uma_wide['race_id'].unique())
races_pay_uma_ids = set(df_pay_uma.dropna(subset=['win_P1', 'win_P2', 'payout_uma'])['race_id'])
valid_races_uma = races_both_uma & races_pay_uma_ids

n_total = df_inv['race_id'].nunique()
print(f'全 race_id              : {n_total}')
print(f'  単勝 T-10/T-3 両方あり : {len(races_both_tan)}')
print(f'  単勝 払戻あり          : {len(races_pay_tan_ids)}')
print(f'  → 単勝 有効レース      : {len(valid_races_tan)}  ({len(valid_races_tan)/n_total*100:.1f}%)')
print(f'  馬連 T-10/T-3 両方あり : {len(races_both_uma)}')
print(f'  馬連 払戻あり          : {len(races_pay_uma_ids)}')
print(f'  → 馬連 有効レース      : {len(valid_races_uma)}  ({len(valid_races_uma)/n_total*100:.1f}%)')

# 除外内訳 (単勝)
miss_tan = pd.DataFrame({
    '理由': [
        'T-10 または T-3 単勝オッズなし',
        '単勝払戻なし',
        'T-10/T-3 はあるが 単勝払戻なし',
        'T-10/T-3 揃って 払戻もあり (= 単勝有効)',
    ],
    'レース数': [
        n_total - len(races_both_tan),
        n_total - len(races_pay_tan_ids),
        len(races_both_tan - races_pay_tan_ids),
        len(valid_races_tan),
    ],
})
print('=== 単勝 除外内訳 ===')
display(miss_tan)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 単勝: 変化率 × 的中フラグ × 払戻 をマージ (有効レースのみ)
tan_merged = (tan_wide[tan_wide['race_id'].isin(valid_races_tan)]
              .merge(df_pay_tan.dropna(subset=['win_umaban', 'payout_tan']),
                     on='race_id', how='inner'))
tan_merged['hit'] = (tan_merged['umaban'] == tan_merged['win_umaban']).astype(int)
# 払戻 (¥ / 100円賭け): 的中時のみ payout_tan、外れは 0
tan_merged['payout'] = np.where(tan_merged['hit'] == 1, tan_merged['payout_tan'], 0)
print(f'単勝 merged: {len(tan_merged):,} 行  '
      f'/ {tan_merged["race_id"].nunique():,} レース  '
      f'/ 的中数: {tan_merged["hit"].sum():,}')
display(tan_merged.head())

# ── 馬連: 同様
uma_merged = (uma_wide[uma_wide['race_id'].isin(valid_races_uma)]
              .merge(df_pay_uma.dropna(subset=['win_P1', 'win_P2', 'payout_uma']),
                     on='race_id', how='inner'))
uma_merged['hit'] = ((uma_merged['P1'] == uma_merged['win_P1'])
                     & (uma_merged['P2'] == uma_merged['win_P2'])).astype(int)
uma_merged['payout'] = np.where(uma_merged['hit'] == 1, uma_merged['payout_uma'], 0)
print(f'馬連 merged: {len(uma_merged):,} 行  '
      f'/ {uma_merged["race_id"].nunique():,} レース  '
      f'/ 的中数: {uma_merged["hit"].sum():,}')
display(uma_merged.head())
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 4. 変化率 10 区分 × 的中・回収統計

変化率を `pd.qcut(..., q=10)` で 10 等分し、各区分の集計を出力する。

| 列 | 内容 |
|---|---|
| 母数 | 区分内のサンプル数 |
| 的中数 | hit == 1 のレコード数 |
| 的中率 (%) | 的中数 / 母数 |
| 回収額 (¥) | 区分内の payout 合計 (100円賭けベース) |
| 回収率 (%) | 回収額 / (母数 × 100) × 100 |
| 平均配当 (¥) | 的中時のみの payout 平均 |
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
        if pd.isna(v):
            return ''
        if v >= 120: return 'background-color:#2e7d32;color:#fff'
        if v >= 100: return 'background-color:#66bb6a;color:#000'
        if v >=  80: return 'background-color:#a5d6a7;color:#000'
        if v >=  60: return 'background-color:#fff176;color:#000'
        return 'background-color:#e57373;color:#000'

    def color_hit(v):
        if pd.isna(v):
            return ''
        if v >= 30: return 'background-color:#2e7d32;color:#fff'
        if v >= 20: return 'background-color:#66bb6a;color:#000'
        if v >= 15: return 'background-color:#a5d6a7;color:#000'
        if v >= 10: return 'background-color:#fff176;color:#000'
        return 'background-color:#ef9a9a;color:#000'

    return (df.style
              .map(color_hit, subset=['的中率(%)'])
              .map(color_rec, subset=['回収率(%)'])
              .format({'回収額': '{:,.0f}', '平均配当(¥)': '{:,.0f}'}))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 単勝 10 区分集計
tan_stats = bin_stats(tan_merged, 'change_rate', n_bins=10)
print('=== 単勝: T-10 → T-3 変化率 10 区分 ===')
display(style_stats(tan_stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 馬連 10 区分集計
uma_stats = bin_stats(uma_merged, 'change_rate', n_bins=10)
print('=== 馬連: T-10 → T-3 変化率 10 区分 ===')
display(style_stats(uma_stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 単勝・馬連 区分別の可視化
def plot_bin_stats(stats: pd.DataFrame, title_prefix: str):
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    # x軸ラベル: 区分の中央値
    x_labels = [f'[{lo:+.1f}, {hi:+.1f}]' for lo, hi in
                zip(stats['区分下限(%)'], stats['区分上限(%)'])]
    x_pos = range(len(stats))

    # ── 母数
    ax = axes[0, 0]
    ax.bar(x_pos, stats['母数'], color='steelblue')
    ax.set_title(f'{title_prefix} 母数 (区分別レコード数)')
    ax.set_xticks(x_pos); ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
    ax.grid(axis='y', alpha=0.3)

    # ── 的中率
    ax = axes[0, 1]
    bars = ax.bar(x_pos, stats['的中率(%)'], color='seagreen')
    ax.set_title(f'{title_prefix} 的中率 (%)')
    ax.axhline(stats['的中数'].sum() / stats['母数'].sum() * 100,
               color='gray', linestyle='--', label='全体平均')
    ax.set_xticks(x_pos); ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
    ax.legend(); ax.grid(axis='y', alpha=0.3)

    # ── 回収率
    ax = axes[1, 0]
    colors = ['#2e7d32' if v >= 100 else '#e57373' for v in stats['回収率(%)']]
    ax.bar(x_pos, stats['回収率(%)'], color=colors)
    ax.axhline(100, color='black', linestyle='--', label='損益分岐 100%')
    ax.set_title(f'{title_prefix} 回収率 (%)')
    ax.set_xticks(x_pos); ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
    ax.legend(); ax.grid(axis='y', alpha=0.3)

    # ── 平均配当
    ax = axes[1, 1]
    ax.bar(x_pos, stats['平均配当(¥)'].fillna(0), color='goldenrod')
    ax.set_title(f'{title_prefix} 平均配当 (¥)')
    ax.set_xticks(x_pos); ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
    ax.grid(axis='y', alpha=0.3)

    fig.suptitle(f'{title_prefix}: T-10 → T-3 変化率区分別 統計', fontsize=14)
    plt.tight_layout()
    plt.show()


plot_bin_stats(tan_stats, '単勝')
plot_bin_stats(uma_stats, '馬連')
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 5. 勝ち馬の単勝オッズ推移 vs 非勝ち馬

各 race の `result.csv` で `rank == 1` の馬を勝ち馬とする。
T-60〜T-1 のオッズを取得して、勝ち馬と非勝ち馬それぞれの平均推移を比較する。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 全 T-label の tanfuku をロード (各 race × umaban × label の単勝オッズ)
df_tan_all_raw = load_snapshots('tanfuku', T_LABELS)
df_tan_all = (df_tan_all_raw
              .sort_values('snapshot_time')
              .drop_duplicates(['race_id', 'umaban', 'label'], keep='last'))

# wide
tan_full_wide = (df_tan_all.pivot_table(
                    index=['race_id', 'umaban'], columns='label',
                    values='odds_tan', aggfunc='last')
                 .reindex(columns=T_LABELS)
                 .reset_index())

# T-10/T-3/払戻が揃った単勝有効レースのみに絞る (セクション 3.1 の集合)
tan_full_wide = tan_full_wide[tan_full_wide['race_id'].isin(valid_races_tan)]

# 勝ち馬フラグ
winners = df_res_clean[df_res_clean['rank'] == 1][['race_id', 'umaban']].copy()
winners['is_winner'] = 1
tan_full_wide = tan_full_wide.merge(winners, on=['race_id', 'umaban'], how='left')
tan_full_wide['is_winner'] = tan_full_wide['is_winner'].fillna(0).astype(int)

n_winner = int(tan_full_wide['is_winner'].sum())
n_other  = int((tan_full_wide['is_winner'] == 0).sum())
print(f'対象レース       : {tan_full_wide["race_id"].nunique():,}')
print(f'勝ち馬レコード   : {n_winner:,}  / 非勝ち馬レコード: {n_other:,}')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 集計1: 各 T-label の平均オッズ (勝ち馬 vs 非勝ち馬)
def odds_summary(df: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    rows = []
    for lab in labels:
        if lab not in df.columns:
            continue
        s = df[lab].dropna()
        rows.append({'label': lab,
                     'n': len(s),
                     'mean_odds': s.mean(),
                     'median_odds': s.median()})
    return pd.DataFrame(rows)


sum_win = odds_summary(tan_full_wide[tan_full_wide['is_winner'] == 1], T_LABELS)
sum_los = odds_summary(tan_full_wide[tan_full_wide['is_winner'] == 0], T_LABELS)

cmp_df = sum_win.merge(sum_los, on='label', suffixes=('_win', '_loss'))
cmp_df['mean_diff'] = cmp_df['mean_odds_win'] - cmp_df['mean_odds_loss']
print('=== 勝ち馬 vs 非勝ち馬 単勝オッズ (label 別) ===')
display(cmp_df.round(2))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 集計2: 各馬を T-10 で正規化して推移を見る (相対倍率)
#         (オッズの絶対値は馬ごとに差が大きいため)
def normalized_trajectory(df: pd.DataFrame, anchor: str = 'T10') -> pd.DataFrame:
    base_ok = df[anchor].notna() & (df[anchor] > 0)
    sub = df[base_ok].copy()
    for lab in T_LABELS:
        if lab in sub.columns:
            sub[f'rel_{lab}'] = sub[lab] / sub[anchor]
    return sub


tan_norm = normalized_trajectory(tan_full_wide, anchor='T10')

rel_cols = [f'rel_{lab}' for lab in T_LABELS if f'rel_{lab}' in tan_norm.columns]
traj_win  = tan_norm[tan_norm['is_winner'] == 1][rel_cols].mean()
traj_los  = tan_norm[tan_norm['is_winner'] == 0][rel_cols].mean()

traj_df = pd.DataFrame({'勝ち馬 (mean)': traj_win.values,
                         '非勝ち馬 (mean)': traj_los.values},
                        index=T_LABELS)
print('=== T-10 を 1.0 とした相対オッズ平均 ===')
display(traj_df.round(4))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── プロット: 平均オッズ + T-10 正規化推移
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# (a) 平均オッズ (log スケール)
ax = axes[0]
x = [T_OFFSETS[l] for l in cmp_df['label']]
ax.plot(x, cmp_df['mean_odds_win'], 'o-', color='crimson', label='勝ち馬 (mean)')
ax.plot(x, cmp_df['mean_odds_loss'], 'o-', color='steelblue', label='非勝ち馬 (mean)')
ax.invert_xaxis()
ax.set_yscale('log')
ax.set_xlabel('T-XX (発走前 分)')
ax.set_ylabel('単勝オッズ (log)')
ax.set_title('単勝オッズ平均推移')
ax.legend(); ax.grid(alpha=0.3)

# (b) T-10 正規化
ax = axes[1]
x_full = [T_OFFSETS[l] for l in T_LABELS]
ax.plot(x_full, traj_win.values, 'o-', color='crimson', label='勝ち馬')
ax.plot(x_full, traj_los.values, 'o-', color='steelblue', label='非勝ち馬')
ax.axhline(1.0, color='gray', linestyle='--', alpha=0.5)
ax.invert_xaxis()
ax.set_xlabel('T-XX (発走前 分)')
ax.set_ylabel('オッズ倍率 (T-10 = 1.0)')
ax.set_title('T-10 を基準とした相対オッズ推移')
ax.legend(); ax.grid(alpha=0.3)

plt.tight_layout()
plt.show()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 変化率分布の比較: 勝ち馬 vs 非勝ち馬 (T-10 → T-3)
tan_full_wide['change_rate_t10_t3'] = (
    (tan_full_wide['T3'] - tan_full_wide['T10']) / tan_full_wide['T10'] * 100
)
cmp_change = tan_full_wide.dropna(subset=['change_rate_t10_t3'])

stat_win = cmp_change[cmp_change['is_winner'] == 1]['change_rate_t10_t3']
stat_los = cmp_change[cmp_change['is_winner'] == 0]['change_rate_t10_t3']

print('=== T-10 → T-3 単勝変化率分布 ===')
print(pd.DataFrame({
    '勝ち馬': stat_win.describe(percentiles=[.1, .25, .5, .75, .9]),
    '非勝ち馬': stat_los.describe(percentiles=[.1, .25, .5, .75, .9]),
}).round(2))

fig, ax = plt.subplots(figsize=(10, 5))
bins = np.linspace(-50, 50, 41)
ax.hist(stat_los.clip(-50, 50), bins=bins, density=True,
        alpha=0.55, color='steelblue', label=f'非勝ち馬 (n={len(stat_los):,})')
ax.hist(stat_win.clip(-50, 50), bins=bins, density=True,
        alpha=0.55, color='crimson', label=f'勝ち馬 (n={len(stat_win):,})')
ax.axvline(stat_win.mean(), color='crimson', linestyle='--',
           label=f'勝ち馬 平均={stat_win.mean():.2f}%')
ax.axvline(stat_los.mean(), color='steelblue', linestyle='--',
           label=f'非勝ち馬 平均={stat_los.mean():.2f}%')
ax.set_xlabel('T-10 → T-3 単勝オッズ変化率 (%)')
ax.set_ylabel('density')
ax.set_title('T-10 → T-3 単勝変化率分布: 勝ち馬 vs 非勝ち馬')
ax.legend(); ax.grid(alpha=0.3)
plt.tight_layout()
plt.show()
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# ── 「直前で下げた (= 人気上昇) 馬」が勝ち馬になりやすいか?
#    change_rate ≤ -10% の馬の勝率 / そうでない馬の勝率
threshold = -10
cmp_change['is_downer'] = (cmp_change['change_rate_t10_t3'] <= threshold).astype(int)
xt = (cmp_change.groupby('is_downer')['is_winner']
      .agg(['size', 'sum'])
      .rename(columns={'size': '母数', 'sum': '勝ち馬数'}))
xt['勝率(%)'] = (xt['勝ち馬数'] / xt['母数'] * 100).round(2)
xt.index = ['それ以外', f'change_rate ≤ {threshold}% (下げ馬)']
print(f'=== 直前下げ (change_rate ≤ {threshold}%) と勝率の関係 ===')
display(xt)
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 6. TXX → T-3 全パターンの変化率分析

直前オッズ下落シグナルが他のスタート点でも有効かを検証する。

- 検証パターン: `T-60 → T-3`, `T-30 → T-3`, `T-15 → T-3`, `T-10 → T-3`, `T-5 → T-3`
- 各パターンで 単勝・馬連 の 10 区分集計を出力
- 「TXX 行が存在しない race × 馬」は自動的に除外 (有効レース集合 ∩ TXX 取得済み)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 馬連の全 T-label をロード (単勝は section 5 で df_tan_all をロード済み)
df_uma_all_raw = load_snapshots('umaren', T_LABELS)
df_uma_all = (df_uma_all_raw
              .sort_values('snapshot_time')
              .drop_duplicates(['race_id', 'P1', 'P2', 'label'], keep='last'))
print(f'umaren 全 T-label 行数: {len(df_uma_all):,}')

START_LABELS = ['T60', 'T30', 'T15', 'T10', 'T5']
END_LABEL = 'T3'


def build_pattern(start_label: str, kind: str, end_label: str = 'T3') -> pd.DataFrame:
    '''指定 (start, end) ペアの change_rate + hit + payout を返す.

    valid_races は (start ∩ end ∩ payout) を動的に計算するため、
    終端を T-3 / T-1 と切り替えても同じ関数で評価できる。
    '''
    if kind == 'tanfuku':
        src = df_tan_all
        odds_col = 'odds_tan'
        key_cols = ['race_id', 'umaban']
        pay_df = df_pay_tan.dropna(subset=['win_umaban', 'payout_tan'])
        pay_col = 'payout_tan'
    else:
        src = df_uma_all
        odds_col = 'odds_umaren'
        key_cols = ['race_id', 'P1', 'P2']
        pay_df = df_pay_uma.dropna(subset=['win_P1', 'win_P2', 'payout_uma'])
        pay_col = 'payout_uma'

    sub = src[src['label'].isin([start_label, end_label])]
    if sub.empty:
        return pd.DataFrame()

    # 動的 valid_races: start, end, payout が揃ったレースのみ
    races_start = set(src[src['label'] == start_label]['race_id'])
    races_end   = set(src[src['label'] == end_label]['race_id'])
    races_pay   = set(pay_df['race_id'])
    valid_races = races_start & races_end & races_pay

    wide = (sub.pivot_table(index=key_cols, columns='label',
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
    else:
        merged['hit'] = ((merged['P1'] == merged['win_P1'])
                         & (merged['P2'] == merged['win_P2'])).astype(int)
    merged['payout'] = np.where(merged['hit'] == 1, merged[pay_col], 0)
    return merged


pat_tan: dict[str, pd.DataFrame] = {s: build_pattern(s, 'tanfuku', 'T3') for s in START_LABELS}
pat_uma: dict[str, pd.DataFrame] = {s: build_pattern(s, 'umaren',  'T3') for s in START_LABELS}

summary = pd.DataFrame([
    {
        'pattern': f'{s}→T3',
        '単勝_n': len(pat_tan[s]),
        '単勝_的中': int(pat_tan[s]['hit'].sum()) if not pat_tan[s].empty else 0,
        '単勝_的中率(%)': (round(pat_tan[s]['hit'].mean() * 100, 2)
                          if not pat_tan[s].empty else np.nan),
        '馬連_n': len(pat_uma[s]),
        '馬連_的中': int(pat_uma[s]['hit'].sum()) if not pat_uma[s].empty else 0,
        '馬連_的中率(%)': (round(pat_uma[s]['hit'].mean() * 100, 2)
                          if not pat_uma[s].empty else np.nan),
    }
    for s in START_LABELS
])
print('=== TXX → T-3 各パターンの母数 ===')
display(summary)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 単勝: パターン × 10 区分テーブル
for start in START_LABELS:
    df = pat_tan[start]
    if df.empty:
        print(f'  [SKIP] 単勝 {start}→T3: データなし')
        continue
    stats = bin_stats(df, 'change_rate', n_bins=10)
    print(f'=== 単勝: {start} → T-3   n={len(df)} / 的中={int(df["hit"].sum())} ===')
    display(style_stats(stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 馬連: パターン × 10 区分テーブル
for start in START_LABELS:
    df = pat_uma[start]
    if df.empty:
        print(f'  [SKIP] 馬連 {start}→T3: データなし')
        continue
    stats = bin_stats(df, 'change_rate', n_bins=10)
    print(f'=== 馬連: {start} → T-3   n={len(df)} / 的中={int(df["hit"].sum())} ===')
    display(style_stats(stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 7. 閾値スイープ — 下落率カットオフごとの ROI と P/L

「change_rate ≤ 閾値」(= 閾値以上の下落) で買い目を絞ったときの 回収率・累積損益・母数 を
閾値を `-50% ~ 0%` の範囲で 1% 刻みでスイープする。

各パターン (TXX→T-3) で次を出力:
1. 回収率 (%) vs 閾値
2. 累積 P/L (¥, 100円賭けベース) vs 閾値
3. 母数 (n) vs 閾値

最後に「最低サンプル数 20」を満たす範囲で **ROI 最大**・**P/L 最大** となる閾値を表で出力。
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def threshold_sweep(df: pd.DataFrame, thresholds: np.ndarray) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for th in thresholds:
        sub = df[df['change_rate'] <= th]
        n = len(sub)
        if n == 0:
            rows.append({'threshold': th, 'n': 0, 'hit_rate': np.nan,
                         'roi': np.nan, 'pl': 0})
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
sweep_tan = {s: threshold_sweep(pat_tan[s], THRESHOLDS) for s in START_LABELS}
sweep_uma = {s: threshold_sweep(pat_uma[s], THRESHOLDS) for s in START_LABELS}

MIN_N = 20   # 信頼性確保のための最低サンプル数
print(f'閾値スイープ: change_rate <= 閾値 の買い目 (min_n={MIN_N})')
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def plot_sweep(sweeps: dict, title_prefix: str, min_n: int = 20):
    cmap = plt.colormaps['viridis']
    colors = [cmap(i / max(1, len(START_LABELS) - 1)) for i in range(len(START_LABELS))]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ax_roi, ax_pl, ax_n = axes

    for color, start in zip(colors, START_LABELS):
        s = sweeps[start]
        if s.empty:
            continue
        ok = s['n'] >= min_n
        ax_roi.plot(s.loc[ok, 'threshold'], s.loc[ok, 'roi'],
                    'o-', color=color, label=f'{start}→T3', ms=4)
        ax_pl.plot(s.loc[ok, 'threshold'], s.loc[ok, 'pl'],
                   'o-', color=color, label=f'{start}→T3', ms=4)
        ax_n.plot(s['threshold'], s['n'], '-', color=color, label=f'{start}→T3')

    ax_roi.axhline(100, color='gray', linestyle='--', alpha=0.5, label='損益分岐 100%')
    ax_pl.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax_n.axhline(min_n, color='red', linestyle=':', alpha=0.6, label=f'min_n={min_n}')

    for ax, ylabel, title in [
        (ax_roi, '回収率 (%)', f'{title_prefix}: 回収率 vs 閾値'),
        (ax_pl,  'P/L (¥, 100円賭けベース)', f'{title_prefix}: 累積損益 vs 閾値'),
        (ax_n,   '母数 n', f'{title_prefix}: サンプル数 vs 閾値'),
    ]:
        ax.set_xlabel('change_rate 閾値 (%) — 閾値以下を買う')
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(loc='best', fontsize=9)
        ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.show()


plot_sweep(sweep_tan, '単勝', min_n=MIN_N)
plot_sweep(sweep_uma, '馬連', min_n=MIN_N)
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
            '[ROI最大] 閾値(%)': float(best_roi['threshold']),
            '[ROI最大] 母数': int(best_roi['n']),
            '[ROI最大] 的中率(%)': round(float(best_roi['hit_rate']), 2),
            '[ROI最大] 回収率(%)': round(float(best_roi['roi']), 1),
            '[ROI最大] P/L(¥)': int(best_roi['pl']),
            '[PL最大] 閾値(%)': float(best_pl['threshold']),
            '[PL最大] 母数': int(best_pl['n']),
            '[PL最大] 的中率(%)': round(float(best_pl['hit_rate']), 2),
            '[PL最大] 回収率(%)': round(float(best_pl['roi']), 1),
            '[PL最大] P/L(¥)': int(best_pl['pl']),
        })
    return pd.DataFrame(rows)


def _style_best(df: pd.DataFrame):
    def color_roi(v):
        if pd.isna(v): return ''
        if v >= 120: return 'background-color:#2e7d32;color:#fff'
        if v >= 100: return 'background-color:#66bb6a;color:#000'
        if v >=  80: return 'background-color:#a5d6a7;color:#000'
        return 'background-color:#e57373;color:#000'

    def color_pl(v):
        if pd.isna(v): return ''
        if v > 5000:  return 'background-color:#2e7d32;color:#fff'
        if v > 0:     return 'background-color:#66bb6a;color:#000'
        if v == 0:    return ''
        return 'background-color:#e57373;color:#000'

    return (df.style
              .map(color_roi, subset=['[ROI最大] 回収率(%)', '[PL最大] 回収率(%)'])
              .map(color_pl,  subset=['[ROI最大] P/L(¥)',   '[PL最大] P/L(¥)'])
              .format({'[ROI最大] P/L(¥)': '{:,.0f}', '[PL最大] P/L(¥)': '{:,.0f}'}))


bt_tan = best_thresholds(sweep_tan, min_n=MIN_N)
bt_uma = best_thresholds(sweep_uma, min_n=MIN_N)
print(f'=== 単勝: パターン別 最適閾値 (min_n={MIN_N}) ===')
display(_style_best(bt_tan))
print(f'=== 馬連: パターン別 最適閾値 (min_n={MIN_N}) ===')
display(_style_best(bt_uma))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 8. 終端を T-1 に変えた検証 (TXX → T-1)

T-3 では捉えきれない 直前 2 分間 (T-3 → T-1) のオッズ変動を信号に加えた場合の効果を検証する。
理論的には T-1 終端の方が直前スマートマネーをより多く反映するため精度が上がる可能性がある。

- 検証パターン: `T-60 → T-1`, `T-30 → T-1`, `T-15 → T-1`, `T-10 → T-1`, `T-5 → T-1`, `T-3 → T-1`
- セクション 6/7 と同じ集計 (10 区分テーブル + 閾値スイープ + 最適閾値表) を出力
- セクション 10 で T-3 終端と T-1 終端の比較を行う
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
START_LABELS_T1 = ['T60', 'T30', 'T15', 'T10', 'T5', 'T3']
END_LABEL_T1 = 'T1'

pat_tan_t1: dict[str, pd.DataFrame] = {s: build_pattern(s, 'tanfuku', END_LABEL_T1)
                                        for s in START_LABELS_T1}
pat_uma_t1: dict[str, pd.DataFrame] = {s: build_pattern(s, 'umaren',  END_LABEL_T1)
                                        for s in START_LABELS_T1}

summary_t1 = pd.DataFrame([
    {
        'pattern': f'{s}→T1',
        '単勝_n': len(pat_tan_t1[s]),
        '単勝_的中': int(pat_tan_t1[s]['hit'].sum()) if not pat_tan_t1[s].empty else 0,
        '単勝_的中率(%)': (round(pat_tan_t1[s]['hit'].mean() * 100, 2)
                          if not pat_tan_t1[s].empty else np.nan),
        '馬連_n': len(pat_uma_t1[s]),
        '馬連_的中': int(pat_uma_t1[s]['hit'].sum()) if not pat_uma_t1[s].empty else 0,
        '馬連_的中率(%)': (round(pat_uma_t1[s]['hit'].mean() * 100, 2)
                          if not pat_uma_t1[s].empty else np.nan),
    }
    for s in START_LABELS_T1
])
print('=== TXX → T-1 各パターンの母数 ===')
display(summary_t1)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 単勝: パターン × 10 区分テーブル (T-1 終端)
for start in START_LABELS_T1:
    df = pat_tan_t1[start]
    if df.empty:
        print(f'  [SKIP] 単勝 {start}→T1: データなし')
        continue
    stats = bin_stats(df, 'change_rate', n_bins=10)
    print(f'=== 単勝: {start} → T-1   n={len(df)} / 的中={int(df["hit"].sum())} ===')
    display(style_stats(stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 馬連: パターン × 10 区分テーブル (T-1 終端)
for start in START_LABELS_T1:
    df = pat_uma_t1[start]
    if df.empty:
        print(f'  [SKIP] 馬連 {start}→T1: データなし')
        continue
    stats = bin_stats(df, 'change_rate', n_bins=10)
    print(f'=== 馬連: {start} → T-1   n={len(df)} / 的中={int(df["hit"].sum())} ===')
    display(style_stats(stats))
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
# 閾値スイープ (T-1 終端)
sweep_tan_t1 = {s: threshold_sweep(pat_tan_t1[s], THRESHOLDS) for s in START_LABELS_T1}
sweep_uma_t1 = {s: threshold_sweep(pat_uma_t1[s], THRESHOLDS) for s in START_LABELS_T1}


def plot_sweep_with_labels(sweeps: dict, labels: list[str], title_prefix: str,
                            end_label: str, min_n: int = 20):
    cmap = plt.colormaps['viridis']
    colors = [cmap(i / max(1, len(labels) - 1)) for i in range(len(labels))]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ax_roi, ax_pl, ax_n = axes

    for color, start in zip(colors, labels):
        s = sweeps.get(start, pd.DataFrame())
        if s.empty:
            continue
        ok = s['n'] >= min_n
        tag = f'{start}→{end_label}'
        ax_roi.plot(s.loc[ok, 'threshold'], s.loc[ok, 'roi'],
                    'o-', color=color, label=tag, ms=4)
        ax_pl.plot(s.loc[ok, 'threshold'], s.loc[ok, 'pl'],
                   'o-', color=color, label=tag, ms=4)
        ax_n.plot(s['threshold'], s['n'], '-', color=color, label=tag)

    ax_roi.axhline(100, color='gray', linestyle='--', alpha=0.5, label='損益分岐 100%')
    ax_pl.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax_n.axhline(min_n, color='red', linestyle=':', alpha=0.6, label=f'min_n={min_n}')

    for ax, ylabel, title in [
        (ax_roi, '回収率 (%)', f'{title_prefix} (→{end_label}): 回収率 vs 閾値'),
        (ax_pl,  'P/L (¥, 100円賭けベース)', f'{title_prefix} (→{end_label}): 累積損益 vs 閾値'),
        (ax_n,   '母数 n', f'{title_prefix} (→{end_label}): サンプル数 vs 閾値'),
    ]:
        ax.set_xlabel('change_rate 閾値 (%) — 閾値以下を買う')
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(loc='best', fontsize=9)
        ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.show()


plot_sweep_with_labels(sweep_tan_t1, START_LABELS_T1, '単勝', 'T1', min_n=MIN_N)
plot_sweep_with_labels(sweep_uma_t1, START_LABELS_T1, '馬連', 'T1', min_n=MIN_N)
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
bt_tan_t1 = best_thresholds(sweep_tan_t1, min_n=MIN_N, end_label='T1')
bt_uma_t1 = best_thresholds(sweep_uma_t1, min_n=MIN_N, end_label='T1')
print(f'=== 単勝: パターン別 最適閾値 (T-1 終端, min_n={MIN_N}) ===')
display(_style_best(bt_tan_t1))
print(f'=== 馬連: パターン別 最適閾値 (T-1 終端, min_n={MIN_N}) ===')
display(_style_best(bt_uma_t1))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 9. T-3 終端 vs T-1 終端 比較

同じ start_label について、終端を T-3 にした場合と T-1 にした場合の `最適 ROI` と `最適 P/L` を並べる。
- ROI / P/L が大きく改善する → 直前 2 分のシグナルが効いている (= T-1 終端を取りに行く価値あり)
- 改善が小さい / 母数が大きく減る → T-3 終端で十分
""")

# ─────────────────────────────────────────────────────────────────────────────
code("""
def compare_endpoints(bt_t3: pd.DataFrame, bt_t1: pd.DataFrame, label: str) -> pd.DataFrame:
    '''start_label をキーにして T3 終端と T1 終端を横並びで比較.'''
    def _start(p):  # 'T60→T3' → 'T60'
        return p.split('→')[0]

    t3 = bt_t3.assign(start=bt_t3['pattern'].apply(_start)).set_index('start')
    t1 = bt_t1.assign(start=bt_t1['pattern'].apply(_start)).set_index('start')
    starts = sorted(set(t3.index) | set(t1.index),
                    key=lambda s: int(s.lstrip('T')), reverse=True)
    rows = []
    for s in starts:
        r3 = t3.loc[s] if s in t3.index else None
        r1 = t1.loc[s] if s in t1.index else None
        rows.append({
            'start': s,
            'T3:閾値(%)':    r3['[PL最大] 閾値(%)']   if r3 is not None else np.nan,
            'T3:母数':        int(r3['[PL最大] 母数']) if r3 is not None else 0,
            'T3:回収率(%)':  r3['[PL最大] 回収率(%)'] if r3 is not None else np.nan,
            'T3:P/L(¥)':     int(r3['[PL最大] P/L(¥)']) if r3 is not None else 0,
            'T1:閾値(%)':    r1['[PL最大] 閾値(%)']   if r1 is not None else np.nan,
            'T1:母数':        int(r1['[PL最大] 母数']) if r1 is not None else 0,
            'T1:回収率(%)':  r1['[PL最大] 回収率(%)'] if r1 is not None else np.nan,
            'T1:P/L(¥)':     int(r1['[PL最大] P/L(¥)']) if r1 is not None else 0,
        })
    out = pd.DataFrame(rows)
    out['Δ 回収率(pp)']  = (out['T1:回収率(%)'] - out['T3:回収率(%)']).round(1)
    out['Δ P/L(¥)']     = out['T1:P/L(¥)']   - out['T3:P/L(¥)']
    return out


def _style_compare(df: pd.DataFrame):
    def color_delta(v):
        if pd.isna(v): return ''
        if v > 0: return 'background-color:#66bb6a;color:#000'
        if v < 0: return 'background-color:#ef9a9a;color:#000'
        return ''
    return (df.style
              .map(color_delta, subset=['Δ 回収率(pp)', 'Δ P/L(¥)'])
              .format({'T3:P/L(¥)': '{:,.0f}', 'T1:P/L(¥)': '{:,.0f}',
                       'Δ P/L(¥)': '{:+,.0f}'}))


cmp_tan = compare_endpoints(bt_tan, bt_tan_t1, '単勝')
cmp_uma = compare_endpoints(bt_uma, bt_uma_t1, '馬連')
print('=== 単勝: 最適 P/L 時点での T-3 vs T-1 比較 ===')
display(_style_compare(cmp_tan))
print('=== 馬連: 最適 P/L 時点での T-3 vs T-1 比較 ===')
display(_style_compare(cmp_uma))
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## 10. 自動購入を前提とした実運用上の制約

### T-1 終端の理論精度 vs 実運用ラグの板挟み
- セクション 8/9 で示した通り、T-1 終端のほうが直前スマートマネーをより多く反映するため
  シグナル精度自体は T-3 終端より高くなる傾向がある。
- しかし発注タイミングとしては T-1 は実質的に間に合わない:
  - 締切まで 60 秒前後しかなく、ページロード (3 秒) + パース + モデル評価 + 投票 API 往復 で 5〜10 秒消費。
  - ネットワーク揺らぎ・取引所側の処理遅延でさらに 5〜30 秒のばらつきが乗る。
  - 投票確認ポップアップを伴う UI 経路では物理的に到達できないケースが出る。
- T-2 (発走 2 分前) は **現状取得していない** が、ラグを 60〜90 秒見ても締切までに発注が完了するため、
  実運用での発注信号点として最も現実的。

### 採用方針 (暫定)
| 役割 | 取得・利用範囲 |
|---|---|
| **取得 (`#L901`)** | T-60 / T-30 / T-15 / T-10 / T-5 / T-3 / T-1 をフル取得 (履歴・分析用) |
| **将来的な発注 (自動投票)** | **T-2 を取得対象に追加**し、`TXX → T-2` シグナルで T-2 直後に投票確定 |
| **現状の暫定発注** | T-3 を最終評価点に固定し、T-3 取得直後に投票確定 |
| **学習・分析** | TXX → T-3 / T-1 両方を継続観察。サンプル蓄積後に T-2 と比較 |

### 次のアクション (候補)
1. `#L901` の `SNAPSHOT_CONFIG` に `(2, 0.2)` を追加して T-2 を取得開始
2. 1〜2 週間データを溜めたうえで TXX → T-2 と TXX → T-1 / T-3 を再度比較
""")

# ─────────────────────────────────────────────────────────────────────────────
md("""
## まとめ

- セクション 1: 取得カバレッジ (日付×場所×T-label) — 取得状況・欠損確認
- セクション 2/3: T-10 → T-3 変化率 × 結果結合 (T-10/T-3/払戻が揃った有効レースのみ)
- セクション 4: 変化率 10 区分 × 的中率・回収率・平均配当 (表 + グラフ)
- セクション 5: 勝ち馬と非勝ち馬の単勝オッズ推移比較
- セクション 6: TXX → T-3 全パターン (T-60/T-30/T-15/T-10/T-5) × 単勝・馬連 の 10 区分集計
- セクション 7: 閾値スイープ (T-3 終端) — 回収率・累積 P/L が最大になる下落率閾値の探索
- セクション 8: TXX → T-1 全パターン (T-60/T-30/T-15/T-10/T-5/T-3) × 同様の集計
- セクション 9: T-3 終端 vs T-1 終端 比較 — 終端を最後まで引き寄せたときの ROI/PL 改善幅
- セクション 10: 自動購入を前提とした実運用の制約と次のアクション

> サンプル数が少ない (3 日分) ため、回収率・的中率の数値は参考値。
> `#L901` が長期稼働するに従って統計的信頼性が向上する。継続観察を推奨。
""")


# ============================================================================
# 組み立て
# ============================================================================

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
