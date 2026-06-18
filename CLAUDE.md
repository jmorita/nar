# NAR (地方競馬) プロジェクト — CLAUDE 指示

地方競馬 (NAR) のオッズスナップショット・出走表・結果取得システム。
JRA プロジェクト (`../jra/`) と同じアーキ・命名ルールで運用。

## ディレクトリ構成
```
nar/
├── notebooks/
│   └── #L901_odds_snapshot_v01.ipynb   常時起動メインスクリプト
├── data/
│   ├── schedule/                       当日スケジュール cache (YYYYMMDD.csv)
│   ├── shutsuba/                       出走表 ({race_id}_shutsuba.csv)
│   ├── odds_snapshots/                 T-60/30/15/10/5/3/1 オッズ
│   └── results/                        確定オッズ + 払戻
├── scripts/                            バッチ・補助
└── logs/
```

## 命名規則 (#J90x / #M90x と統一)
- `#L9xx` 番台: 自動運用系 (#L901 = 常時起動オッズ取得)
- `#L4xx` 番台: 予測・モデル適用
- `#L9yy` 番台: サマリー・実績確認

## 取得タイミング (JRA #J902 と同一)
| T-XX | 窓 (分) | 用途 |
|---|---|---|
| T-60 | ±2.0 | 基準点 |
| T-30 | ±1.0 | 中期トレンド |
| T-15 | ±0.7 | ミドル |
| T-10 | ±0.7 | スマートマネー入口 |
| T-5  | ±0.5 | 本格スマートマネー |
| T-3  | ±0.3 | 直前駆け込み前半 |
| T-1  | ±0.3 | 締切直前 |

## 実装基盤
- **Selenium Chrome headless** (NAR netkeiba odds は JS レンダリング)
- 単一 driver インスタンス再利用、例外時自動再起動
- スケジュール取得は BS4 (静的 HTML)
- 出力は CSV (utf-8-sig)、`race_id` を最初の列

## NAR 場所コード (race_id 5-6桁目)
```
30=門別, 35=盛岡, 36=水沢, 42=浦和, 43=船橋, 44=大井, 45=川崎,
46=金沢, 47=笠松, 48=名古屋, 50=園田, 51=姫路, 54=高知, 55=佐賀, 65=帯広(ばんえい)
```

## 注意事項
- print 引数の先頭に `\n` を入れない (`print("\n何か")` 禁止) — JRA と同様
- スクレイピング rate-limit: 連続リクエスト間隔 3 秒以上
- race_id は 12 桁 (YYYY+VV+KK+DD+RR) で netkeiba 形式

## 参考
- 元実装: `../jra/netkeiba/recommend＿地方_v11_園田.ipynb` (Selenium基盤)
- JRA 相当: `../jra/#J902_odds_snapshot_v01.ipynb`
- ボート相当: `../mra/#M907_odds_snapshot_v01.ipynb`
