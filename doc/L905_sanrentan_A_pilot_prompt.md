# L905 への 3連単 A 型 試験運用 追加 — 実装指示

## 目的
NAR 自動投票システム L905 に **3連単 A 型 (T30→T3 -30% + 2000-5000倍)** を追加し、小口で試験運用する。
現状 L905 は単勝・馬連のみ対応。本タスクで 3連単を **券種別パラメータ** で並列対応する。

## 試験運用の根拠 (要約)

データ期間 06-23 〜 06-26 (4 日, 約 110R 確定) で:
- A 型 (T30→T3 -30% + T3 オッズ 2000-5000 倍): **PL +1,336,940 円, ROI 736.6%**
- max 配当 1 本を除いた **PL_ex_max +575,820 円** (1 ヒット依存度が低い)
- 4 ヒット中 **2 本が独立した大物** (06-24 と 06-25 で別レース) → シグナル妥当性の兆候
- パラメータバリエーション 5 種類すべてプラス → **構造的に効いている可能性が高い**

ただしサンプル小のため **小口で 2 週間試験運用** し、PL_ex_max が +30 万維持できるかを確認する。

## 試験運用パラメータ

### 閾値・フィルタ
| 項目 | 値 |
|---|---|
| パターン | T30 → T3 |
| 下落率閾値 | **change_rate <= -30%** |
| T3 オッズ範囲 | **2000.0 〜 5000.0 倍** |
| 1 レース最大買い目 | **20 点** (検証値 18.8/R に余裕 +1) |

### 試験運用の安全設定
| 項目 | 値 | 理由 |
|---|---|---|
| `stake_yen_san` | **50 円** | 通常 100 円の半額 (DD 抑制) |
| `dry_run` | true | 初日は必ず DRY-RUN 動作確認 |
| `safety_mode` | true | 確定ボタン手前で停止して目視確認 |
| **日次損切ライン** | **-50,000 円** | 1 日この額に達したら自動停止 |
| **連続日損失制限** | **3 日連続マイナス** | 自動停止し見直し |
| **総 DD ストップ** | **-200,000 円** | 試験期間中の累積 |

## 実装内容

### 1. `config/l905_settings.json` の拡張

既存 (単勝・馬連の) 構造を保ったまま **3連単セクションを追加**。
※ パターン (T_START/T_END) は **券種別** に持てるようリファクタリングが必要。

```json
{
  "_comment": "L905 自動投票の設定 (券種別)。",

  "tan": {
    "T_START": "T10",
    "T_END": "T3",
    "change_rate_max": -30.0,
    "min_odds": 1.5,
    "max_odds": 2000.0,
    "max_bets_per_race": 1,
    "stake_yen": 100,
    "enabled": true
  },

  "uma": {
    "T_START": "T10",
    "T_END": "T3",
    "change_rate_max": -30.0,
    "min_odds": 0.0,
    "max_odds": 99999.0,
    "max_bets_per_race": 10,
    "stake_yen": 100,
    "enabled": true
  },

  "san": {
    "_comment": "3連単 A 型 試験運用 (2026-06-26 開始)。安定したら stake_yen を 100 に戻す。",
    "T_START": "T30",
    "T_END": "T3",
    "change_rate_max": -30.0,
    "min_odds": 2000.0,
    "max_odds": 5000.0,
    "max_bets_per_race": 20,
    "stake_yen": 50,
    "enabled": true
  },

  "global": {
    "dry_run": false,
    "safety_mode": true,
    "max_total_bets_per_day": 1000,
    "daily_loss_stop_yen": -50000,
    "consecutive_loss_days_stop": 3,
    "total_drawdown_stop_yen": -200000
  },

  "operation": {
    "data_root": "D:/workspace/nar/data",
    "poll_interval_sec": 10,
    "log_dir": "logs/"
  }
}
```

**後方互換**: 旧キー (`thresholds.*`, `betting.*`) を読んだ場合は警告のみで起動できるよう、`spat4.load_config()` にマイグレーションロジックを入れてもよい (任意)。

### 2. `scripts/build_L905_notebook.py` の改修

#### 2.1 設定読み込みセル
旧コードを置換:
```python
TAN_CFG = config['tan']
UMA_CFG = config['uma']
SAN_CFG = config['san']
GLOBAL_CFG = config['global']

DRY_RUN                = GLOBAL_CFG['dry_run']
MAX_TOTAL_BETS_PER_DAY = GLOBAL_CFG['max_total_bets_per_day']
```

#### 2.2 シグナル計算関数の拡張
既存 `calc_signals_for_race(race_id)` に **3連単処理ブロック** を追加。
構造は単勝・馬連と同じ:

```python
# ── 3連単 (sanrentan) ──
if SAN_CFG.get('enabled', False):
    df_s_start = load_latest_snapshot(race_id, SAN_CFG['T_START'], 'sanrentan')
    df_s_end   = load_latest_snapshot(race_id, SAN_CFG['T_END'],   'sanrentan')
    if not df_s_start.empty and not df_s_end.empty:
        m = df_s_start[['P1','P2','P3','odds_sanrentan']].rename(columns={'odds_sanrentan': 'odds_start'}) \
              .merge(df_s_end[['P1','P2','P3','odds_sanrentan']].rename(columns={'odds_sanrentan': 'odds_end'}),
                     on=['P1','P2','P3'])
        m = m.dropna(subset=['odds_start','odds_end'])
        m = m[m['odds_start'] > 0]
        m['change_rate'] = (m['odds_end'] - m['odds_start']) / m['odds_start'] * 100
        hits = m[(m['change_rate'] <= SAN_CFG['change_rate_max'])
                 & (m['odds_end'] >= SAN_CFG['min_odds'])
                 & (m['odds_end'] <= SAN_CFG['max_odds'])]
        for _, r in hits.iterrows():
            bets['san_bets'].append({
                'P1': int(r['P1']), 'P2': int(r['P2']), 'P3': int(r['P3']),
                'odds_start': float(r['odds_start']),
                'odds_end':   float(r['odds_end']),
                'change_rate': float(r['change_rate']),
            })
```

