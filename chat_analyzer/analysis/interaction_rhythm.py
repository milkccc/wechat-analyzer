from __future__ import annotations

import statistics

import pandas as pd


_SESSION_GAP = pd.Timedelta(hours=6)


def _humanize_seconds(seconds: float | None) -> str:
    if seconds is None:
        return '样本不足'
    if seconds < 60:
        return f'{int(seconds)} 秒'
    if seconds < 3600:
        return f'{seconds / 60:.1f} 分钟'
    if seconds < 86400:
        return f'{seconds / 3600:.1f} 小时'
    return f'{seconds / 86400:.1f} 天'


def analyze(df: pd.DataFrame, self_name: str = '我', partner_name: str = '对方') -> dict:
    if df is None or len(df) == 0:
        return {
            'self_reply_median': '样本不足',
            'partner_reply_median': '样本不足',
            'session_count': 0,
            'session_average_length': 0.0,
            'initiations': [],
            'longest_streak': [],
        }

    frame = df.sort_values('datetime').reset_index(drop=True).copy()

    self_reply = []
    partner_reply = []
    streaks = []
    current_streak_sender = None
    current_streak_len = 0

    session_starts = []
    session_lengths = []
    last_session_start_idx = 0

    for idx in range(len(frame)):
        row = frame.iloc[idx]
        sender = int(row['is_sender'])

        if current_streak_sender == sender:
            current_streak_len += 1
        else:
            if current_streak_sender is not None:
                streaks.append((current_streak_sender, current_streak_len))
            current_streak_sender = sender
            current_streak_len = 1

        if idx == 0:
            session_starts.append(sender)
            continue

        prev = frame.iloc[idx - 1]
        gap = row['datetime'] - prev['datetime']
        if gap > _SESSION_GAP:
            session_lengths.append(idx - last_session_start_idx)
            last_session_start_idx = idx
            session_starts.append(sender)

        if sender != int(prev['is_sender']):
            delay = max(float(gap.total_seconds()), 0.0)
            if sender == 1:
                self_reply.append(delay)
            else:
                partner_reply.append(delay)

    if current_streak_sender is not None:
        streaks.append((current_streak_sender, current_streak_len))
    session_lengths.append(len(frame) - last_session_start_idx)

    initiations = [
        {'name': self_name, 'count': int(sum(s == 1 for s in session_starts))},
        {'name': partner_name, 'count': int(sum(s == 0 for s in session_starts))},
    ]
    longest_streak = sorted(
        [
            {'name': self_name if sender == 1 else partner_name, 'count': int(length)}
            for sender, length in streaks
        ],
        key=lambda item: item['count'],
        reverse=True,
    )[:3]

    self_median = statistics.median(self_reply) if self_reply else None
    partner_median = statistics.median(partner_reply) if partner_reply else None

    return {
        'self_reply_median': _humanize_seconds(self_median),
        'partner_reply_median': _humanize_seconds(partner_median),
        'session_count': len(session_starts),
        'session_average_length': round(sum(session_lengths) / max(len(session_lengths), 1), 1),
        'initiations': initiations,
        'longest_streak': longest_streak,
    }
