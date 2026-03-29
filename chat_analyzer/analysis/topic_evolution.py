from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd


TOPIC_KEYWORDS = {
    '求职': ['面试', 'offer', '秋招', '暑期', '实习', '简历', '笔试', '转正', 'hr', 'leader', 'mentor'],
    '科研': ['论文', '导师', '组会', '文献', '开题', '实验室', '课题', '答辩', '横向'],
    '生活': ['房', '租', '回家', '吃', '周末', '学校', '宿舍', '女朋友', '家长'],
    '技术': ['代码', 'java', 'go', 'python', 'sql', 'mysql', 'debug', '系统', '架构', '算法'],
}


def classify_topic(text: str) -> str:
    content = str(text or '')
    scores = {}
    for topic, words in TOPIC_KEYWORDS.items():
        scores[topic] = sum(word.lower() in content.lower() for word in words)
    best_topic, best_score = max(scores.items(), key=lambda item: item[1])
    return best_topic if best_score > 0 else '其他'


def analyze(df: pd.DataFrame, phase_by_month: dict[str, str] | None = None) -> dict:
    if df is None or len(df) == 0:
        return {'overall': [], 'by_period': [], 'by_phase': []}

    frame = df.copy()
    frame['topic'] = frame['content'].astype(str).apply(classify_topic)
    frame['period'] = frame['month'].astype(str)

    overall_counts = Counter(frame['topic'])
    overall = [
        {'topic': topic, 'count': int(count)}
        for topic, count in overall_counts.most_common()
    ]

    by_period = []
    for period, group in frame.groupby('period'):
        counts = Counter(group['topic'])
        top_topics = [{'topic': topic, 'count': int(count)} for topic, count in counts.most_common(3)]
        by_period.append({
            'period': period,
            'top_topics': top_topics,
        })

    phase_summary = defaultdict(Counter)
    for row in frame[['period', 'topic']].itertuples(index=False):
        stage = (phase_by_month or {}).get(row.period, '未分段')
        phase_summary[stage][row.topic] += 1

    by_phase = []
    for stage, counts in phase_summary.items():
        by_phase.append({
            'stage': stage,
            'top_topics': [{'topic': topic, 'count': int(count)} for topic, count in counts.most_common(3)],
        })

    by_period.sort(key=lambda item: item['period'])
    by_phase.sort(key=lambda item: item['stage'])
    return {
        'overall': overall,
        'by_period': by_period,
        'by_phase': by_phase,
    }
