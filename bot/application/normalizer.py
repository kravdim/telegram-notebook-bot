"""Conservative input normalization before deterministic/LLM recognition."""

import re
from dataclasses import dataclass

_OPAQUE_MARKER_RE = re.compile(
    r"\b(?:DP-\d{8}T\d{6}-[a-f0-9]{6}-[\w-]+|[А-ЯЁA-Z]\d{1,4}-[\w-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NormalizedInput:
    raw_text: str
    text: str
    opaque_marker: str | None


class IntentNormalizer:
    """Fix only known harmless variants; this is deliberately not an NLU."""

    def normalize(self, text: str) -> NormalizedInput:
        normalized = re.sub(r"^\s*напмни\b", "напомни", text, flags=re.IGNORECASE)
        normalized = re.sub(
            r"\bчерез\s+пол\s*часа\b",
            "через 30 минут",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"^\s*забей\s+в\s+задач[иу]\s*",
            "создай задачу: ",
            normalized,
            flags=re.IGNORECASE,
        ).strip()
        marker = _OPAQUE_MARKER_RE.search(text)
        return NormalizedInput(text, normalized, marker.group(0) if marker else None)


intent_normalizer = IntentNormalizer()
