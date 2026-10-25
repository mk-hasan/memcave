"""Pure-Python paired bootstrap for rule-score differences."""

import random


def mean_difference_interval(
    left: list[float],
    right: list[float],
    *,
    seed: int,
    samples: int = 1000,
) -> tuple[float, float]:
    """Return the 2.5 and 97.5 percentiles of the paired mean difference."""
    if len(left) != len(right) or not left:
        raise ValueError("bootstrap inputs must be non-empty and the same length")
    rng = random.Random(seed)
    size = len(left)
    diffs: list[float] = []
    for _ in range(samples):
        total = 0.0
        for _index in range(size):
            pick = rng.randrange(size)
            total += left[pick] - right[pick]
        diffs.append(total / size)
    diffs.sort()
    return _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def states_difference(low: float, high: float) -> bool:
    return high < 0 or low > 0


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight
