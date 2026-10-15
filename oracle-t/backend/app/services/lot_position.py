"""Позиции спецификации в закупке из нескольких изделий (15.10.2026, раздел 61
ARCHITECTURE.md).

Отдельный модуль, потому что позицию понимают двое: извлечение требований
(`tender_analysis` — сводит подписи одной позиции из разных фрагментов) и матрица
соответствия (`compliance_service` — сверяет требования позиции только с моделями её типа).
Друг друга они не импортируют.
"""

from __future__ import annotations

import re

# Номер в начале подписи позиции: «1. ВШУ…», «Позиция 2 — …», «поз. №3», «Лот 1». Не
# «1.2 ВШУ» и не «5(10) А»: за номером не должно идти продолжение числа.
_POSITION_NUMBER_RE = re.compile(
    r"^\s*(?:(?:позици[яи]|поз|лот|пункт|п)\.?\s*)?№?\s*(\d+)(?![\d.,]*\d)", re.IGNORECASE
)


def position_key(position: str | None) -> str:
    """Ключ позиции для сравнения: номер, если он есть, иначе подпись без регистра и лишних
    пробелов. Требования извлекаются по фрагментам, и одну позицию разные фрагменты пишут
    по-разному — «1. ВШУ с однофазным ПУ» и «Позиция 1»; это одна позиция. Пустая строка —
    требование без позиции."""

    value = " ".join((position or "").split())
    if not value:
        return ""
    match = _POSITION_NUMBER_RE.match(value)
    if match:
        return match.group(1).lstrip("0") or "0"
    return value.casefold().rstrip(".")


def lot_groups(positions: list[str | None]) -> list[tuple[str | None, list[int]]]:
    """Группы требований для матрицы: (подпись позиции, номера требований с единицы).

    Требования без позиции — последней группой с подписью `None`. Позиций меньше двух —
    одна группа из всех требований: делить нечего, а лишняя группа — лишние запросы к
    модели с той же карточкой."""

    groups: dict[str, tuple[str, list[int]]] = {}
    general: list[int] = []
    for index, position in enumerate(positions, start=1):
        key = position_key(position)
        if key:
            groups.setdefault(key, (" ".join((position or "").split()), []))[1].append(index)
        else:
            general.append(index)
    if len(groups) < 2:
        return [(None, list(range(1, len(positions) + 1)))]
    result: list[tuple[str | None, list[int]]] = list(groups.values())
    if general:
        result.append((None, general))
    return result
