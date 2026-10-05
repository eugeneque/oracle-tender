"""Классификатор ОКПД2 (ОК 034-2014): дерево для выбора кодов в фильтрах (30.09–05.10.2026).

Справочник — открытые данные Росстата; берётся готовый разбор `app/seed/okpd2.json.gz`
(репозиторий prog815/okpd2, MIT, версия классификатора на 01.10.2026). Читается один раз и
держится в памяти: ~21 тыс. записей — это несколько мегабайт, а запросы дерева идут на каждое
раскрытие узла.

Разделы-буквы (A–U) в файле отдельными записями не лежат — только как `parent` у двузначных
классов, — поэтому их названия заданы здесь. Буква в фильтр как код не годится: у закупки
хранится числовой код, и «раздел H» для БД значит «классы 49–53». Поэтому у каждого узла есть
`select_codes` — то, что реально записывается в фильтр при выборе: у раздела это список его
классов, у остальных — сам код.
"""

import gzip
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "okpd2.json.gz"

SECTIONS: dict[str, str] = {
    "A": "Продукция и услуги сельского, лесного и рыбного хозяйства",
    "B": "Продукция горнодобывающей промышленности",
    "C": "Продукция обрабатывающей промышленности",
    "D": "Электроэнергия, газ, пар и кондиционирование воздуха",
    "E": "Услуги по водоснабжению; водоотведению, сбору и утилизации отходов, ликвидации загрязнений",
    "F": "Сооружения и строительные работы",
    "G": "Услуги по оптовой и розничной торговле; услуги по ремонту автотранспортных средств и мотоциклов",
    "H": "Услуги транспорта и складского хозяйства",
    "I": "Услуги гостиничного хозяйства и общественного питания",
    "J": "Продукция и услуги в области информации и связи",
    "K": "Услуги финансовые и страховые",
    "L": "Услуги по операциям с недвижимым имуществом",
    "M": "Услуги профессиональные, научные и технические",
    "N": "Услуги административные и вспомогательные",
    "O": "Услуги в области государственного управления и обеспечения военной безопасности; услуги по социальному обеспечению",
    "P": "Услуги в области образования",
    "Q": "Услуги в области здравоохранения и социальные услуги",
    "R": "Услуги в области культуры, спорта, организации досуга и развлечений",
    "S": "Услуги прочие",
    "T": "Услуги домашних хозяйств; недифференцированная продукция домашних хозяйств для собственного потребления",
    "U": "Услуги, предоставляемые экстерриториальными организациями и органами",
}

# Что допустимо хранить в фильтре: 2–9 цифр кода с точками (26, 26.5, 26.51, 26.51.6,
# 26.51.63, 26.51.63.110).
CODE_RE = re.compile(r"^\d{2}(\.\d{1,3}){0,3}$")
SEARCH_LIMIT = 60


@dataclass(frozen=True)
class OkpdNode:
    code: str
    name: str
    parent: str | None
    is_leaf: bool
    has_children: bool
    select_codes: tuple[str, ...]


@dataclass
class _Index:
    by_code: dict[str, OkpdNode]
    children: dict[str | None, list[str]]
    search_rows: list[tuple[str, str]]  # (код, название в нижнем регистре)


@lru_cache(maxsize=1)
def _index() -> _Index:
    with gzip.open(SEED_PATH, "rt", encoding="utf-8") as handle:
        records = json.load(handle)

    children: dict[str | None, list[str]] = {None: list(SECTIONS)}
    for letter in SECTIONS:
        children[letter] = []
    for record in records:
        children.setdefault(record["p"], []).append(record["c"])

    by_code: dict[str, OkpdNode] = {}
    for letter, name in SECTIONS.items():
        by_code[letter] = OkpdNode(
            code=letter,
            name=name,
            parent=None,
            is_leaf=False,
            has_children=True,
            select_codes=tuple(children[letter]),
        )
    search_rows: list[tuple[str, str]] = []
    for record in records:
        code = record["c"]
        by_code[code] = OkpdNode(
            code=code,
            name=record["n"],
            parent=record["p"],
            is_leaf=bool(record["l"]),
            has_children=bool(children.get(code)),
            select_codes=(code,),
        )
        search_rows.append((code, record["n"].lower()))
    return _Index(by_code=by_code, children=children, search_rows=search_rows)


def children_of(parent: str | None) -> list[OkpdNode]:
    index = _index()
    return [index.by_code[code] for code in index.children.get(parent, [])]


def get_node(code: str) -> OkpdNode | None:
    return _index().by_code.get(code)


def lookup(codes: list[str]) -> list[OkpdNode]:
    index = _index()
    return [index.by_code[code] for code in codes if code in index.by_code]


def search(query: str, limit: int = SEARCH_LIMIT) -> list[OkpdNode]:
    """Поиск по началу кода или по словам названия (все слова сразу, порядок не важен)."""

    text = query.strip().lower()
    if not text:
        return []
    index = _index()
    if re.fullmatch(r"[\d.]+", text):
        found = [code for code, _ in index.search_rows if code.startswith(text)]
    else:
        words = text.split()
        found = [
            code for code, name in index.search_rows if all(word in name for word in words)
        ]
    return [index.by_code[code] for code in found[:limit]]


def clean_prefixes(values: list[str] | None) -> list[str]:
    """Префиксы ОКПД2 из query-параметров: пустые отбрасываются, мусор — ValueError.

    Префикс уходит в `LIKE` по колонке с индексом; строка вне формата кода не нашла бы
    ничего и скрыла бы это, поэтому лучше отказать явно.
    """

    result: list[str] = []
    for value in values or []:
        code = value.strip()
        if not code:
            continue
        if not CODE_RE.fullmatch(code):
            raise ValueError(f"Некорректный код ОКПД2: {code}")
        if code not in result:
            result.append(code)
    return result
