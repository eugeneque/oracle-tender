"""Сравнение редакций документов и краткое содержание разъяснений — кодом (17.11.2026).

Перечень Bitrix24 «Что еще нужно от ИИ», пп. 14, 52–54, 60, 35, 90 (раздел 113 ARCHITECTURE.md).
Раньше новая редакция ТЗ или проекта договора приходила в журнал событий одним названием файла
(«изменённые требования не сверялись»), а разъяснение — названием, без содержания.

Почему без модели: различия двух текстов — задача `difflib`, она не выдумывает изменений, и её
результат проверяем дословно (прежний и новый фрагмент). Модель понадобилась бы для пересказа,
но портал просит именно «прежнюю и новую формулировку» — их и передаём, с ограничением длины.

Ограничения: сравниваются распознанные тексты (`tender_documents.extracted_text`); у скана с
плохим распознаванием различий будет много мелких — тогда событие помечается «требуется проверка»
(слишком много изменённых фрагментов). Документ, заменённый по тому же адресу, сюда не попадает:
мониторинг его не скачивает повторно.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

MAX_FRAGMENTS = 15
MAX_FRAGMENT_CHARS = 700
# Больше — вероятно, другой документ или иное распознавание, а не правка пунктов.
NOISY_FRAGMENTS = 60

_SPACE_RE = re.compile(r"[ \t ]+")


def _paragraphs(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = _SPACE_RE.sub(" ", line).strip()
        if len(line) >= 3:
            out.append(line)
    return out


def _clip(text: str, limit: int = MAX_FRAGMENT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def diff_texts(old: str | None, new: str | None) -> dict[str, Any]:
    """Изменённые фрагменты двух редакций: `changes` — [{key, field, old_value, new_value}]
    (формат `data.changes` журнала Bitrix24), `total` — сколько фрагментов изменилось,
    `noisy` — изменений так много, что это скорее другой документ или другое распознавание."""

    a, b = _paragraphs(old or ""), _paragraphs(new or "")
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    changes: list[dict[str, Any]] = []
    total = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        total += 1
        if len(changes) >= MAX_FRAGMENTS:
            continue
        before = "\n".join(a[i1:i2]) or None
        after = "\n".join(b[j1:j2]) or None
        # Номер пункта — из начала строки («5.2.», «п. 7»), чтобы сотрудник нашёл место.
        anchor = re.match(r"^((?:п\.\s*)?\d+(?:\.\d+)*\.?)\s", (after or before or ""))
        changes.append(
            {
                "key": f"fragment-{j1 + 1}",
                "field": f"Пункт {anchor.group(1)}" if anchor else f"Фрагмент у строки {j1 + 1}",
                "old_value": _clip(before) if before else None,
                "new_value": _clip(after) if after else None,
            }
        )
    return {"changes": changes, "total": total, "noisy": total > NOISY_FRAGMENTS}


def summary_values(diff: dict[str, Any], limit: int = 1500) -> tuple[str | None, str | None]:
    """Прежнее и новое значение события: изменённые фрагменты подряд (портал: «краткие прежние
    и новые требования»)."""

    def join(side: str) -> str | None:
        parts = [c[side] for c in diff["changes"] if c.get(side)]
        text = "\n— ".join(parts)
        return _clip("— " + text, limit) if parts else None

    return join("old_value"), join("new_value")


# --- разъяснения ------------------------------------------------------------------------------

_ANSWER_RE = re.compile(r"(?:^|\n)\s*(?:ответ|разъяснени[ея](?:\s+положений)?)\s*[:.\-—]?\s*", re.IGNORECASE)

IMPACT_AREAS: dict[str, tuple[str, re.Pattern[str]]] = {
    "PARTICIPATION": ("Участие и состав заявки", re.compile(r"участник|заявк|квалификац|лиценз|опыт|допуск|сро\b", re.I)),
    "TECHNICAL": ("ТЗ и характеристики", re.compile(r"техническ\w+\s+задани|характеристик|эквивалент|модел|товарн\w+\s+знак|ТЗ\b", re.I)),
    "CONTRACT": ("Договор", re.compile(r"договор|контракт|неустойк|штраф|гаранти", re.I)),
    "PRICE": ("Цена", re.compile(r"\bцен[аыеу]|НМЦ|стоимост|НДС", re.I)),
    "TERMS": ("Сроки", re.compile(r"\bсрок|календарн\w+\s+дн|рабоч\w+\s+дн|график", re.I)),
}


def impact_areas(text: str | None) -> list[str]:
    """На что влияет текст (п. 35): участие, ТЗ, договор, цена, сроки — по словам самого текста.
    Пусто — по словам не определить (это не «ни на что не влияет»)."""

    return [label for label, pattern in IMPACT_AREAS.values() if pattern.search(text or "")]


def clarification_excerpt(text: str | None, limit: int = 1500) -> str | None:
    """Содержание разъяснения: текст после «Ответ:»/«Разъяснение:», иначе начало документа."""

    text = (text or "").strip()
    if not text:
        return None
    match = _ANSWER_RE.search(text)
    body = text[match.end():] if match and len(text) - match.end() > 50 else text
    body = _SPACE_RE.sub(" ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return _clip(body, limit)
