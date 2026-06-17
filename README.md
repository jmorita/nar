# LHR (Local Horse Racing) — 地方競馬データ取得システム

NAR (地方競馬) の全レースを対象に、オッズスナップショット・出走表・結果・払戻を
常時取得するシステム。

## ディレクトリ構成

```
lhr/
├── notebooks/
│   └── #L901_odds_snapshot_v01.ipynb   常時起動メインスクリプト
├── data/
│   ├── schedule/         当日スケジュール cache (YYYYMMDD.csv)
│   ├── shutsuba/         出走表 ({race_id}_shutsuba.csv)
│   ├── odds_snapshots/   T-30/T-10/T-3 オッズ
│   └── results/          確定オッズ + 払戻
├── scripts/              バッチ・補助スクリプト
└── logs/
```

## 取得タイミング

| タイミング | 取得内容 |
|---|---|
| 発走 90分前〜1分前 (1回) | 出走表 (馬名/騎手/調教師/オッズ) |
| 発走 T-30分 (±1分) | 単勝・複勝・馬連 オッズスナップショット |
| 発走 T-10分 (±1分) | 同上 |
| 発走 T-3分 (±1分) | 同上 |
| 発走 5分後以降 | レース結果 + 確定オッズ + 払戻 |

## 必要環境

- Python 3.11+
- selenium 4.x (Chrome driver は selenium 内蔵 manager で自動取得)
- pandas, requests, beautifulsoup4
- Chrome ブラウザ (ヘッドレス実行)

## 使い方

```
1. Jupyter Lab で notebooks/#L901_odds_snapshot_v01.ipynb を開く
2. cell[0] 〜 cell[7] を順に実行 (helper 定義)
3. cell[8] (メインループ) を実行 → 常時起動
4. 終了は Ctrl+C (driver は自動 quit)
```

## 参考

- スクレイピング実装の元: `jra/netkeiba/recommend＿地方_v11_園田.ipynb`
- JRA 版相当: `jra/#J902_odds_snapshot_v01.ipynb`
