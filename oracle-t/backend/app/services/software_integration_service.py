"""Интеграции приборов МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).

Зачем. Публичные списки поддерживаемого оборудования (`upper_software_service`) говорят лишь
«прибор есть в списке». Старший тендерный специалист ведёт точнее: протоколы (МИРТЕК/СПОДЭС,
версия, объём), способ идентификации приборов в ПО, версия ПО, функции из официальных писем
разработчиков. Он ручается за эти данные и раз-два в месяц их обновляет, поэтому они —
«истина в последней инстанции» для продукции МИРТЕК и перекрывают публичные списки.

Черновик и публикация. Правки пишутся в `SoftwareIntegration.draft`; в анализ тендеров и в
каталог попадает только `published` — то, что специалист подтвердил общей кнопкой
«Сохранить» (`publish`). Так полузаполненная форма не меняет вердикты в середине правки, а
каждое нажатие остаётся в журнале публикаций. «Дообучения» модели здесь нет: опубликованное
подмешивается в карточку производителя и в промпт матрицы соответствия (`mirtek_facts`) —
поэтому обновление вступает в силу сразу, без переобучения.

Что считается проверенным. Поля с пометкой `auto` (заполнены системой по публичному списку
или по скану) в публикацию не идут, пока специалист их не подтвердит, исправит или не
заполнит сам (`confirmed` / `edited` / `manual`). Функции — по флагу `verified`.

Форма документа (`draft` и `published`):

    {"fields": {<ключ>: {"value": str, "verdict": "yes|partial|no|unknown|None",
                          "status": "auto|confirmed|edited|manual", "source": str,
                          "updated_at": iso, "updated_by": str|None}},
     "models": [str],
     "functions": [{"id", "model", "protocol", "name", "status": "yes|partial|no",
                    "comment", "verified": bool, "source", "document_id"}]}
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.upper_software import PLATFORMS
from app.core.timezones import now_utc, to_msk
from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product
from app.models.software_integration import (
    SoftwareIntegration,
    SoftwareIntegrationDocument,
    SoftwareIntegrationPublication,
)
from app.models.user import User
from app.seed.software_integrations_data import SOFTWARE_INTEGRATIONS

# Поля формы: ключ → (подпись, нужен ли словесный вердикт). Порядок — порядок в интерфейсе.
FIELDS: dict[str, tuple[str, bool]] = {
    "has_mirtek": ("Есть ли ПУ МИРТЕК?", True),
    "protocol_mirtek": ("Протокол МИРТЕК?", True),
    "protocol_spodes": ("Протокол СПОДЭС? (версия, объём интеграции)", True),
    "identification": ("Как идентифицируются ПУ в ПО?", False),
    "integration_protocols": ("Протоколы интеграции", False),
    "software_version": ("Версия ПО, в которую интегрированы", False),
    "note": ("Комментарий", False),
}
VERDICTS = ("yes", "partial", "no", "unknown")
FIELD_STATUSES = ("auto", "confirmed", "edited", "manual")
VALIDATED_STATUSES = ("confirmed", "edited", "manual")
FUNCTION_STATUSES = ("yes", "partial", "no")
PROTOCOLS = ("СПОДЭС", "МИРТЕК")

MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp")

VERDICT_WORDS = {
    "yes": "да",
    "partial": "частично",
    "no": "нет",
    "unknown": "не выяснено",
}

# Сколько знаков одного поля попадает в карточку для модели: версии Энфорса — абзац, но
# вердикт по ним не зависит от десятого пункта списка.
_MAX_VALUE_CHARS = 400
_MAX_VERSION_CHARS = 700
_MAX_FUNCTIONS_IN_FACT = 30


# ------------------------------------------------------------------------------ документ


def _empty_field() -> dict:
    return {"value": "", "verdict": None, "status": "auto", "source": "manual",
            "updated_at": None, "updated_by": None}


def empty_draft() -> dict:
    return {"fields": {key: _empty_field() for key in FIELDS}, "models": [], "functions": []}


def _clean_text(value: Any, limit: int = 4000) -> str:
    return re.sub(r"[ \t]+\n", "\n", str(value or "")).strip()[:limit]


def _clean_models(models: Any) -> list[str]:
    seen: list[str] = []
    for item in models or []:
        name = _clean_text(item, 120)
        if name and name.lower() not in {m.lower() for m in seen}:
            seen.append(name)
    return seen


def _clean_function(item: dict, stored: dict | None) -> dict | None:
    name = _clean_text(item.get("name"), 500)
    if not name:
        return None
    status = item.get("status") if item.get("status") in FUNCTION_STATUSES else "yes"
    protocol = _clean_text(item.get("protocol"), 40)
    return {
        "id": str(item.get("id") or uuid.uuid4()),
        "model": _clean_text(item.get("model"), 120),
        "protocol": protocol,
        "name": name,
        "status": status,
        "comment": _clean_text(item.get("comment"), 1000),
        "verified": bool(item.get("verified")),
        "source": (stored or {}).get("source") or item.get("source") or "manual",
        "document_id": (stored or {}).get("document_id") or item.get("document_id"),
    }


def apply_draft_update(stored: dict, incoming: dict, *, actor_name: str | None) -> dict:
    """Новый черновик из присланной формы.

    Статус поля выводит сервер, а не клиент: изменилось значение или вердикт → `edited`
    (или `manual`, если поле было пустым), не изменилось, но пользователь нажал
    «Подтвердить» → `confirmed`. Иначе у поля остаётся прежний статус, и «автозаполнено,
    не проверено» нельзя перевести в проверенное, просто пересохранив форму."""

    now = now_utc().isoformat()
    result = empty_draft()
    old_fields = (stored or {}).get("fields") or {}
    for key, (_label, with_verdict) in FIELDS.items():
        old = {**_empty_field(), **(old_fields.get(key) or {})}
        new_in = (incoming.get("fields") or {}).get(key)
        if new_in is None:
            result["fields"][key] = old
            continue
        value = _clean_text(new_in.get("value"))
        # Вердикт не прислан — остаётся прежним (частичное обновление через API не должно его стирать).
        raw_verdict = new_in["verdict"] if "verdict" in new_in else old.get("verdict")
        verdict = raw_verdict if with_verdict and raw_verdict in VERDICTS else None
        field = {**old, "value": value, "verdict": verdict}
        changed = value != (old.get("value") or "") or (with_verdict and verdict != old.get("verdict"))
        if changed:
            field["status"] = "edited" if (old.get("value") or old.get("verdict")) else "manual"
            field["source"] = "manual" if field["status"] == "manual" else old.get("source") or "manual"
            field["updated_at"], field["updated_by"] = now, actor_name
        elif new_in.get("status") == "confirmed" and (value or verdict):
            if old.get("status") != "confirmed":
                field["status"] = "confirmed"
                field["updated_at"], field["updated_by"] = now, actor_name
        result["fields"][key] = field
    result["models"] = _clean_models(incoming.get("models"))
    old_functions = {f.get("id"): f for f in (stored or {}).get("functions") or []}
    functions = []
    for item in incoming.get("functions") or []:
        cleaned = _clean_function(item, old_functions.get(item.get("id")))
        if cleaned is not None:
            functions.append(cleaned)
    result["functions"] = functions
    return result


def published_view(draft: dict) -> dict:
    """Что из черновика уходит в публикацию: проверенные поля и функции."""

    fields = {}
    for key in FIELDS:
        field = {**_empty_field(), **((draft.get("fields") or {}).get(key) or {})}
        if field["status"] in VALIDATED_STATUSES and (field["value"] or field["verdict"]):
            fields[key] = field
    return {
        "fields": fields,
        "models": list(draft.get("models") or []),
        "functions": [f for f in draft.get("functions") or [] if f.get("verified")],
    }


def _core(view: dict | None) -> dict:
    """Сравнимая часть публикации: без времени и автора правки (подтверждение без
    изменения значения — не изменение данных)."""

    view = view or {"fields": {}, "models": [], "functions": []}
    return {
        "fields": {
            k: [v.get("value"), v.get("verdict")] for k, v in (view.get("fields") or {}).items()
        },
        "models": view.get("models") or [],
        "functions": [
            [f.get("id"), f.get("model"), f.get("protocol"), f.get("name"), f.get("status"), f.get("comment")]
            for f in view.get("functions") or []
        ],
    }


def is_dirty(item: SoftwareIntegration) -> bool:
    return _core(published_view(item.draft or {})) != _core(item.published)


def pending_review_count(item: SoftwareIntegration) -> int:
    """Сколько полей и функций система заполнила сама и специалист ещё не проверил."""

    draft = item.draft or {}
    fields = sum(
        1
        for f in (draft.get("fields") or {}).values()
        if f.get("status") == "auto" and (f.get("value") or f.get("verdict"))
    )
    functions = sum(1 for f in draft.get("functions") or [] if not f.get("verified"))
    return fields + functions


def _diff(old: dict | None, new: dict) -> dict | None:
    old_core, new_core = _core(old), _core(new)
    if old_core == new_core:
        return None
    changed = [
        FIELDS[k][0]
        for k in FIELDS
        if old_core["fields"].get(k) != new_core["fields"].get(k)
    ]
    old_ids = {f[0] for f in old_core["functions"]}
    new_ids = {f[0] for f in new_core["functions"]}
    changed_functions = [f for f in new_core["functions"] if f[0] in old_ids and f not in old_core["functions"]]
    return {
        "fields": changed,
        "models_changed": old_core["models"] != new_core["models"],
        "functions_added": len(new_ids - old_ids),
        "functions_removed": len(old_ids - new_ids),
        "functions_changed": len(changed_functions),
    }


# ---------------------------------------------------------------------------- операции


def list_integrations(db: Session) -> list[SoftwareIntegration]:
    return list(
        db.scalars(select(SoftwareIntegration).order_by(SoftwareIntegration.sort_order, SoftwareIntegration.name))
    )


def get_integration(db: Session, integration_id: uuid.UUID) -> SoftwareIntegration | None:
    return db.get(SoftwareIntegration, integration_id)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9а-яё]+", "_", name.lower()).strip("_")[:60] or "po"
    return base


def create_integration(
    db: Session, *, name: str, aliases: list[str], platform_key: str | None, actor: User
) -> SoftwareIntegration:
    name = _clean_text(name, 255)
    if not name:
        raise ValueError("Укажите название ПО")
    if platform_key and platform_key not in PLATFORMS:
        raise ValueError(f"Площадка «{platform_key}» неизвестна")
    key = _slug(name)
    if db.scalar(select(SoftwareIntegration).where(SoftwareIntegration.key == key)):
        key = f"{key}_{uuid.uuid4().hex[:6]}"
    order = (db.scalar(select(func.max(SoftwareIntegration.sort_order))) or 0) + 1
    item = SoftwareIntegration(
        key=key, name=name, platform_key=platform_key, aliases=_clean_aliases(aliases) or [re.escape(name.lower())],
        sort_order=order, draft=empty_draft(), draft_updated_at=now_utc(),
    )
    db.add(item)
    db.flush()
    return item


def _clean_aliases(aliases: Any) -> list[str]:
    result: list[str] = []
    for alias in aliases or []:
        text = _clean_text(alias, 200)
        if not text:
            continue
        try:
            re.compile(text)
        except re.error as exc:
            raise ValueError(f"Шаблон «{text}» не является корректным регулярным выражением: {exc}") from exc
        result.append(text)
    return result


def update_integration(db: Session, item: SoftwareIntegration, payload: dict, *, actor: User) -> SoftwareIntegration:
    if payload.get("name") is not None:
        name = _clean_text(payload["name"], 255)
        if not name:
            raise ValueError("Название ПО не может быть пустым")
        item.name = name
    if payload.get("aliases") is not None:
        item.aliases = _clean_aliases(payload["aliases"])
    if "platform_key" in payload:
        key = payload["platform_key"] or None
        if key and key not in PLATFORMS:
            raise ValueError(f"Площадка «{key}» неизвестна")
        item.platform_key = key
    if payload.get("draft") is not None:
        item.draft = apply_draft_update(item.draft or {}, payload["draft"], actor_name=actor.full_name)
        item.draft_updated_at = now_utc()
    db.flush()
    return item


def reset_draft(db: Session, item: SoftwareIntegration) -> SoftwareIntegration:
    """Отменить несохранённые правки: черновик возвращается к опубликованному.

    Автозаполненные, но не опубликованные поля при этом исчезают — их система предложит
    заново при следующем автозаполнении."""

    published = copy.deepcopy(item.published) if item.published else None
    draft = empty_draft()
    if published:
        draft["fields"].update(published.get("fields") or {})
        draft["models"] = published.get("models") or []
        draft["functions"] = published.get("functions") or []
    item.draft = draft
    item.draft_updated_at = now_utc()
    db.flush()
    return item


def delete_integration(db: Session, item: SoftwareIntegration) -> None:
    db.delete(item)
    db.flush()


def publish(db: Session, *, actor: User, comment: str | None = None) -> SoftwareIntegrationPublication | None:
    """Общая кнопка «Сохранить»: проверенное из черновиков становится опубликованным.

    Возвращает запись журнала, а если менять нечего — `None`."""

    changes: list[dict] = []
    now = now_utc()
    for item in list_integrations(db):
        view = published_view(item.draft or {})
        diff = _diff(item.published, view)
        if diff is None:
            continue
        item.published = copy.deepcopy(view)
        item.published_at = now
        item.published_by_id = actor.id
        changes.append({"key": item.key, "name": item.name, **diff})
    if not changes:
        return None
    record = SoftwareIntegrationPublication(
        published_at=now, published_by_id=actor.id, comment=_clean_text(comment, 1000) or None, changes=changes
    )
    db.add(record)
    db.flush()
    return record


def list_publications(db: Session, limit: int = 20) -> list[tuple[SoftwareIntegrationPublication, str | None]]:
    rows = db.execute(
        select(SoftwareIntegrationPublication, User.full_name)
        .outerjoin(User, User.id == SoftwareIntegrationPublication.published_by_id)
        .order_by(SoftwareIntegrationPublication.published_at.desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def seed_defaults(db: Session) -> int:
    """Первичная загрузка из таблицы интеграций V6. Разовая: если записи уже есть — ничего
    не делает, чтобы перезапуск не затирал правки специалиста."""

    if db.scalar(select(func.count()).select_from(SoftwareIntegration)):
        return 0
    now = now_utc().isoformat()
    for order, entry in enumerate(SOFTWARE_INTEGRATIONS, start=1):
        draft = empty_draft()
        for key, raw in entry["fields"].items():
            if not (raw["value"] or raw["verdict"]):
                continue
            draft["fields"][key] = {
                "value": raw["value"], "verdict": raw["verdict"], "status": "confirmed",
                "source": "table_v6", "updated_at": now, "updated_by": "Таблица интеграций V6",
            }
        draft["models"] = list(entry["models"])
        item = SoftwareIntegration(
            key=entry["key"], name=entry["name"], platform_key=entry["platform_key"],
            aliases=list(entry["aliases"]), sort_order=order, draft=draft, draft_updated_at=now_utc(),
            published=published_view(draft), published_at=now_utc(),
        )
        db.add(item)
    db.add(
        SoftwareIntegrationPublication(
            comment="Первичная загрузка из «Таблицы интеграции наших ПУ (итог) V6»",
            changes=[{"key": e["key"], "name": e["name"], "fields": [], "initial": True} for e in SOFTWARE_INTEGRATIONS],
        )
    )
    db.flush()
    logger.info(f"Интеграции в ПО верхнего уровня: загружено {len(SOFTWARE_INTEGRATIONS)} записей")
    return len(SOFTWARE_INTEGRATIONS)


# ---------------------------------------------------------------------------- документы


def add_document(
    db: Session, item: SoftwareIntegration, *, file_name: str, content_type: str | None, content: bytes, actor: User
) -> SoftwareIntegrationDocument:
    name = file_name or "scan"
    lower = name.lower()
    if lower.endswith((".emf", ".wmf")):
        raise ValueError(
            "Форматы EMF/WMF не читаются: сохраните страницу как PNG или PDF и загрузите её заново"
        )
    if not lower.endswith(ALLOWED_DOCUMENT_EXTENSIONS):
        raise ValueError("Допустимы PDF и изображения PNG, JPG, WEBP")
    if not content:
        raise ValueError("Файл пустой")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Файл больше предела {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ")
    document = SoftwareIntegrationDocument(
        integration_id=item.id, file_name=name[:255], content_type=content_type, size=len(content),
        content=content, uploaded_by_id=actor.id,
    )
    db.add(document)
    db.flush()
    return document


def list_documents(db: Session, integration_id: uuid.UUID) -> list[SoftwareIntegrationDocument]:
    return list(
        db.scalars(
            select(SoftwareIntegrationDocument)
            .where(SoftwareIntegrationDocument.integration_id == integration_id)
            .order_by(SoftwareIntegrationDocument.created_at.desc())
        )
    )


# ------------------------------------------------------------------ сопоставление и факты


def _alias_res(item: SoftwareIntegration) -> list[re.Pattern[str]]:
    result = []
    for alias in item.aliases or []:
        try:
            result.append(re.compile(alias, re.IGNORECASE))
        except re.error:
            logger.warning(f"Интеграция {item.key}: битый шаблон «{alias}»")
    return result


def detect_integrations(
    items: list[SoftwareIntegration], text: str | None, public_platforms: list[str] | None = None
) -> list[SoftwareIntegration]:
    """Какие ПО названы в тексте: по своим шаблонам либо по площадке публичного списка,
    уже узнанной в тексте (названа «Пирамида» — подходят обе записи Пирамиды)."""

    if not text:
        return []
    public = set(public_platforms or [])
    return [
        item
        for item in items
        if any(p.search(text) for p in _alias_res(item)) or (item.platform_key and item.platform_key in public)
    ]


def _value(view: dict, key: str, limit: int = _MAX_VALUE_CHARS) -> str:
    field = (view.get("fields") or {}).get(key)
    if not field:
        return ""
    value = (field.get("value") or "").replace("\n", " ").strip()
    verdict = field.get("verdict")
    if verdict and verdict in VERDICT_WORDS and not value:
        return VERDICT_WORDS[verdict]
    if verdict and verdict in VERDICT_WORDS and value.lower().rstrip(".") not in ("да", "нет"):
        # «Да» с пояснением («СПОДЭС V4») — само пояснение; остальные вердикты называем.
        return value[:limit] if verdict == "yes" else f"{VERDICT_WORDS[verdict]} — {value[:limit]}"
    return value[:limit] or (VERDICT_WORDS.get(verdict or "", ""))


def _functions_text(view: dict) -> str:
    groups: dict[tuple[str, str], dict[str, list[str]]] = {}
    for f in view.get("functions") or []:
        group = groups.setdefault((f.get("model") or "", f.get("protocol") or ""), {"yes": [], "partial": [], "no": []})
        group[f.get("status") or "yes"].append(f["name"] + (f" ({f['comment']})" if f.get("comment") else ""))
    parts: list[str] = []
    budget = _MAX_FUNCTIONS_IN_FACT
    for (model, protocol), group in groups.items():
        label = " ".join(x for x in (model, f"[{protocol}]" if protocol else "") if x) or "все модели"
        pieces = []
        for status, caption in (("yes", "поддержано"), ("partial", "частично"), ("no", "НЕ поддержано")):
            names = group[status][:budget]
            budget -= len(names)
            if names:
                pieces.append(f"{caption}: {', '.join(names)}")
        if pieces:
            parts.append(f"{label} — {'; '.join(pieces)}")
    return " | ".join(parts)


def integration_fact_line(item: SoftwareIntegration) -> str | None:
    view = item.published
    if not view:
        return None
    checked = to_msk(item.published_at)
    stamp = f"проверено специалистом {checked:%d.%m.%Y}" if checked else "проверено специалистом"
    pieces = []
    for key, caption, limit in (
        ("has_mirtek", "приборы МИРТЕК в ПО", _MAX_VALUE_CHARS),
        ("protocol_mirtek", "протокол МИРТЕК", _MAX_VALUE_CHARS),
        ("protocol_spodes", "протокол СПОДЭС", _MAX_VALUE_CHARS),
        ("identification", "идентификация приборов", _MAX_VALUE_CHARS),
        ("integration_protocols", "протоколы интеграции", _MAX_VALUE_CHARS),
        ("software_version", "версия ПО", _MAX_VERSION_CHARS),
        ("note", "примечание", _MAX_VALUE_CHARS),
    ):
        value = _value(view, key, limit)
        if value:
            pieces.append(f"{caption} — {value}")
    functions = _functions_text(view)
    if functions:
        pieces.append(f"интегрированные функции: {functions}")
    if not pieces:
        return None
    return f"[software] ДАННЫЕ СПЕЦИАЛИСТА МИРТЕК, {item.name} ({stamp}): " + "; ".join(pieces)


def _has_verdict(item: SoftwareIntegration) -> str | None:
    field = ((item.published or {}).get("fields") or {}).get("has_mirtek") or {}
    return field.get("verdict")


def mirtek_facts(db: Session, manufacturer: Manufacturer, requirements: list[Requirement]) -> list[str]:
    """Строки `[software]` из данных специалиста — только для производителя МИРТЕК и только
    когда закупка спрашивает об интеграции. Названо ПО — факты по нему; требование об
    интеграции без названия — по всем записям с оговоркой, какие ПО не интегрированы."""

    if not manufacturer.is_mirtek or not requirements:
        return []
    from app.services.upper_software_service import detect_platforms, mentions_integration

    items = [i for i in list_integrations(db) if i.published]
    if not items:
        return []
    named: dict[uuid.UUID, SoftwareIntegration] = {}
    generic = False
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        hits = detect_integrations(items, text, detect_platforms(text))
        for hit in hits:
            named[hit.id] = hit
        if not hits and mentions_integration(text):
            generic = True
    facts: list[str] = []
    if named:
        facts.extend(line for item in items if item.id in named and (line := integration_fact_line(item)))
    if generic:
        negative: list[str] = []
        for item in items:
            if item.id in named:
                continue
            if _has_verdict(item) == "no":
                negative.append(item.name)
            elif line := integration_fact_line(item):
                facts.append(line)
        if negative:
            checked = max((i.published_at for i in items if i.published_at), default=None)
            stamp = f"проверено специалистом {to_msk(checked):%d.%m.%Y}" if checked else "проверено специалистом"
            facts.append(
                f"[software] ДАННЫЕ СПЕЦИАЛИСТА МИРТЕК ({stamp}): приборы МИРТЕК НЕ интегрированы в ПО: "
                + ", ".join(negative)
            )
    return facts


def product_integrations(db: Session, product: Product) -> list[dict]:
    """В каких ПО по данным специалиста интегрирована модель — для карточки модели.

    Модель узнаётся по списку `models` записи тем же префиксным сравнением с границей, что
    и у публичных списков («МИРТЕК-32-РУ» покрывает «МИРТЕК-32-РУ-W3»)."""

    from app.services.upper_software_service import _covers, _device_keys, _product_keys

    manufacturer = db.get(Manufacturer, product.manufacturer_id)
    if manufacturer is None or not manufacturer.is_mirtek:
        return []
    product_keys = _product_keys(product)
    result: list[dict] = []
    for item in list_integrations(db):
        if not item.published:
            continue
        matched = [
            model
            for model in item.published.get("models") or []
            if any(_covers(dk, pk) for dk in _device_keys(model) for pk in product_keys)
        ]
        if not matched:
            continue
        fields = item.published.get("fields") or {}
        functions = [
            f for f in item.published.get("functions") or []
            if not f.get("model") or any(_covers(dk, pk) for dk in _device_keys(f["model"]) for pk in product_keys)
        ]
        result.append(
            {
                "integration_id": item.id,
                "key": item.key,
                "name": item.name,
                "matched_models": matched,
                "has_mirtek": _value(item.published, "has_mirtek"),
                "has_mirtek_verdict": (fields.get("has_mirtek") or {}).get("verdict"),
                "protocol_mirtek": _value(item.published, "protocol_mirtek"),
                "protocol_spodes": _value(item.published, "protocol_spodes"),
                "software_version": _value(item.published, "software_version", _MAX_VERSION_CHARS),
                "functions_total": len(functions),
                "functions_supported": sum(1 for f in functions if f.get("status") == "yes"),
                "published_at": item.published_at,
            }
        )
    return result


def tender_integrations(db: Session, requirements: list[Requirement]) -> list[dict]:
    """Для карточки закупки: какие записи специалиста относятся к названному в требованиях
    ПО (кратко — вердикт и версия)."""

    from app.services.upper_software_service import detect_platforms

    items = [i for i in list_integrations(db) if i.published]
    seen: dict[uuid.UUID, dict] = {}
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        for item in detect_integrations(items, text, detect_platforms(text)):
            fields = item.published.get("fields") or {}
            seen[item.id] = {
                "integration_id": item.id,
                "name": item.name,
                "has_mirtek": _value(item.published, "has_mirtek"),
                "has_mirtek_verdict": (fields.get("has_mirtek") or {}).get("verdict"),
                "protocol_spodes": _value(item.published, "protocol_spodes"),
                "software_version": _value(item.published, "software_version", 300),
                "published_at": item.published_at,
            }
    return list(seen.values())


def to_out(item: SoftwareIntegration) -> dict:
    return {
        "id": item.id,
        "key": item.key,
        "name": item.name,
        "platform_key": item.platform_key,
        "platform_name": PLATFORMS[item.platform_key].name if item.platform_key in PLATFORMS else None,
        "aliases": item.aliases or [],
        "sort_order": item.sort_order,
        "draft": item.draft or empty_draft(),
        "published_at": item.published_at,
        "draft_updated_at": item.draft_updated_at,
        "dirty": is_dirty(item),
        "pending_review": pending_review_count(item),
        "has_published": item.published is not None,
    }


# ----------------------------------------------------------------- автозаполнение по спискам


_STATUS_TO_VERDICT = {"supported": "yes", "protocol_only": "partial", "not_listed": "no"}


def autofill_from_public_lists(db: Session) -> dict:
    """Предложения по публичным спискам поддерживаемого оборудования (раздел 35).

    Заполняются только пустые поля записей, у которых есть `platform_key`: «Есть ли ПУ
    МИРТЕК?» (вердикт по статусу списка), «Как идентифицируются ПУ» (обозначения из списка)
    и перечень моделей. Всё помечается `auto` и в анализ не идёт, пока специалист не
    подтвердит или не исправит поле (п.7 решения заказчика 05.11.2026)."""

    from app.services.upper_software_service import manufacturer_support

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)).order_by(Manufacturer.created_at))
    if mirtek is None:
        return {"filled": 0, "items": 0, "message": "Производитель МИРТЕК не найден в справочнике"}
    support = {row.key: row for row in manufacturer_support(db, mirtek)}
    now = now_utc().isoformat()
    filled = items_touched = 0
    for item in list_integrations(db):
        row = support.get(item.platform_key or "")
        if row is None or row.status == "not_synced":
            continue
        draft = copy.deepcopy(item.draft or empty_draft())
        for key in FIELDS:
            draft["fields"].setdefault(key, _empty_field())
        touched = False

        def put(key: str, value: str, verdict: str | None) -> None:
            nonlocal touched, filled
            current = draft["fields"][key]
            if current.get("value") or current.get("verdict"):
                return
            draft["fields"][key] = {
                "value": value, "verdict": verdict, "status": "auto", "source": "public_list",
                "updated_at": now, "updated_by": f"публичный список «{row.name}»",
            }
            touched = True
            filled += 1

        verdict = _STATUS_TO_VERDICT.get(row.status)
        if verdict:
            put("has_mirtek", {"yes": "Да, по публичному списку", "partial": "Только по протоколу СПОДЭС",
                               "no": "Нет в публичном списке"}[verdict], verdict)
        names = [d["device_raw"] for d in row.devices[:12] if not d.get("is_generic")]
        if row.status == "supported" and names:
            put("identification", "По наименованию в списке: " + "; ".join(names), None)
            known = {m.lower() for m in draft["models"]}
            for device in row.devices:
                for name in (device.get("device_names") or []):
                    if name.lower().startswith("миртек") and name.lower() not in known:
                        draft["models"].append(name)
                        known.add(name.lower())
                        touched = True
        if touched:
            item.draft = draft
            item.draft_updated_at = now_utc()
            items_touched += 1
    db.flush()
    return {
        "filled": filled, "items": items_touched,
        "message": f"Предложено полей: {filled} (записей: {items_touched}). Проверьте и подтвердите их.",
    }
