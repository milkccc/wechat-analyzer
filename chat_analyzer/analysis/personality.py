"""
personality.py — 人格分析逻辑

阶段一：程序化提取语言特征（客观指标）
阶段二：优先调用外部模型；若不可用，则走本地启发式生成结构化 JSON
"""

import json
import os
import re
from datetime import datetime
from typing import List, Optional

import pandas as pd
from anthropic import Anthropic

# ── 阶段一：语言特征提取 ──────────────────────────────────────────────────────

_OPINION_WORDS  = ['我觉得', '我认为', '感觉', '我想', '我希望', '在我看来', '我以为']
_EMOTION_POS    = ['开心', '高兴', '快乐', '喜欢', '爱', '棒', '不错', '满意', '幸福', '兴奋']
_EMOTION_NEG    = ['难过', '伤心', '生气', '烦', '焦虑', '担心', '害怕', '失望', '委屈', '无聊']
_PLANNING_WORDS = ['打算', '计划', '准备', '决定', '目标', '将来', '以后', '未来']
_CERTAINTY_POS  = ['一定', '肯定', '确定', '绝对', '必须']
_CERTAINTY_NEG  = ['可能', '也许', '大概', '应该', '或许', '不确定']
_FIRST_PERSON   = ['我', '我的', '我觉得', '我认为', '我想']
_SOCIAL_WORDS   = ['朋友', '大家', '我们', '一起', '聚', '出去', '玩']
_STRESS_WORDS   = ['焦虑', '担心', '害怕', '压抑', '委屈', '难受', '崩溃', '烦', '累', '慌']
_CURIOUS_WORDS  = ['ai', '算法', '系统', '架构', '研究', '论文', '模型', '数据', '技术', '为什么', '怎么']
_KIND_WORDS     = ['谢谢', '感谢', '辛苦', '可以', '建议', '别压力太大', '没事', '帮', '方便']
_ORDER_WORDS    = ['计划', '准备', '安排', '进度', '总结', '先', '再', '然后', '上线', '交付']
_SCENE_HINTS    = [
    ('求职与面试', ['面试', '秋招', '暑期', 'offer', '简历', '笔试', '实习', 'leader', 'mentor']),
    ('科研与论文', ['论文', '导师', '文献', '组会', '开题', '实验室', '课题', '横向']),
    ('工程与代码', ['代码', 'java', 'go', 'python', 'sql', 'mysql', 'debug', '系统', '架构']),
    ('生活与关系', ['房', '租', '回家', '女朋友', '家长', '吃', '周末', '学校']),
]
_BIG5_KEYS = ['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism']
_DEFAULT_BIG5_NOTES = {
    'openness': '更关注抽象想法、趋势判断和新路径。',
    'conscientiousness': '表达里能看到对进度、安排和执行的关注。',
    'extraversion': '整体呈现的是互动热度，而不是现实中的社交能力总量。',
    'agreeableness': '更多反映聊天里的体谅、协作和建议方式。',
    'neuroticism': '更多反映在高压场景中的波动和不确定感表达。',
}


def _count_hits(texts: List[str], words: List[str]) -> int:
    return sum(1 for t in texts if any(w.lower() in t.lower() for w in words))


def _clip(score: float, low: int = 10, high: int = 90) -> int:
    return max(low, min(high, int(round(score))))


def _level(score: int) -> str:
    if score >= 75:
        return '高'
    if score >= 62:
        return '中高'
    if score >= 45:
        return '中'
    if score >= 30:
        return '中低'
    return '低'


def _strength(delta: int) -> str:
    return '明显' if abs(delta) >= 12 else '轻微'


def _clean_text(value: object, default: str = '') -> str:
    text = str(value or '').strip()
    return text or default


def _coerce_score(value: object, default: int = 50) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = default
    return max(0, min(100, numeric))


def _normalize_confidence(value: object, default: str = '中') -> str:
    text = _clean_text(value, default)
    if text in {'低', '中', '中高', '高'}:
        return text
    if text in {'明显', '较高'}:
        return '中高'
    return default


