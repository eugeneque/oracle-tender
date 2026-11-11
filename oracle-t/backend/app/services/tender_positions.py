"""Позиции закупки: таблица спецификации заказчика — что и сколько закупается (23.10.2026).

Замечание тендерного отдела «Что нужно видеть менеджеру в Битрикс»: «табличная часть —
какие нужны приборы и количество (та таблица, которую публикует заказчик)». Требования
извлекаются с подписью позиции (`Requirement.position`), но количества у них нет, а
перечень позиций, собранный из подписей, неполон: позиция без требований (монтаж, пломбы,
кабель) в него не попадает.

Позиции извлекаются отдельным запросом к модели — не из того же ответа, что требования:
требования идут по фрагментам документации, и таблица, разрезанная на два фрагмента, дала бы
две половины перечня. Здесь модели отдаётся текст вокруг таблиц и слов «спецификация»,
«количество», «ед. изм.» из всех документов, приоритетные и ТЗ первыми, — одним куском.

Позиции пересоздаются при каждом разборе целиком (как требования); сбой модели оставляет
прежние. Количество — числом, если оно указано числом; «по заявкам», «в пределах цены
договора» — текстом.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import pydantic
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.tender_position import TenderPosition
from app.models.user import User
from app.services.ai_client import fit_chars, run_structured
from app.services.audit import log_action

# Сколько текста просить у окна модели: таблица спецификации редко длиннее 30–40 тыс.
# символов, а окно меньше — `fit_chars` урежет.
DESIRED_CHARS = 60_000
MAX_POSITIONS = 300
# Строки вокруг признака таблицы: заголовок таблицы бывает на пару строк выше, а строки
# позиций идут ниже заголовка.
_CONTEXT_BEFORE = 3
_CONTEXT_AFTER = 40

_SPEC_RE = re.compile(
    r"спецификац|ведомост|количеств|кол-?во|ед\.?\s*изм|единиц\w*\s+измерени|наименовани\w+\s+(?:товар|продукци|оборудован|позици)"
    r"|\bшт\b\.?|\bкомпл\b\.?|техническ\w+\s+задани",
    re.IGNORECASE,
)


class PositionAnswer(pydantic.BaseModel):
    position: str
    name: str
    unit: str
    quantity: str
    lot: str
    document: str
    quote: str


class PositionsAnswer(pydantic.BaseModel):
    """Все поля обязательны: Yandex AI Studio не принимает схему с необязательными полями."""

    positions: list[PositionAnswer]


_SYSTEM_PROMPT = """Ты разбираешь закупочную документацию поставщика приборов учёта \
электроэнергии. Извлеки ТАБЛИЦУ ПОЗИЦИЙ закупки — то, что заказчик закупает и в каком \
количестве: спецификацию, ведомость поставки, перечень товаров, таблицу в техническом \
задании или проекте договора.

