"""Метрики бинарной классификации: «закупка релевантна» против решения специалиста."""

from collections import Counter
from collections.abc import Iterable

# Меньше стольких примеров в разрезе — процент ненадёжен, показываем только число.
MIN_RELIABLE = 30


def confusion(pairs: Iterable[tuple[bool, bool]]) -> dict[str, int]:
    """pairs: (прогноз системы, решение человека). Положительный класс — «релевантна»."""

    counts = Counter()
    for predicted, actual in pairs:
        key = ("t" if predicted == actual else "f") + ("p" if predicted else "n")
        counts[key] += 1
    return {k: counts.get(k, 0) for k in ("tp", "fp", "fn", "tn")}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def scores(c: dict[str, int]) -> dict:
    precision = _ratio(c["tp"], c["tp"] + c["fp"])
    recall = _ratio(c["tp"], c["tp"] + c["fn"])
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall
        else None
    )
    n = sum(c.values())
    return {**c, "n": n, "precision": precision, "recall": recall, "f1": f1,
            "reliable": n >= MIN_RELIABLE}


def by_group(rows: list[dict], key: str, predicted: str) -> dict[str, dict]:
    """Метрики в разрезе: rows — dict с полями `actual`, `<predicted>` и полем разреза."""

    groups: dict[str, list[tuple[bool, bool]]] = {}
    for row in rows:
        if row[predicted] is None:
            continue
        groups.setdefault(str(row.get(key)), []).append((bool(row[predicted]), row["actual"]))
    return {name: scores(confusion(pairs)) for name, pairs in sorted(groups.items())}
