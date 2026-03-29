from __future__ import annotations

import pandas as pd


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def analyze(
    df: pd.DataFrame,
    ai_input: dict | None,
    partner_ai_input: dict | None,
    topic_data: dict,
    interaction_data: dict,
) -> dict:
    total_messages = int(len(df)) if df is not None else 0
    span_days = max(1, int((df['datetime'].max() - df['datetime'].min()).days)) if total_messages else 1
    sample_count = int((ai_input or {}).get('features', {}).get('sample_count', 0) or 0)
    partner_sample_count = int((partner_ai_input or {}).get('features', {}).get('sample_count', 0) or 0)

    sufficiency = _clamp(
        min(total_messages / 120.0, 40) +
        min(span_days / 18.0, 35) +
        min((sample_count + partner_sample_count) / 5.0, 25)
    )

    overall_topics = topic_data.get('overall', [])
    total_topic = max(sum(item['count'] for item in overall_topics), 1)
    dominant_share = max((item['count'] for item in overall_topics), default=0) / total_topic
    scene_bias = _clamp(100 - dominant_share * 65)

    session_count = int(interaction_data.get('session_count', 0) or 0)
    interaction_score = _clamp(min(session_count * 4.5, 100))

    notes = []
    if dominant_share >= 0.65:
        notes.append('话题高度集中，部分人格或关系判断会被单一场景放大。')
    if span_days < 60:
        notes.append('时间跨度较短，更容易反映某个阶段而不是长期关系。')
    if total_messages < 300:
        notes.append('有效文本量偏少，结论更适合作为线索而不是定论。')
    if not notes:
        notes.append('样本量、时间跨度和互动覆盖度都处于可参考区间。')

    return {
        'sample_sufficiency': sufficiency,
        'scene_bias': scene_bias,
        'interaction_coverage': interaction_score,
        'notes': notes[:3],
    }
