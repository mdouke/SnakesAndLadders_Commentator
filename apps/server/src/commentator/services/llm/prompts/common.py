"""Settings-driven parts shared by every prompt version."""

from __future__ import annotations

# Target and hard maximum length per setting: characters for Japanese, words for
# English. Texts over the maximum are cut at a sentence end (see safety.py).
LENGTH_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    "ja": {"short": (40, 70), "medium": (80, 130), "long": (140, 210)},
    "en": {"short": (20, 35), "medium": (40, 65), "long": (70, 105)},
}

STYLE_JA = {
    "lively": "テンポのよいスポーツ実況のように、明るく盛り上げる。",
    "calm": "落ち着いた解説者のように、穏やかで聞き取りやすく話す。",
    "kids": "小さな子どもにも分かるやさしい言葉で、楽しく話す。",
}
STYLE_EN = {
    "lively": "Energetic, like a sports play-by-play announcer.",
    "calm": "Calm and clear, like a relaxed color commentator.",
    "kids": "Simple, friendly words that young children understand.",
}