#### 2.3 採用フィルタの拡張
既存の `apply_cap_per_race(signals)` で `san_bets` も処理:
```python
san_sorted = sorted(signals['san_bets'], key=lambda b: b['change_rate'])
signals['san_bets'] = san_sorted[:SAN_CFG['max_bets_per_race']]
```

#### 2.4 SPAT4 投票実行ブロック
3連単の投票呼び出しを追加 (spat4 モジュール側で `place_sanrentan_bet()` が必要):
```python
for b in signals['san_bets']:
    spat4.place_sanrentan_bet(driver, race_id, b['P1'], b['P2'], b['P3'],
                              stake=SAN_CFG['stake_yen'], dry_run=DRY_RUN)
```

#### 2.5 損切ロジックの追加
新規セル `class DailyLossStop`:
```python
class DailyLossStop:
    """1 日の累計 PL が損切ラインに達したら停止フラグを立てる。"""
    def __init__(self, threshold_yen: int):
        self.threshold = threshold_yen  # 負の値
        self.daily_pl = 0
        self.stopped = False

    def add(self, pl_yen: int):
        self.daily_pl += pl_yen
        if self.daily_pl <= self.threshold:
            self.stopped = True
            print(f'⚠ 日次損切 {self.daily_pl:,d}円 (閾値 {self.threshold:,d}円) — 投票停止')
        return self.stopped
```

メインループ先頭で `if loss_stop.stopped: continue` をチェック。

### 3. `spat4.py` (SPAT4 投票モジュール)

新規関数:
- `place_sanrentan_bet(driver, race_id, p1, p2, p3, stake, dry_run)` — SPAT4 の 3連単投票画面遷移・入力
- 既存 `place_tan_bet` / `place_uma_bet` と同じインターフェイスで実装

### 4. ログ出力フォーマット拡張

`logs/L905_YYYYMMDD.csv` のヘッダーを更新:
```csv
timestamp,race_id,venue,race_num,kenshu,combo,odds_start,odds_end,change_rate,stake,dry_run,result
```
`combo` は 単勝=`5`, 馬連=`3-7`, 3連単=`3-7-11` の表記で。

### 5. 動作確認手順

1. **DRY-RUN テスト** (初日)
   - `global.dry_run: true` のまま 1 日稼働
   - ログを目視確認: 3連単シグナル発生 / 買い目算出 / 損切判定が正常か
   - 想定: 1 日 約 700 点シグナル (= 35,000 円相当の DRY 取引)

2. **SAFETY-MODE 確認** (2 日目)
   - `dry_run: false, safety_mode: true` で SPAT4 ナビゲーション動作確認
   - 投票確定ボタン直前で 60 秒停止すること
   - 1 件だけ手動確定 → 投票履歴 / 残高反映を SPAT4 で目視確認

3. **本運用** (3 日目以降)
   - `safety_mode: false` に切替
   - **stake_yen: 50 円のまま 2 週間継続**
   - daily_loss_stop_yen / consecutive_loss_days_stop / total_drawdown_stop_yen の自動停止が効くこと

### 6. 監視ポイント

毎日 21:00 頃 (NAR ナイター終了後) に確認:
- 日次 PL (3連単のみ / 単勝・馬連合算)
- **累積 PL_ex_max** (最大配当 1 本を除いた PL)
- 連続マイナス日数
- 自動停止が発動したかどうか

### 7. 停止条件 (試験運用中)
以下のいずれかに該当したら **stake_yen を 100 円に戻さず即停止して連絡**:
- 日次損失 -50,000 円 に到達
- 連続 3 日マイナス
- 累積 DD -200,000 円
- **PL_ex_max が試験開始から -10 万円未満** に低下

## 期待される結果 (2 週間後)

成功シナリオ:
- 14 日中 **8〜10 日プラス** (期待値 ベース)
- 累計 PL **+50 万円〜+100 万円** (stake 50 円ベース、シグナル妥当なら)
- **PL_ex_max +30 万円以上 維持**
- → 本格運用 (stake 100 円) に移行

失敗シナリオ:
- 14 日中 **8 日以上マイナス** → シグナル無効と判定し停止
- PL_ex_max +10 万円以下 → 大物配当 1 本依存 と判定し見直し

## ファイル変更サマリ

| ファイル | 変更内容 |
|---|---|
| `config/l905_settings.json` | 券種別構造へリファクタリング + `san` セクション追加 |
| `scripts/build_L905_notebook.py` | 3連単シグナル計算・採用・投票・損切ロジック追加 |
| `notebooks/#L905_auto_bettor_v01.ipynb` | 上記スクリプトで自動再生成 |
| `nar/module/spat4.py` (推定) | `place_sanrentan_bet()` 追加 |
| `logs/L905_*.csv` | 既存フォーマットでヘッダー互換 (kenshu 列に "3連単" 追加) |

## 完了報告に含めるべきもの

- 改修した PR / commit ハッシュ
- DRY-RUN 1 日のサンプルログ (3連単シグナル 50 件分程度)
- SAFETY-MODE で 1 件手動確定した SPAT4 投票履歴のスクリーンショット
- 本運用切替の準備完了報告 (チェックリスト付き)
