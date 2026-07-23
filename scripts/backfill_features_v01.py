"""L902 追加特徴のバックフィル。

- 優先2 (馬体重増減/性齢/斤量/タイム): 既存 *_result.csv に生列があるので
  ネットワーク不要で正規化 (sex/age/color/body_weight_val/body_weight_diff) して
  features_horse.csv に統合する。
- 優先1 (馬場状態/天候/距離/クラス): 既存 result には無いので競走成績ページを
  再取得して *_meta.csv を作る。--fetch-meta 指定時のみ (rate-limit 遵守)。既存 meta はスキップ。

使い方:
  python scripts/backfill_features_v01.py                 # 優先2 のみ (ネット不要・高速)
  python scripts/backfill_features_v01.py --fetch-meta    # 優先1 も再取得 (低速)
  python scripts/backfill_features_v01.py --fetch-meta --limit 5   # 小サンプルで検証
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(r'C:/Users/ppny9/workspace/racing-common')))
from racing_common import rakuten_keiba as rk  # noqa: E402

RESULT_DIR = Path('D:/workspace/nar/data/results')
OUT_HORSE = RESULT_DIR / 'features_horse.csv'
DELAY_SEC = 2.5  # rakuten への負荷。ライブ L902 と併走するので余裕をもたせる


def backfill_fundamentals() -> pd.DataFrame:
    """既存 result CSV を読み、派生列を付けて1本の表に統合 (ネット不要)。"""
    files = sorted(RESULT_DIR.glob('*_result.csv'))
    files = [p for p in files if re.match(r'(\d{12}|\d{18})_result\.csv', p.name)]
    frames = []
    for p in files:
        try:
            df = pd.read_csv(p, encoding='utf-8-sig')
        except Exception as e:
            print(f'  [WARN] {p.name}: {e}')
            continue
        if df.empty or 'sex_age' not in df.columns:
            continue  # 旧 L901 形式 (性齢なし) はスキップ
        df = rk.enrich_result_df(df)
        frames.append(df)
    if not frames:
        print('対象 result なし (sex_age 列を持つファイルが無い)')
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(OUT_HORSE, index=False, encoding='utf-8-sig')
    print(f'[優先2] features_horse.csv 保存: {len(out):,}行 / {out["race_id"].nunique():,}レース')
    # カバレッジ
    for c in ['sex', 'age', 'body_weight_val', 'body_weight_diff', 'weight_load']:
        if c in out.columns:
            cov = out[c].notna().mean() * 100 if out[c].dtype.kind in 'fi' else (out[c].astype(str).str.len() > 0).mean() * 100
            print(f'    {c:18s}: 非欠損 {cov:.1f}%')
    return out


def backfill_meta(limit: int | None) -> None:
    """競走成績ページを再取得して *_meta.csv を作る (未取得ぶんのみ)。"""
    res = sorted(RESULT_DIR.glob('*_result.csv'))
    ids = [m.group(1) for p in res if (m := re.match(r'(\d{18})_result\.csv', p.name))]
    todo = [rid for rid in ids if not (RESULT_DIR / f'{rid}_meta.csv').exists()]
    if limit:
        todo = todo[:limit]
    print(f'[優先1] meta 再取得対象: {len(todo)} レース (未取得ぶん)')
    ok = 0
    for i, rid in enumerate(todo, 1):
        try:
            data = rk.fetch_result(rid)
        except Exception as e:
            print(f'  [ERROR {rid}] {e}')
            time.sleep(DELAY_SEC)
            continue
        meta = data.get('meta') or {}
        if meta and (meta.get('going') or meta.get('distance_m')):
            row = {'race_id': rid, **meta}
            pd.DataFrame([row]).to_csv(RESULT_DIR / f'{rid}_meta.csv', index=False, encoding='utf-8-sig')
            ok += 1
        if i % 20 == 0:
            print(f'    {i}/{len(todo)} … 保存 {ok}')
        time.sleep(DELAY_SEC)
    print(f'[優先1] 完了: {ok}/{len(todo)} 件 meta 保存')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fetch-meta', action='store_true', help='競走成績ページを再取得して馬場状態等を埋める')
    ap.add_argument('--limit', type=int, default=None, help='meta 再取得の上限件数 (検証用)')
    args = ap.parse_args()

    backfill_fundamentals()
    if args.fetch_meta:
        backfill_meta(args.limit)
