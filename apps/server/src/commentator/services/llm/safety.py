"""Checks applied to every generated comment before it is shown or spoken.

Policy (ADR 0005):
- empty or whitespace-only -> replaced by the template comment
- longer than the maximum  -> cut at the last sentence end within the maximum;
                              if no sentence fits, replaced by the template comment
- blocked words            -> replaced by the template comment
- Markdown, quotes and line breaks are removed, because the text is read aloud
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Small, explicit list for a family setting. Matching is on the normalized text.
BLOCKED_WORDS: tuple[str, ...] = (
    "死ね", "殺す", "ころす", "バカ", "ばか", "馬鹿", "アホ", "あほ", "クソ", "くそ",
    "ブス", "キモい", "きもい", "うざい", "カス", "ざまあ",
    "kill", "stupid", "idiot", "loser", "shut up", "damn", "hell", "suck",
)

_SENTENCE_END_JA = "。！？!?"
_MARKUP = re.compile(r"[*_`#>\[\]]|^\s*[-・]\s*", re.MULTILINE)
_QUOTES = re.compile(r"^[\s「『\"“']+|[\s」』\"”']+$")


@dataclass(frozen=True, slots=True)
class SafetyResult:
    text: str | None
    problem: str | None  # None when the text is usable
    adjusted: bool = False


def measure(text: str, language: str) -> int:
    return len(text.split()) if language == "en" else len(text)


def clean(text: str) -> str:
    text = _MARKUP.sub("", text)
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    return _QUOTES.sub("", text).strip()


def _contains_blocked(text: str) -> str | None:
    lowered = text.lower()
    for word in BLOCKED_WORDS:
        if word.isascii():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return word
        elif word in text:
            return word
    return None


def _cut(text: str, language: str, maximum: int) -> str | None:
    if language == "en":
        words = text.split()[:maximum]
        candidate = " ".join(words)
        ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", candidate)]
        return candidate[: ends[-1]] if ends else None
    candidate = text[:maximum]
    ends = [index + 1 for index, char in enumerate(candidate) if char in _SENTENCE_END_JA]
    return candidate[: ends[-1]] if ends else None


def review(text: str, *, language: str, maximum: int) -> SafetyResult:
    cleaned = clean(text)
    if not cleaned:
        return SafetyResult(None, "empty")
    blocked = _contains_blocked(cleaned)
    if blocked is not None:
        return SafetyResult(None, "blocked_word")
    if measure(cleaned, language) <= maximum:
        return SafetyResult(cleaned, None, adjusted=cleaned != text)
    cut = _cut(cleaned, language, maximum)
    if not cut:
        return SafetyResult(None, "too_long")
    return SafetyResult(cut, None, adjusted=True)
