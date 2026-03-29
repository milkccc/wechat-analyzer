from __future__ import annotations

from collections import Counter

import pandas as pd


_ROLE_KEYWORDS = {
    '同学': ['同学', '实验室', '学校', '课程', '答辩', '开题', '宿舍', '国奖'],
    '朋友': ['哈哈', '周末', '吃', '玩', '见家长', '回家', '女朋友', '一起'],
    '导师': ['老师', '导师', '组会', '课题', '论文', '指导', '开题'],
    '暧昧对象': ['喜欢', '想你', '晚安', '想见', '想你了', '约会', '抱抱', '宝贝'],
    '工作搭子': ['leader', 'mentor', '需求', '上线', '实习', 'offer', '项目', '转正'],
}


def analyze(df: pd.DataFrame, topic_data: dict, interaction_data: dict) -> dict:
    if df is None or len(df) == 0:
        return {'primary_role': '未知', 'scores': [], 'reasons': []}

    texts = df['content'].astype(str).tolist()
    topic_counts = {item['topic']: item['count'] for item in topic_data.get('overall', [])}
    interaction_starts = {item['name']: item['count'] for item in interaction_data.get('initiations', [])}

    scores = Counter()
    for role, words in _ROLE_KEYWORDS.items():
        scores[role] += sum(any(word.lower() in text.lower() for word in words) for text in texts)

    scores['工作搭子'] += topic_counts.get('求职', 0) // 15 + topic_counts.get('技术', 0) // 20
    scores['导师'] += topic_counts.get('科研', 0) // 12
    scores['朋友'] += topic_counts.get('生活', 0) // 10
    scores['同学'] += topic_counts.get('科研', 0) // 18 + topic_counts.get('生活', 0) // 18

    if interaction_starts:
        total_starts = sum(interaction_starts.values())
        if total_starts > 0 and max(interaction_starts.values()) / total_starts < 0.7:
            scores['朋友'] += 2
            scores['同学'] += 1

    ranked = scores.most_common()
    if not ranked:
        return {'primary_role': '未知', 'scores': [], 'reasons': []}

    primary_role = ranked[0][0]
    reasons = []
    if topic_counts.get('科研', 0) >= max(topic_counts.get('生活', 0), 1):
        reasons.append('科研和学业相关话题占比较高。')
    if topic_counts.get('求职', 0) + topic_counts.get('技术', 0) >= max(topic_counts.get('生活', 0), 1):
        reasons.append('求职、技术和协作型信息交换很多。')
    if any('老师' in text or '导师' in text for text in texts):
        reasons.append('对话里直接出现老师、导师等强角色词。')
    if not reasons:
        reasons.append('互动里兼有任务推进和日常交流，但某一类角色词更集中。')

    return {
        'primary_role': primary_role,
        'scores': [{'role': role, 'score': int(score)} for role, score in ranked],
        'reasons': reasons[:3],
    }
