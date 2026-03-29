from __future__ import annotations

from collections import defaultdict

import pandas as pd


_EVENT_PATTERNS = {
    'offer': ('Offer / 签约', ['offer', '签约', '三方', '薪资', '入职']),
    '答辩': ('答辩 / 开题', ['答辩', '开题', '报告', '论文']),
    '导师冲突': ('导师 / 老师压力', ['导师', '老师', '盯上', '压抑', '生气', '难受']),
    '实习入职': ('实习 / 入职', ['实习', '入职', 'leader', 'mentor', '转正']),
    '搬家': ('搬家 / 住房', ['搬', '租房', '房子', '宿舍', '甲醛']),
    '面试': ('面试进展', ['面试', '一面', '二面', 'hr', '笔试']),
}


def analyze(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {'events': []}

    candidates = []
    for row in df[['datetime', 'content', 'is_sender']].itertuples(index=False):
        text = str(row.content or '')
        lowered = text.lower()
        for key, (label, words) in _EVENT_PATTERNS.items():
            score = sum(word.lower() in lowered for word in words)
            if score <= 0:
                continue
            candidates.append({
                'category': key,
                'label': label,
                'date': row.datetime.strftime('%Y-%m-%d'),
                'sender': '我' if int(row.is_sender) == 1 else '对方',
                'score': score,
                'snippet': text[:80] + ('…' if len(text) > 80 else ''),
            })

    if not candidates:
        return {'events': []}

    grouped: dict[tuple[str, str], dict] = defaultdict(dict)
    for item in sorted(candidates, key=lambda entry: (-entry['score'], entry['date'])):
        grouped.setdefault((item['category'], item['date']), item)

    events = sorted(grouped.values(), key=lambda item: item['date'])[:12]
    return {'events': events}
