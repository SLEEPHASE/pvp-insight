"""完美平台段位换算与配色。"""

from __future__ import annotations

from dataclasses import dataclass

# 2025-2026 赛季段位分档（与平台客户端展示一致）
# D < 1000, D+ < 1200, C < 1400, C+ < 1600, B < 1800,
# B+ < 2000, A < 2200, A+ <= 2400, S > 2400
_TIERS = [
    (1000, "D"),
    (1200, "D+"),
    (1400, "C"),
    (1600, "C+"),
    (1800, "B"),
    (2000, "B+"),
    (2200, "A"),
    (2401, "A+"),
]

_COLORS = {
    "未定级": "#6b7280",
    "D": "#7d8797",
    "D+": "#94a3b8",
    "C": "#4ade80",
    "C+": "#22c55e",
    "B": "#38bdf8",
    "B+": "#818cf8",
    "A": "#c084fc",
    "A+": "#fb7185",
    "S": "#f5b544",
}

_GRADIENTS = {
    "D": ("#4b5563", "#6b7280"),
    "D+": ("#64748b", "#94a3b8"),
    "C": ("#16a34a", "#4ade80"),
    "C+": ("#15803d", "#22c55e"),
    "B": ("#0284c7", "#38bdf8"),
    "B+": ("#4f46e5", "#818cf8"),
    "A": ("#9333ea", "#c084fc"),
    "A+": ("#e11d48", "#fb7185"),
    "S": ("#d97706", "#fbbf24"),
    "未定级": ("#374151", "#6b7280"),
}


@dataclass(frozen=True)
class Rank:
    label: str
    score: int
    stars: int = 0

    @property
    def color(self) -> str:
        return _COLORS.get(self.label, "#8a95a6")

    @property
    def colors(self) -> tuple[str, str]:
        return _GRADIENTS.get(self.label, ("#374151", "#8a95a6"))

    @property
    def text(self) -> str:
        if self.label == "未定级":
            return "未定级"
        return f"{self.label} ★{self.stars}" if self.stars else self.label

    @property
    def short(self) -> str:
        return self.label


def rank_for(score: int | float | None, stars: int = 0) -> Rank:
    try:
        value = int(round(float(score or 0)))
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return Rank("未定级", 0, 0)
    label = "S"
    for limit, name in _TIERS:
        if value < limit:
            label = name
            break
    try:
        stars = max(0, int(stars or 0))
    except (TypeError, ValueError):
        stars = 0
    return Rank(label, value, stars)


def rank_text(score: int | float | None, stars: int = 0) -> str:
    return rank_for(score, stars).text


def score_text(score: int | float | None) -> str:
    try:
        value = int(round(float(score or 0)))
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    # S 段平台统一封顶显示 2401，不展示具体分数会造成误解，但仍保留原值
    return str(value)


def rating_color(rating: float | None) -> str:
    if rating is None:
        return "#6f7a8a"
    if rating >= 1.30:
        return "#ff6b57"
    if rating >= 1.18:
        return "#ff8a2b"
    if rating >= 1.08:
        return "#f5c451"
    if rating >= 1.00:
        return "#6ee7b7"
    if rating >= 0.90:
        return "#93a4ba"
    return "#7c8a9a"


def form_color(win_rate: float | None) -> str:
    if win_rate is None:
        return "#8a95a6"
    if win_rate >= 55:
        return "#4ade80"
    if win_rate >= 50:
        return "#f5c451"
    return "#fb7185"
