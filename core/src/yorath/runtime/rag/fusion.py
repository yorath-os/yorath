"""Reciprocal Rank Fusion (k = 60 per the spike)."""

from collections.abc import Hashable, Sequence


def rrf[T: Hashable](rankings: Sequence[Sequence[T]], k: int = 60) -> list[T]:
    score: dict[T, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            score[item] = score.get(item, 0.0) + 1.0 / (k + rank + 1)
    return [item for item, _ in sorted(score.items(), key=lambda x: -x[1])]
