from __future__ import annotations

import pandas as pd

from .confidence import analyze as analyze_confidence
from .emotion_periods import analyze as analyze_emotion
from .events import analyze as analyze_events
from .identity_merge import analyze as analyze_identity
from .interaction_rhythm import analyze as analyze_interaction
from .role_inference import analyze as analyze_role
from .topic_evolution import analyze as analyze_topics


def analyze_advanced_insights(
    df: pd.DataFrame,
    meta: dict,
    self_name: str,
    partner_name: str,
    ai_input: dict | None = None,
    partner_ai_input: dict | None = None,
) -> dict:
    topics = analyze_topics(df)
    interaction = analyze_interaction(df, self_name=self_name, partner_name=partner_name)
    emotion = analyze_emotion(df)
    role = analyze_role(df, topics, interaction)
    events = analyze_events(df)
    identity = analyze_identity(df, meta, partner_name)
    confidence = analyze_confidence(df, ai_input, partner_ai_input, topics, interaction)

    return {
        'identity_merge': identity,
        'emotion_periods': emotion,
        'topic_evolution': topics,
        'interaction_rhythm': interaction,
        'role_inference': role,
        'events': events,
        'confidence': confidence,
    }
