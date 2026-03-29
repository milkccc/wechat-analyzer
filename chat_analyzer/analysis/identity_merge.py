from __future__ import annotations

from collections import defaultdict

import pandas as pd


def analyze(df: pd.DataFrame, meta: dict, partner_name: str) -> dict:
    platform_rows = []
    observed_counts = defaultdict(int)
    if 'source_platform' in df.columns and 'source_name' in df.columns:
        for row in df[['source_platform', 'source_name']].itertuples(index=False):
            key = (
                str(getattr(row, 'source_platform', 'unknown') or 'unknown'),
                str(getattr(row, 'source_name', partner_name) or partner_name),
            )
            observed_counts[key] += 1

    if meta.get('source') == 'merged' and isinstance(meta.get('inputs'), list):
        for item in meta['inputs']:
            platform = item.get('source') or ('qqnt' if 'qq' in str(item.get('csv', '')).lower() else 'wechat')
            alias = item.get('partner_name') or partner_name
            platform_rows.append({
                'platform': platform,
                'alias': alias,
                'message_count': int(item.get('message_count') or observed_counts.get((platform, alias), 0)),
            })
    else:
        for (platform, alias), count in observed_counts.items():
            platform_rows.append({
                'platform': platform,
                'alias': alias,
                'message_count': int(count),
            })

    platform_rows.sort(key=lambda item: (-item['message_count'], item['platform'], item['alias']))
    alias_summary = '；'.join(f'{item["platform"]}: {item["alias"]}' for item in platform_rows) if platform_rows else partner_name
    return {
        'platforms': platform_rows,
        'alias_summary': alias_summary,
        'platform_count': len(platform_rows),
    }
