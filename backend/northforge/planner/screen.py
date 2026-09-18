"""Deterministic, pre-model screening of a planner request.

Runs before the model ever sees the request text. It never blocks planning:
anything it finds becomes a ``RejectedAction`` (``source="screen"``) that the
prompt tells the model about (``prompts.build_user_prompt``'s
``<excluded_actions>`` block) and that ``compile.py``'s enforcer guarantees
regardless of what the model does with it. Detection is deliberately
conservative (verb-object phrase patterns, not bare verbs) so ordinary
review language -- "extract the notice email address", "the payment terms
clause", "signature block" -- is never flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from northforge.planner.schema import MAX_REQUEST_CHARS, RejectedAction

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")

_SIDE_EFFECT_REASON = "NorthForge never performs side effects; workflows are read-only reviews."
_INJECTION_REASON = (
    "Text embedded in the request attempted to override NorthForge's instructions; it was "
    "recorded and not followed."
)

_SIDE_EFFECT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsend (an? |the )?(email|e-mail|message|notice|notification|report|copy)\b",
        r"\bemail (it|them|this|the|a)\b",
        # "require us to notify", "must notify", "shall notify" describe an
        # obligation under review, not an action to perform.
        r"(?<!to )(?<!must )(?<!shall )(?<!should )\bnotify\b",
        r"\b(pay|wire|refund|reimburse)\b(?! terms| schedule)",
        r"\btransfer (money|funds|the (funds|payment|deposit)|\$)",
        r"\b(sign|countersign|execute|e-sign)\b(?!ature| block|-off| off)",
        r"\b(delete|purge|erase)\b.*\b(document|file|contract|record|data)",
        r"\b(amend|modify|rewrite|edit|update|change)\b.*\b(the |this |our )?"
        r"(contract|agreement|policy|document)\b",
        r"\b(upload|publish)\b.*\b(to|on)\b",
        r"\bpost (it|them|this|the (summary|report|results|findings))\b",
        r"\bauto[- ]?approve\b",
        r"\bskip (the )?(human )?review\b",
        r"\bwithout (human )?review\b",
    )
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget)\s+(?:all |any |the |your )?"
        r"(?:previous|prior|above|earlier|system)\s+(?:instructions|rules|prompt)\b",
        r"\bsystem prompt\b",
        r"\byou are now\b",
        r"\bdeveloper mode\b",
        r"\breveal (your|the) (instructions|prompt|rules)\b",
        r"</?system>",
        r"\bact as (an? )?(unrestricted|jailbroken)",
        r"\bnew instructions?:",
        r"\bfrom now on,? (you|ignore)",
    )
)


@dataclass(frozen=True)
class ScreenResult:
    """Result of screening a raw request before it is shown to the model."""

    text: str
    rejected: list[RejectedAction] = field(default_factory=list)
    injection_suspected: bool = False


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if parts:
        return parts
    stripped = text.strip()
    return [stripped] if stripped else []


def screen_request(text: str) -> ScreenResult:
    """Clean ``text`` and record unsafe or out-of-scope asks it contains.

    Never raises and never blocks: every match becomes a ``RejectedAction``
    and, for injection markers, sets ``injection_suspected``. The model still
    sees the screened text; the enforcer (``compile.py``) is what actually
    guarantees these actions never become steps.
    """
    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = cleaned[:MAX_REQUEST_CHARS]
    cleaned = cleaned.strip()

    rejected: list[RejectedAction] = []
    injection_suspected = False
    seen: set[tuple[int, str]] = set()

    for sentence in _split_sentences(cleaned):
        fragment = sentence[:200]
        for pattern in _SIDE_EFFECT_PATTERNS:
            if pattern.search(sentence):
                key = (id(pattern), sentence)
                if key in seen:
                    continue
                seen.add(key)
                rejected.append(
                    RejectedAction(
                        action=fragment,
                        reason=_SIDE_EFFECT_REASON,
                        category="side_effect",
                        source="screen",
                    )
                )
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(sentence):
                key = (id(pattern), sentence)
                if key in seen:
                    continue
                seen.add(key)
                injection_suspected = True
                rejected.append(
                    RejectedAction(
                        action=fragment,
                        reason=_INJECTION_REASON,
                        category="prompt_injection",
                        source="screen",
                    )
                )

    return ScreenResult(text=cleaned, rejected=rejected, injection_suspected=injection_suspected)


__all__ = ["ScreenResult", "screen_request"]
