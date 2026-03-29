from __future__ import annotations

import pandas as pd


_STRESS_WORDS = ['焦虑', '担心', '害怕', '压抑', '委屈', '烦', '难受', '慌', '崩溃', '累']
_POSITIVE_WORDS = ['开心', '高兴', '喜欢', '顺利', '不错', '放松', '快乐', '期待', '稳定']


def analyze(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {'frequency': '月', 'series': [], 'high_pressure_windows': []}

    span_days = max(1, int((df['datetime'].max() - df['datetime'].min()).days))
    freq = 'W' if span_days <= 180 else 'M'
    freq_label = '周' if freq == 'W' else '月'

    frame = df.copy()
    frame['period'] = frame['datetime'].dt.to_period(freq)
    frame['stress_hit'] = frame['content'].astype(str).apply(lambda text: sum(word in text for word in _STRESS_WORDS))
    frame['positive_hit'] = frame['content'].astype(str).apply(lambda text: sum(word in text for word in _POSITIVE_WORDS))

    grouped = frame.groupby('period').agg(
        message_count=('content', 'size'),
        stress_hits=('stress_hit', 'sum'),
        positive_hits=('positive_hit', 'sum'),
    ).reset_index()
    grouped['stress_rate'] = (grouped['stress_hits'] / grouped['message_count']).round(3)
    grouped['positive_rate'] = (grouped['positive_hits'] / grouped['message_count']).round(3)
    grouped['stress_index'] = (grouped['stress_rate'] * 100 - grouped['positive_rate'] * 40).round(1)

    series = []
    for row in grouped.itertuples(index=False):
        series.append({
            'period': str(row.period),
            'message_count': int(row.message_count),
            'stress_rate': round(float(row.stress_rate) * 100, 1),
            'positive_rate': round(float(row.positive_rate) * 100, 1),
            'stress_index': float(row.stress_index),
        })

    if grouped.empty:
        return {'frequency': freq_label, 'series': [], 'high_pressure_windows': []}

    stress_threshold = max(float(grouped['stress_index'].quantile(0.75)), 8.0)
    candidates = grouped[grouped['stress_index'] >= stress_threshold].sort_values(
        ['stress_index', 'message_count'], ascending=[False, False]
    )

    high_pressure_windows = []
    for row in candidates.head(4).itertuples(index=False):
        high_pressure_windows.append({
            'period': str(row.period),
            'stress_index': float(row.stress_index),
            'summary': '压力词显著高于积极词，属于高压窗口。',
            'message_count': int(row.message_count),
        })

    return {
        'frequency': freq_label,
        'series': series,
        'high_pressure_windows': high_pressure_windows,
    }
