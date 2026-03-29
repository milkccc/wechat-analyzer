"""
stats.py — 从 DataFrame 计算量化统计指标
"""

import re
from collections import Counter

import jieba
import pandas as pd

_EMOJI_RE = re.compile(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF]+')

_STOPWORDS = {
    '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '这', '那',
    '就', '都', '和', '与', '但', '也', '很', '有', '没', '不', '一', '个',
    '上', '对', '说', '好', '要', '么', '啊', '呢', '吧', '哦', '嗯', '然后',
    '所以', '因为', '如果', '可以', '还是', '已经', '什么', '怎么', '为什么',
    '就是', '还有', '然后', '其实', '感觉', '觉得', '现在', '时候', '一个',
    '这个', '那个', '一下', '一起', '一直', '一样', '一点', '一些',
}


def compute(df: pd.DataFrame) -> dict:
    """
    返回包含以下键的 dict：
      total_messages, total_chars, avg_length, date_range,
      daily, hourly, monthly, weekday,
      word_freq (Counter), emoji_freq (Counter), length_series (Series)
    """
    contents = df['content'].fillna('').astype(str)
    lengths = contents.str.len()

    # 分词、emoji、长度在一轮遍历里完成，避免大聊天记录时额外构造超大字符串。
    word_freq: Counter = Counter()
    emoji_freq: Counter = Counter()
    for text in contents:
        emoji_freq.update(_EMOJI_RE.findall(text))
        word_freq.update(
            w for w in jieba.cut(text)
            if len(w) > 1 and w not in _STOPWORDS and not w.isspace()
        )

    return {
        'total_messages': len(df),
        'total_chars':    int(lengths.sum()),
        'avg_length':     round(lengths.mean(), 1),
        'date_range':     (df['datetime'].min(), df['datetime'].max()),
        'daily':          df.groupby('date').size(),
        'hourly':         df.groupby('hour').size().reindex(range(24), fill_value=0),
        'monthly':        df.groupby('month').size(),
        'weekday':        df.groupby('weekday').size().reindex(range(7), fill_value=0),
        'word_freq':      word_freq,
        'emoji_freq':     emoji_freq,
        'length_series':  lengths,
    }