def normalize_result(result: Optional[dict]) -> dict:
    """把外部或本地人格结果收敛到稳定结构，减少报告渲染时的分支和空字段。"""
    payload = result if isinstance(result, dict) else {}

    big5_src = payload.get('big5', {}) if isinstance(payload.get('big5'), dict) else {}
    big5: dict = {}
    for key in _BIG5_KEYS:
        item = big5_src.get(key, {}) if isinstance(big5_src.get(key), dict) else {}
        score = _coerce_score(item.get('score', 50))
        big5[key] = {
            'score': score,
            'level': _clean_text(item.get('level'), _level(score)),
            'evidence': _clean_text(item.get('evidence')),
            'note': _clean_text(item.get('note'), _DEFAULT_BIG5_NOTES[key]),
        }

    mbti_src = payload.get('mbti', {}) if isinstance(payload.get('mbti'), dict) else {}
    dims_src = mbti_src.get('dims', {}) if isinstance(mbti_src.get('dims'), dict) else {}
    dims = {}
    for dim, fallback in {'EI': 'I', 'SN': 'N', 'TF': 'T', 'JP': 'P'}.items():
        item = dims_src.get(dim, {}) if isinstance(dims_src.get(dim), dict) else {}
        lean = _clean_text(item.get('lean'), fallback).upper()[:1]
        if lean not in {'E', 'I', 'S', 'N', 'T', 'F', 'J', 'P'}:
            lean = fallback
        dims[dim] = {
            'lean': lean,
            'strength': _clean_text(item.get('strength'), '轻微'),
            'reason': _clean_text(item.get('reason')),
        }

    style_src = payload.get('style', {}) if isinstance(payload.get('style'), dict) else {}
    strengths = [_clean_text(v) for v in style_src.get('strengths', []) if _clean_text(v)]
    fun_facts = [_clean_text(v) for v in style_src.get('fun_facts', []) if _clean_text(v)]

    return {
        'big5': big5,
        'mbti': {
            'type': _clean_text(mbti_src.get('type'), ''.join(dims[k]['lean'] for k in ['EI', 'SN', 'TF', 'JP'])),
            'confidence': _normalize_confidence(mbti_src.get('confidence')),
            'note': _clean_text(
                mbti_src.get('note'),
                '这是基于聊天语言的风格映射，更适合作为沟通风格参考。'
            ),
            'dims': dims,
        },
        'style': {
            'one_line': _clean_text(style_src.get('one_line')),
            'summary': _clean_text(style_src.get('summary')),
            'strengths': strengths[:3],
            'fun_facts': fun_facts[:3],
        },
        'reliability': _clean_text(payload.get('reliability')),
    }


def _first_match(messages: List[str], words: List[str], fallback: str) -> str:
    for msg in messages:
        lowered = msg.lower()
        if any(w.lower() in lowered for w in words):
            return msg
    return fallback


def _short_quote(message: str, limit: int = 30) -> str:
    msg = ' '.join(str(message).split())
    return msg if len(msg) <= limit else msg[: limit - 1] + '…'


def _top_word_strings(top_words: List[dict]) -> List[str]:
    words = []
    for item in top_words or []:
        word = str(item.get('word', '')).strip()
        if word:
            words.append(word)
    return words


def _dominant_scenes(messages: List[str], top_words: List[dict]) -> List[str]:
    joined = ' '.join(messages).lower()
    tw = ' '.join(_top_word_strings(top_words)).lower()
    ranked = []
    for label, words in _SCENE_HINTS:
        score = sum(joined.count(w.lower()) + tw.count(w.lower()) * 2 for w in words)
        if score:
            ranked.append((score, label))
    ranked.sort(reverse=True)
    return [label for _, label in ranked[:2]] or ['日常沟通']


