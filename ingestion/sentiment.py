from __future__ import annotations
import re
from typing import List, Tuple

NEGATIVE_INDICATORS = [
    "hate", "terrible", "awful", "worst", "overpriced", "waste",
    "disappointed", "frustrating", "annoying", "slow", "buggy",
    "expensive", "not worth", "doesn't work", "doesnt work",
    "broken", "unreliable", "cancelled", "canceled", "refund",
    "switching away", "leaving", "moving to", "alternative to",
    "looking for replacement", "done with", "tired of", "fed up",
    "poor support", "no support", "crash", "crashes", "outrageous",
    "rip off", "ripoff", "scam", "misleading",
]

POSITIVE_INDICATORS = [
    "love", "great", "excellent", "amazing", "best", "fantastic",
    "helpful", "valuable", "worth it", "recommend", "solid",
    "impressive", "reliable", "intuitive", "powerful",
]

COMPETITOR_NAMES = ["ahrefs", "semrush", "moz", "screaming frog", "surferseo", "surfer"]


def classify_sentiment(text: str, targets: List[str] | None = None) -> List[Tuple[str, str, float, str]]:
    if targets is None:
        targets = _detect_targets(text)

    results = []
    text_lower = text.lower()
    sentences = re.split(r"[.!?]+", text_lower)

    for target in targets:
        target_lower = target.lower()
        if target_lower not in text_lower:
            continue

        neg_count = 0
        pos_count = 0
        relevant_quote = ""

        for sent in sentences:
            if target_lower in sent:
                neg_in_sent = sum(1 for w in NEGATIVE_INDICATORS if w in sent)
                pos_in_sent = sum(1 for w in POSITIVE_INDICATORS if w in sent)
                if neg_in_sent + pos_in_sent > 0:
                    relevant_quote = sent.strip()[:200]
                neg_count += neg_in_sent
                pos_count += pos_in_sent

        if neg_count == 0 and pos_count == 0:
            continue

        total = neg_count + pos_count
        if neg_count > pos_count:
            sentiment = "negative"
            confidence = min(0.95, 0.5 + (neg_count - pos_count) / max(total, 1) * 0.45)
        elif pos_count > neg_count:
            sentiment = "positive"
            confidence = min(0.95, 0.5 + (pos_count - neg_count) / max(total, 1) * 0.45)
        else:
            sentiment = "neutral"
            confidence = 0.5

        results.append((target, sentiment, round(confidence, 2), relevant_quote))

    return results


def _detect_targets(text: str) -> List[str]:
    text_lower = text.lower()
    return [name for name in COMPETITOR_NAMES if name in text_lower]