Правила:
1. Одна позиция — одна строка таблицы заказчика: товар, оборудование, материал или работа \
(«Монтаж прибора учёта») со своим количеством.
2. Характеристики товара («Класс точности 1», «Номинальный ток 5 А») — НЕ позиции.
3. position — номер позиции, как в таблице («1», «2.1»); если номера нет — пустая строка.
4. name — наименование позиции, как в таблице, без характеристик сверх наименования.
5. unit — единица измерения, как в документе («шт.», «компл.», «усл. ед.»); нет — пустая строка.
6. quantity — количество точно как в документе («120», «1 500», «по заявкам Заказчика»); \
нет — пустая строка. Не складывай и не пересчитывай.
7. lot — номер лота, если документация делит закупку на лоты; иначе "1".
8. document — имя файла, из которого взята позиция (строка «=== имя файла ===» над текстом).
9. quote — короткая цитата строки таблицы (до 200 символов).
10. Одна и та же таблица часто повторяется в ТЗ, проекте договора и извещении — бери её один \
раз, из технического задания или спецификации.
11. Если таблицы позиций в документации нет, а предмет закупки назван с количеством \
(«поставка 120 счётчиков»), — одна позиция. Если количества нет нигде — пустой список.
Отвечай по-русски."""


def _ordered_documents(db: Session, tender: Tender) -> list[TenderDocument]:
    documents = list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id, TenderDocument.extracted_text.is_not(None))
            .order_by(TenderDocument.created_at)
        )
    )
    # Тот же порядок, что у разделов «Дополнительно»: звёздочка человека, затем ТЗ и смета —
    # при обрезке по окну модели под нож идут извещение и протоколы, а не спецификация.
    priority = {DocumentClass.TZ_DESCRIPTION.value: 1, DocumentClass.SSR.value: 2}
    documents.sort(
        key=lambda document: (not document.is_priority_source, priority.get(document.document_class or "", 9))
    )
    return documents


def specification_text(db: Session, tender: Tender, limit: int) -> str:
    """Текст для модели: куски документов вокруг признаков таблицы позиций.

    Документ целиком в окно не помещается (проект договора — сотни тысяч символов), а
    таблица позиций — это строки с « | » (так извлекаются таблицы docx, раздел о
    `extract_docx_text`) или с «шт.»/«количество» рядом."""

    parts: list[str] = []
    length = 0
    for document in _ordered_documents(db, tender):
        lines = document.extracted_text.splitlines()
        keep: set[int] = set()
        for index, line in enumerate(lines):
            if " | " in line or _SPEC_RE.search(line):
                keep.update(range(max(0, index - _CONTEXT_BEFORE), min(len(lines), index + _CONTEXT_AFTER)))
        if not keep:
            continue
        chunks: list[str] = []
        previous = None
        for index in sorted(keep):
            if previous is not None and index != previous + 1:
                chunks.append("…")
            chunks.append(lines[index])
            previous = index
        body = "\n".join(chunks)
        header = f"\n=== {document.file_name} ===\n"
        room = limit - length - len(header)
        if room <= 500:
            break
        parts.append(header + body[:room])
        length += len(header) + min(len(body), room)
    return "".join(parts)


_NUMBER_RE = re.compile(r"^\s*(\d[\d\s ]*(?:[.,]\d+)?)\s*(?:[^\d\s.,].*)?$")


def parse_quantity(text: str) -> Decimal | None:
    """«1 500» → 1500, «120,5» → 120.5; «по заявкам» и «до 120» — не число."""

    match = _NUMBER_RE.match(text or "")
    if not match:
        return None
    try:
        return Decimal(re.sub(r"[\s ]", "", match.group(1)).replace(",", "."))
    except InvalidOperation:
        return None


def _clean(value: str, limit: int) -> str | None:
    value = " ".join((value or "").split()).strip(" ;")
    return value[:limit] or None


def extract_positions(db: Session, tender: Tender, *, actor: User | None = None) -> int:
    """Извлекает позиции закупки и заменяет ими прежние. Возвращает число позиций.

    Без текста документации модель не вызывается, прежние позиции не трогаются. Сбой модели
    поднимается — вызывающий решает, отменяет ли он остальной разбор."""

    reserved = len(_SYSTEM_PROMPT) + 500
    text = specification_text(db, tender, fit_chars(db, DESIRED_CHARS, reserved=reserved))
    if not text:
        return 0
    answer = run_structured(
        db,
        system_prompt=_SYSTEM_PROMPT,
        user_text=f"ЗАКУПКА: {tender.title}\n\nДОКУМЕНТАЦИЯ (фрагменты с таблицами):\n{text}",
        response_model=PositionsAnswer,
        temperature=0.0,
    )

    rows: list[TenderPosition] = []
    seen: set[tuple[str, str, str]] = set()
    for item in answer.positions[:MAX_POSITIONS]:
        name = _clean(item.name, 2000)
        if not name:
            continue
        lot = _clean(item.lot, 30) or "1"
        number = _clean(item.position, 30)
        quantity_text = _clean(item.quantity, 100)
        marker = (lot, number or "", name.casefold())
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(
            TenderPosition(
                tender_id=tender.id,
                sort=len(rows) + 1,
                position_no=number,
                lot_number=lot,
                name=name,
                unit=_clean(item.unit, 50),
                quantity=parse_quantity(quantity_text or ""),
                quantity_text=quantity_text,
                document_name=_clean(item.document, 500),
                quote=_clean(item.quote, 600),
            )
        )

    db.execute(delete(TenderPosition).where(TenderPosition.tender_id == tender.id))
    db.add_all(rows)
    log_action(
        db,
        component="tender_positions",
        action=f"extract_positions:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=f"Позиций закупки: {len(rows)}",
        user_id=actor.id if actor else None,
    )
    db.commit()
    return len(rows)


def list_positions(db: Session, tender: Tender) -> list[TenderPosition]:
    return list(
        db.scalars(
            select(TenderPosition)
            .where(TenderPosition.tender_id == tender.id)
            .order_by(TenderPosition.sort)
        )
    )


# --- привязка требований к позициям (11.11.2026) ----------------------------------------------
#
# Замечание специалиста Bitrix24 (11.11.2026): во вкладке «ТЗ» не понять, к какой позиции
# относится требование, — «поз. 1, и по ней весь анализ ТЗ, далее поз. 2». Подпись позиции у
# требования (`Requirement.position`) модель пишет заново в каждом фрагменте документации, и
# одна позиция выходит по-разному: «1. Высоковольтный прибор… 10 кВ», то же без номера, «…
# электроэнергии…» вместо «… электрической энергии…», а то и название закупки. Здесь подпись
# сводится к строке спецификации (`TenderPosition`): по номеру, иначе по наименованию. Не
# угадываем: два одинаково похожих кандидата или расхождение в числах («6 кВ» и «10 кВ») —
# позиция не определена, подпись остаётся как есть.

_LEAD_NO_RE = re.compile(
    r"^\s*(?:поз(?:иция|\.)?\s*)?№?\s*(\d+(?:\.\d+)*)(?:[.)]\s*|\s+(?=\D)|\s*$)", re.IGNORECASE
)
_WORD_RE = re.compile(r"\d+(?:[.,]\d+)?|[a-zа-я]+", re.IGNORECASE)
# Слова, которые в подписях позиций ставятся или опускаются без смысла для различения.
_STOP = {"и", "с", "в", "на", "для", "по", "из", "не", "или", "шт", "ед"}
_STEM = 5
# Доля общих основ слов, начиная с которой наименования считаются одной позицией: «прибор
# учёта электрической энергии» и «прибор учёта электроэнергии» расходятся на одно-два слова.
_NAME_THRESHOLD = 0.6
ALL_POSITIONS = "Все позиции"


def split_number(text: str) -> tuple[str | None, str]:
    match = _LEAD_NO_RE.match(text)
    if not match:
        return None, text.strip()
    return match.group(1), text[match.end():].strip()


def _words(text: str) -> tuple[set[str], set[str]]:
    """Основы слов и числа наименования: числа сравниваются отдельно и строго."""

    stems: set[str] = set()
    numbers: set[str] = set()
    for word in _WORD_RE.findall(text.lower().replace("ё", "е")):
        if word[0].isdigit():
            numbers.add(word.replace(",", "."))
        elif word not in _STOP and len(word) > 1:
            stems.add(word[:_STEM])
    return stems, numbers


def _similarity(label: str, name: str) -> float:
    a_stems, a_numbers = _words(label)
    b_stems, b_numbers = _words(name)
    if a_numbers != b_numbers or not a_stems or not b_stems:
        return 0.0
    return len(a_stems & b_stems) / len(a_stems | b_stems)


def _numbers_conflict(label: str, name: str) -> bool:
    a = _words(label)[1]
    b = _words(name)[1]
    return bool(a and b and a != b)


def match_position(label: str | None, positions: list[TenderPosition]) -> TenderPosition | None:
    """Строка спецификации, к которой относится подпись позиции у требования, или None.

    Номер в начале подписи («1.», «Поз. 2») решает, если такой номер в спецификации есть
    один и наименование ему не противоречит. Иначе — самое похожее наименование, если оно
    похоже достаточно и однозначно."""

    if not label or not label.strip() or not positions:
        return None
    number, name = split_number(label)
    if number:
        numbered = [item for item in positions if (item.position_no or "").strip().rstrip(".") == number]
        if len(numbered) == 1:
            # Номер без наименования или с наименованием той же позиции: «1. … 10 кВ» не
            # отнесём к позиции 1, если в ней «6 кВ» — модель перепутала номер.
            if not name or not _numbers_conflict(name, numbered[0].name):
                return numbered[0]
            return None
    scored = sorted(
        ((_similarity(name or label, item.name), index) for index, item in enumerate(positions)),
        reverse=True,
    )
    best, index = scored[0]
    if best < _NAME_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1][0] == best:
        # Две позиции с одинаковым наименованием (поз. 1 и 7 — оба «… 10 кВ»): без номера
        # не различить.
        return None
    return positions[index]