def _extract_date_range(ai_input: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    stats_summary = ai_input.get('stats_summary', {})
    dr = stats_summary.get('date_range') or []
    if not isinstance(dr, list) or len(dr) != 2:
        return None, None
    try:
        return tuple(datetime.strptime(x, '%Y-%m-%d') for x in dr)
    except Exception:
        return None, None


def _rate(texts: List[str], words: List[str]) -> float:
    """words 中任意词出现在消息里的比例"""
    hits = sum(1 for t in texts if any(w in t for w in words))
    return round(hits / len(texts) * 100, 1) if texts else 0.0


def extract_features(messages: List[str]) -> dict:
    """从消息列表中提取可量化的语言特征"""
    if not messages:
        return {}
    avg_len = round(sum(len(m) for m in messages) / len(messages), 1)
    return {
        'avg_length':       avg_len,
        'opinion_rate':     _rate(messages, _OPINION_WORDS),
        'positive_emotion': _rate(messages, _EMOTION_POS),
        'negative_emotion': _rate(messages, _EMOTION_NEG),
        'planning_rate':    _rate(messages, _PLANNING_WORDS),
        'certainty_high':   _rate(messages, _CERTAINTY_POS),
        'certainty_low':    _rate(messages, _CERTAINTY_NEG),
        'question_rate':    round(sum(1 for m in messages if '?' in m or '？' in m) / len(messages) * 100, 1),
        'social_rate':      _rate(messages, _SOCIAL_WORDS),
        'first_person_rate':_rate(messages, _FIRST_PERSON),
        'sample_count':     len(messages),
        'total_words':      sum(len(m) for m in messages),
    }


def _build_big5(ai_input: dict) -> dict:
    messages = [str(x) for x in ai_input.get('sample_messages', []) if str(x).strip()]
    features = ai_input.get('features', {}) or {}
    top_words = ai_input.get('top_words', []) or []
    top_word_strings = _top_word_strings(top_words)

    avg_length = float(features.get('avg_length', 0) or 0)
    opinion_rate = float(features.get('opinion_rate', 0) or 0)
    positive = float(features.get('positive_emotion', 0) or 0)
    negative = float(features.get('negative_emotion', 0) or 0)
    planning = float(features.get('planning_rate', 0) or 0)
    question_rate = float(features.get('question_rate', 0) or 0)
    social_rate = float(features.get('social_rate', 0) or 0)
    certainty_high = float(features.get('certainty_high', 0) or 0)
    certainty_low = float(features.get('certainty_low', 0) or 0)
    first_person = float(features.get('first_person_rate', 0) or 0)

    curious_hits = _count_hits(messages, _CURIOUS_WORDS)
    stress_hits = _count_hits(messages, _STRESS_WORDS)
    kind_hits = _count_hits(messages, _KIND_WORDS)
    order_hits = _count_hits(messages, _ORDER_WORDS)

    openness = _clip(
        46 + avg_length * 0.55 + opinion_rate * 0.18 + curious_hits * 1.8
        + (6 if any(w.lower() in {'ai', '算法', '模型', '论文'} for w in top_word_strings) else 0)
    )
    conscientiousness = _clip(
        42 + planning * 1.2 + certainty_high * 0.5 + order_hits * 2.2
        + (4 if any(w in top_word_strings for w in ['总结', '进度', '上线']) else 0)
        - negative * 0.2
    )
    extraversion = _clip(
        38 + social_rate * 1.3 + question_rate * 0.45 + positive * 0.35
        + (3 if any(w in top_word_strings for w in ['哈哈', '大家', '一起']) else 0)
        - (4 if first_person > 45 else 0)
    )
    agreeableness = _clip(
        45 + kind_hits * 3.2 + positive * 0.45 + social_rate * 0.35
        - certainty_high * 0.15
    )
    neuroticism = _clip(
        28 + negative * 3.0 + certainty_low * 0.35 + stress_hits * 5.0
        + (4 if any(w in top_word_strings for w in ['焦虑', '担心', '害怕']) else 0)
    )

    scores = {
        'openness': openness,
        'conscientiousness': conscientiousness,
        'extraversion': extraversion,
        'agreeableness': agreeableness,
        'neuroticism': neuroticism,
    }

    evidence_words = {
        'openness': _CURIOUS_WORDS,
        'conscientiousness': _ORDER_WORDS,
        'extraversion': ['大家', '一起', '飞哥', '哈哈', '问问', '聚'],
        'agreeableness': _KIND_WORDS,
        'neuroticism': _STRESS_WORDS,
    }
    fallback_msg = messages[0] if messages else '样本不足'
    notes = {
        'openness': '话题经常从眼前问题延伸到技术路径、行业趋势或更长线的选择。',
        'conscientiousness': '做事会回到进度、安排和落地动作，执行感较稳定。',
        'extraversion': '更像通过提问和互动推动对话，而不是单纯铺陈自己的想法。',
        'agreeableness': '语言里能看到照顾对方感受、给建议和缓冲压力的倾向。',
        'neuroticism': '遇到不确定任务或评价场景时，情绪波动会明显进入语言表面。',
    }

    return {
        trait: {
            'score': score,
            'level': _level(score),
            'evidence': _short_quote(_first_match(messages, evidence_words[trait], fallback_msg)),
            'note': notes[trait],
        }
        for trait, score in scores.items()
    }


def _build_mbti(ai_input: dict, big5: dict) -> dict:
    features = ai_input.get('features', {}) or {}
    top_words = _top_word_strings(ai_input.get('top_words', []))

    e_score = big5.get('extraversion', {}).get('score', 50) - 50
    n_score = big5.get('openness', {}).get('score', 50) - 50 + (4 if '感觉' in top_words else 0)
    f_score = big5.get('agreeableness', {}).get('score', 50) - 50 + float(features.get('negative_emotion', 0) or 0) * 0.3
    j_score = big5.get('conscientiousness', {}).get('score', 50) - 50 - float(features.get('certainty_low', 0) or 0) * 0.15

    dims = {
        'EI': {
            'lean': 'E' if e_score >= 0 else 'I',
            'strength': _strength(int(e_score)),
            'reason': '更常通过互动、追问和来回交换信息带动对话。' if e_score >= 0 else '更常先给判断和建议，社交热度不高但表达持续。'
        },
        'SN': {
            'lean': 'N' if n_score >= 0 else 'S',
            'strength': _strength(int(n_score)),
            'reason': '经常把问题抽象成趋势、结构、方向和可能性。' if n_score >= 0 else '更偏向具体事实、步骤和已发生的细节。'
        },
        'TF': {
            'lean': 'F' if f_score >= 0 else 'T',
            'strength': _strength(int(f_score)),
            'reason': '判断时会把关系感受、压力状态和氛围一起纳入。' if f_score >= 0 else '判断时更依赖逻辑拆解、可行性和问题结构。'
        },
        'JP': {
            'lean': 'J' if j_score >= 0 else 'P',
            'strength': _strength(int(j_score)),
            'reason': '语言里有比较稳定的计划感和推进顺序。' if j_score >= 0 else '更像边走边调，先把问题想清再决定怎么收束。'
        },
    }

    mbti_type = ''.join(dims[key]['lean'] for key in ['EI', 'SN', 'TF', 'JP'])
    separations = [abs(int(e_score)), abs(int(n_score)), abs(int(f_score)), abs(int(j_score))]
    avg_sep = sum(separations) / len(separations)
    confidence = '中高' if avg_sep >= 14 else ('中' if avg_sep >= 8 else '低')
    note = '这是基于聊天语言的风格映射，场景主要集中在求职、科研和日常互助，因此更适合作为沟通风格参考。'

    return {
        'type': mbti_type,
        'confidence': confidence,
        'note': note,
        'dims': dims,
    }


def _build_style(ai_input: dict, big5: dict, mbti: dict) -> dict:
    messages = [str(x) for x in ai_input.get('sample_messages', []) if str(x).strip()]
    features = ai_input.get('features', {}) or {}
    top_words = _top_word_strings(ai_input.get('top_words', []))
    scenes = _dominant_scenes(messages, ai_input.get('top_words', []))
    scene_text = '、'.join(scenes)

    neuroticism = big5.get('neuroticism', {}).get('score', 50)
    conscientiousness = big5.get('conscientiousness', {}).get('score', 50)
    openness = big5.get('openness', {}).get('score', 50)
    agreeableness = big5.get('agreeableness', {}).get('score', 50)

    if neuroticism >= 68:
        one_line = f'像一台同时开着{scene_text}频道、还会实时播报情绪温度的多线程窗口。'
    elif conscientiousness >= 62 and agreeableness >= 65:
        one_line = f'像一个会把{scene_text}都拆成可执行步骤、顺手还帮人兜情绪的稳定搭子。'
    elif openness >= 70:
        one_line = f'像一个会把{scene_text}不断连到新想法和新路径上的脑力路由器。'
    else:
        one_line = f'像一个在{scene_text}之间来回切换、边想边推进的人。'

    summary_parts = []
    if '求职与面试' in scenes:
        summary_parts.append('聊天重心明显落在求职、面试和职业选择上')
    if '科研与论文' in scenes:
        summary_parts.append('同时也持续处理论文、导师和实验室推进')
    if '工程与代码' in scenes:
        summary_parts.append('技术讨论里会频繁切到代码、架构和工具层面')
    if neuroticism >= 68:
        summary_parts.append('遇到不确定性时，焦虑和压力会直接进入表达')
    elif agreeableness >= 65:
        summary_parts.append('整体语气偏体谅和协商，不太用强压式表达')

    summary = '，'.join(summary_parts[:3]) + '。'
    if summary == '。':
        summary = '聊天内容覆盖学习、工作和日常安排，语言风格比较稳定。'

    strengths = []
    if openness >= 68:
        strengths.append('能快速把具体问题上升到更大的方向和路径选择')
    if conscientiousness >= 60:
        strengths.append('会自然回到节奏、进度和落地动作，不容易只停在空谈')
    if agreeableness >= 64:
        strengths.append('给建议时通常会顾及对方状态，沟通阻力相对小')
    if neuroticism >= 68:
        strengths.append('对风险和变化很敏感，能较早感知不对劲的地方')
    if len(strengths) < 3:
        strengths.append('提问密度高，能把对话快速推进到关键问题')
    strengths = strengths[:3]

    fun_facts = []
    if top_words:
        fun_facts.append(f'高频词里最显眼的是“{top_words[0]}”，说明它几乎成了这段关系里的口头标签。')
    if float(features.get('question_rate', 0) or 0) >= 12:
        fun_facts.append('提问句很多，说明这类聊天不是单向输出，而是持续拿对话来试探判断。')
    elif float(features.get('first_person_rate', 0) or 0) >= 40:
        fun_facts.append('第一人称比例偏高，说明聊天常常直接围着自己的处境、计划和情绪展开。')
    if not fun_facts:
        fun_facts.append('样本里的话题切换很快，说明这段聊天承担了信息同步之外的情绪缓冲功能。')

    return {
        'one_line': one_line,
        'summary': summary,
        'strengths': strengths,
        'fun_facts': fun_facts[:2],
    }


def generate_local_result(ai_input: dict) -> dict:
    big5 = _build_big5(ai_input)
    mbti = _build_mbti(ai_input, big5)
    style = _build_style(ai_input, big5, mbti)
    sample_count = int((ai_input.get('features') or {}).get('sample_count', 0) or 0)
    dr0, dr1 = _extract_date_range(ai_input)
    if dr0 and dr1:
        span = f'{dr0.strftime("%Y-%m-%d")} 到 {dr1.strftime("%Y-%m-%d")}'
    else:
        span = '当前样本覆盖期'
    reliability = f'本结果基于 {sample_count} 条采样消息与规则化特征推断生成，覆盖时间为 {span}，适合作为沟通风格参考，不等同于正式心理测评。'
    return normalize_result({
        'big5': big5,
        'mbti': mbti,
        'style': style,
        'reliability': reliability,
    })


# ── 阶段二：Claude 人格分析 ───────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一位语言学人格研究者，正在分析一位用户的微信聊天记录样本。

【数据概况】
- 样本消息数：{n} 条（已过滤噪音，按时间均匀采样）
- 覆盖时间跨度：约 {months} 个月
- 语言特征摘要：{features_str}

【消息样本】
---
{messages_text}
---

【分析要求】
请基于语言模式推断人格特质，输出严格符合以下格式的 JSON：

```json
{{
  "big5": {{
    "openness":          {{"score": 0-100, "level": "低/中/高", "evidence": "引用1条原文", "note": "一句解读"}},
    "conscientiousness": {{"score": 0-100, "level": "低/中/高", "evidence": "引用1条原文", "note": "一句解读"}},
    "extraversion":      {{"score": 0-100, "level": "低/中/高", "evidence": "引用1条原文", "note": "一句解读"}},
    "agreeableness":     {{"score": 0-100, "level": "低/中/高", "evidence": "引用1条原文", "note": "一句解读"}},
    "neuroticism":       {{"score": 0-100, "level": "低/中/高", "evidence": "引用1条原文", "note": "一句解读"}}
  }},
  "mbti": {{
    "type":       "四字母类型",
    "confidence": "低/中/高",
    "note":       "一句话说明置信度原因",
    "dims": {{
      "EI": {{"lean": "E或I", "strength": "明显/轻微", "reason": "简短理由"}},
      "SN": {{"lean": "S或N", "strength": "明显/轻微", "reason": "简短理由"}},
      "TF": {{"lean": "T或F", "strength": "明显/轻微", "reason": "简短理由"}},
      "JP": {{"lean": "J或P", "strength": "明显/轻微", "reason": "简短理由"}}
    }}
  }},
  "style": {{
    "one_line":    "用一句话描述这个人，要生动、有画面感",
    "summary":     "2-3句话描述聊天风格",
    "strengths":   ["特点1", "特点2", "特点3"],
    "fun_facts":   ["有趣发现1", "有趣发现2"]
  }},
  "reliability": "关于本次分析可靠性的简短说明（样本量、语言偏差等）"
}}
```

重要提醒：
- MBTI 在学术上信效度有限，请在 confidence 和 note 中如实体现不确定性
- Big Five 具有更强研究支撑，是本分析的重点
- evidence 必须是消息样本中真实出现的原文片段，不要编造
"""


def analyze(messages: List[str], date_range: tuple) -> dict:
    """
    调用 Claude 进行完整人格分析。
    返回结构化 dict，若 JSON 解析失败则返回 {'raw': ..., 'parse_error': True}。
    """
    client = Anthropic()

    months = max(1, (date_range[1] - date_range[0]).days // 30)
    features = extract_features(messages)
    features_str = ', '.join(f'{k}={v}' for k, v in features.items())
    messages_text = '\n'.join(f'• {m}' for m in messages)

    prompt = _PROMPT_TEMPLATE.format(
        n=len(messages),
        months=months,
        features_str=features_str,
        messages_text=messages_text,
    )

    resp = client.messages.create(
        model='claude-opus-4-6',
        max_tokens=2500,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = resp.content[0].text

    # 提取 JSON 块
    m = re.search(r'```json\s*([\s\S]+?)\s*```', raw)
    json_str = m.group(1) if m else raw
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {'raw': raw, 'parse_error': True}


def generate_result(ai_input: dict, prefer_remote: bool = True) -> dict:
    """优先尝试远程模型；不可用时自动回退到本地启发式生成。"""
    messages = [str(x) for x in ai_input.get('sample_messages', []) if str(x).strip()]
    dr0, dr1 = _extract_date_range(ai_input)

    if prefer_remote and os.getenv('ANTHROPIC_API_KEY') and messages and dr0 and dr1:
        try:
            remote = analyze(messages, (dr0, dr1))
            if isinstance(remote, dict) and not remote.get('parse_error'):
                return normalize_result(remote)
        except Exception:
            pass

    return generate_local_result(ai_input)
