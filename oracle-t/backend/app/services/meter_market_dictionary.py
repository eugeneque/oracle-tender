"""Справочник рынка ПУ: разметка вариантов, типы, модификации, производители, группы, пороги
(раздел 79 ARCHITECTURE.md, разделы 6–7 ТЗ). Правят роли «Аналитик» и «Администратор».

Ручное решение человека автоматикой не пересматривается: привязка варианта получает
`mapped_by = manual`, локализация — `localization_source = manual`, и ни повторный импорт, ни
сопоставление с ГИСП их не затирают.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.meter_market import (
    DESIGN_LABELS,
    LocalizationSource,
    MappedBy,
    MeterMarketGroup,
    MeterMarketGroupMember,
    MeterMarketManufacturer,
    MeterMarketModification,
    MeterMarketScoreThreshold,
    MeterMarketType,
    MeterMarketVariant,
    TypeReviewStatus,
)
from app.models.user import User, UserRole
from app.services import meter_market_interface as interface_rules
from app.services import meter_market_rules as rules
from app.services.meter_market_normalize import (
    ModificationMatcher,
    clean_text,
    manufacturer_key,
    modification_key,
    normalize_interface,
)

PHASES = ("1ф", "3ф")


def can_edit(user: User) -> bool:
    return user.role in (UserRole.ADMIN.value, UserRole.ANALYST.value)


def _get(db: Session, model: type, object_id: int, what: str) -> Any:
    found = db.get(model, object_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{what} не найден(а)")
    return found


# ── Очередь «Разметить» ────────────────────────────────────────────────────────────────────


def unmapped_queue(db: Session, *, limit: int = 200, type_id: int | None = None) -> dict[str, Any]:
    """Неразмеченные написания типов «в отчёте»: сколько поверок, примеры поверителей и
    заводских номеров (раздел 6.3 ТЗ). Самые массовые — сверху: они сильнее всего искажают итог."""

    params: dict[str, Any] = {"l": limit}
    where = ""
    if type_id is not None:
        where, params["t"] = "AND v.type_id = :t", type_id
    rows = db.execute(
        text(
            "SELECT v.id, v.raw, v.type_id, t.mit_number, t.notation, mf.name AS manufacturer, "
            "coalesce(sum(mv.count), 0) AS count, max(mv.month) AS last_month "
            "FROM meter_market_variants v JOIN meter_market_types t ON t.id = v.type_id AND t.in_report "
            "LEFT JOIN meter_market_manufacturers mf ON mf.id = t.manufacturer_id "
            "LEFT JOIN meter_verification_monthly mv ON mv.variant_id = v.id "
            f"WHERE v.modification_id IS NULL {where} GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC LIMIT :l"
        ),
        params,
    ).mappings().all()
    items = [dict(r) for r in rows]
    for item in items:
        samples = db.execute(
            text(
                "SELECT o.title, mv.mi_number FROM meter_verifications mv "
                "LEFT JOIN meter_market_orgs o ON o.id = mv.org_id WHERE mv.variant_id = :v "
                "ORDER BY mv.verification_date DESC LIMIT 5"
            ),
            {"v": item["id"]},
        ).all()
        item["orgs"] = sorted({s[0] for s in samples if s[0]})
        item["mi_numbers"] = [s[1] for s in samples if s[1]]
    total = db.execute(
        text(
            "SELECT count(*), coalesce(sum(c), 0) FROM (SELECT v.id, sum(mv.count) AS c FROM meter_market_variants v "
            "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report "
            "LEFT JOIN meter_verification_monthly mv ON mv.variant_id = v.id "
            "WHERE v.modification_id IS NULL GROUP BY 1) x"
        )
    ).one()
    return {"items": items, "variants": total[0], "verifications": int(total[1])}


def map_variant(db: Session, variant_id: int, *, modification_id: int | None) -> MeterMarketVariant:
    """Привязать написание к модификации (`None` — снять привязку).

    Допустима модификация и другого описания типа (20.11.2026): поверитель вносит в ФГИС номер СИ
    соседнего типа (в поверке «МИРТЕК-12-РУ-…» стоит 67661-17 МИРТЕК-232-РУ, а модификация — из 61891-15).
    Написание остаётся у своего типа (загрузка ищет его по (тип, написание)), а отчёт берёт признаки
    из самой модификации, так что тип поверки на расчёт не влияет."""

    variant = _get(db, MeterMarketVariant, variant_id, "Вариант написания")
    if modification_id is not None:
        _get(db, MeterMarketModification, modification_id, "Модификация")
    variant.modification_id = modification_id
    variant.mapped_by = MappedBy.MANUAL.value if modification_id is not None else None
    db.flush()
    return variant


def map_same_key(db: Session, variant: MeterMarketVariant) -> int:
    """Остальные неразмеченные написания того же типа с тем же ключом — туда же: Ксения
    размечает одно написание, а не каждую опечатку поверителя по отдельности."""

    if variant.modification_id is None:
        return 0
    result = db.execute(
        text(
            "UPDATE meter_market_variants SET modification_id = :m, mapped_by = 'auto' "
            "WHERE type_id = :t AND norm_key = :k AND modification_id IS NULL AND id <> :v"
        ),
        {"m": variant.modification_id, "t": variant.type_id, "k": variant.norm_key, "v": variant.id},
    )
    return result.rowcount or 0


# ── Модификации ────────────────────────────────────────────────────────────────────────────

_MOD_FIELDS = ("name", "phase", "design", "interface", "mpi_years", "comment", "manufacturer_override_id")
_LOC_FIELDS = ("in_rpp", "score", "rpp_included_at", "rpp_record_number")


def _validate_mod(values: dict[str, Any]) -> dict[str, Any]:
    if "phase" in values and values["phase"] not in (None, *PHASES):
        raise HTTPException(status_code=422, detail="Тип ПУ — «1ф» или «3ф»")
    if "design" in values and values["design"] not in DESIGN_LABELS:
        raise HTTPException(status_code=422, detail="Исполнение — DIN-рейка, шкафное, сплит или н/д")
    if "interface" in values:
        values["interface"] = normalize_interface(values["interface"])
    if "name" in values:
        values["name"] = clean_text(values["name"])
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Название модификации не может быть пустым")
    return values


def create_modification(db: Session, type_id: int, values: dict[str, Any]) -> MeterMarketModification:
    _get(db, MeterMarketType, type_id, "Описание типа")
    values = _validate_mod(dict(values))
    if db.scalar(
        select(MeterMarketModification).where(
            MeterMarketModification.type_id == type_id, MeterMarketModification.name == values.get("name")
        )
    ):
        raise HTTPException(status_code=409, detail="У этого типа уже есть модификация с таким названием")
    modification = MeterMarketModification(type_id=type_id, design=values.pop("design", None) or "nd")
    update_modification(db, modification, values, add=True)
    return modification


def update_modification(
    db: Session, modification: MeterMarketModification, values: dict[str, Any], *, add: bool = False
) -> MeterMarketModification:
    values = _validate_mod(dict(values))
    for key in _MOD_FIELDS:
        if key in values:
            setattr(modification, key, values[key])
    if any(key in values for key in _LOC_FIELDS):
        for key in _LOC_FIELDS:
            if key in values:
                setattr(modification, key, values[key])
        modification.localization_source = LocalizationSource.MANUAL.value
    if "smart_890" in values:
        # Решение аналитика: переопределяет ИИ и им не перезаписывается; None — вернуть решение ИИ.
        # Форма правки шлёт поле всегда: без решения аналитика None — «не трогать ИИ».
        if values["smart_890"] is not None:
            modification.smart_890 = values["smart_890"]
            modification.smart_890_source = "manual"
        elif modification.smart_890_source == "manual":
            modification.smart_890 = None
            modification.smart_890_source = None
            modification.smart_890_note = None
            modification.smart_890_at = None  # ИИ оценит заново при следующем запуске
    if add:
        db.add(modification)
    db.flush()
    return modification


def set_variant_smart(db: Session, variant_id: int, value: bool | None) -> MeterMarketVariant:
    """Ручное решение «соответствует ПП 890» по написанию (22.11.2026): главнее правила по протоколу Pn
    и решения модификации. `None` — снять; правило по обозначению (если применимо) вернётся сразу."""

    from datetime import datetime, timezone

    variant = _get(db, MeterMarketVariant, variant_id, "Вариант написания")
    if value is None:
        variant.smart_890, variant.smart_890_source, variant.smart_890_note, variant.smart_890_at = None, None, None, None
        db.flush()
        rules.apply_mirtek_protocol(db.connection())
        db.refresh(variant)
    else:
        variant.smart_890, variant.smart_890_source = value, "manual"
        variant.smart_890_note = "решение аналитика по этому написанию"
        variant.smart_890_at = datetime.now(timezone.utc)
        db.flush()
    return variant


def set_variant_interface(db: Session, variant_id: int, value: str | None) -> MeterMarketVariant:
    """Решение аналитика по интерфейсу написания (23.11.2026, раздел 122): подтвердить предложение или
    исправить. Главнее таблицы кодов и модификации; `None` — снять, правило по таблице вернётся сразу."""

    from datetime import datetime, timezone

    variant = _get(db, MeterMarketVariant, variant_id, "Вариант написания")
    if value is None:
        variant.interface = variant.interface_source = variant.interface_note = None
        variant.interface_review = variant.interface_suggested = variant.interface_at = None
        db.flush()
        interface_rules.apply_interface_codes(db.connection(), [variant.type_id])
        db.refresh(variant)
        return variant
    label = interface_rules.join_label(interface_rules.split_label(value))  # пусто или «н/д» → «н/д»
    variant.interface, variant.interface_source = label[:255], "manual"
    variant.interface_note = "решение аналитика по этому написанию"
    variant.interface_review = None
    variant.interface_at = datetime.now(timezone.utc)
    db.flush()
    return variant


def interface_review_queue(db: Session, *, type_id: int | None = None, limit: int = 300) -> dict[str, Any]:
    """Написания с интерфейсом на согласовании (раздел 122): код не расшифрован, канал зависит от номера
    модуля, сочетание кодов под вопросом или расшифровка по описанию типа расходится с модификацией.
    Самые массовые — сверху."""

    params: dict[str, Any] = {"l": limit}
    where = ""
    if type_id is not None:
        where, params["t"] = "AND v.type_id = :t", type_id
    base = (
        "FROM meter_market_variants v JOIN meter_market_types t ON t.id = v.type_id "
        "LEFT JOIN meter_market_manufacturers mf ON mf.id = t.manufacturer_id "
        "LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
        f"WHERE v.interface_review IS NOT NULL AND v.excluded_reason IS NULL {where} "
    )
    rows = db.execute(
        text(
            "SELECT v.id, v.raw, v.type_id, t.mit_number, t.notation, mf.name AS manufacturer, t.in_report, "
            "m.id AS modification_id, m.name AS modification, m.interface AS modification_interface, "
            "v.interface_suggested AS suggested, v.interface_review AS review, v.interface_note AS note, "
            "(SELECT coalesce(sum(mv.count), 0) FROM meter_verification_monthly mv WHERE mv.variant_id = v.id) AS count "
            f"{base} ORDER BY count DESC, v.raw LIMIT :l"
        ),
        params,
    ).mappings().all()
    total = db.execute(text(f"SELECT count(*) {base}"), params).scalar()
    return {"items": [dict(r) for r in rows], "total": total}


def interface_codes_draft(db: Session, type_id: int) -> str:
    """Черновик таблицы кодов из «Описания типа» ГРСИ (блоки «Основной / Дополнительные интерфейсы»)."""

    si_type = _get(db, MeterMarketType, type_id, "Описание типа")
    description = db.execute(
        text("SELECT description_type_text FROM si_types WHERE si_code = :c AND description_type_text IS NOT NULL "
             "ORDER BY length(description_type_text) DESC LIMIT 1"),
        {"c": si_type.mit_number},
    ).scalar()
    if not description:
        raise HTTPException(status_code=404, detail="Текста описания типа в каталоге нет — таблицу кодов заполните вручную")
    draft = interface_rules.codes_from_description(description)
    if not draft:
        raise HTTPException(
            status_code=422,
            detail="В описании типа не нашлось блоков «Основной / Дополнительные интерфейсы» с кодами — заполните вручную",
        )
    return draft


def set_type_smart(db: Session, type_id: int, value: bool | None) -> int:
    """Ручная отметка «соответствует ПП 890» всем модификациям типа сразу (06.11.2026): у крупных
    типов десятки модификаций, а решение по линейке одно. `None` — снять ручные отметки (ИИ
    оценит заново). Возвращает, сколько модификаций затронуто."""

    rows = db.execute(select(MeterMarketModification).where(MeterMarketModification.type_id == type_id)).scalars().all()
    for mod in rows:
        if value is None:
            if mod.smart_890_source == "manual":
                mod.smart_890, mod.smart_890_source, mod.smart_890_note, mod.smart_890_at = None, None, None, None
        else:
            mod.smart_890, mod.smart_890_source = value, "manual"
    db.flush()
    return len(rows)


def related_modifications(db: Session, type_id: int) -> list[dict[str, Any]]:
    """Модификации других типов того же производителя — для привязки написания, записанного
    поверителем под чужим номером СИ (20.11.2026). В `type_notation` — описание типа-владельца."""

    rows = db.execute(
        text(
            "SELECT m.*, t.notation AS type_notation, t.mit_number AS type_mit_number "
            "FROM meter_market_modifications m JOIN meter_market_types t ON t.id = m.type_id "
            "WHERE m.type_id <> :t AND t.manufacturer_id IS NOT DISTINCT FROM "
            "(SELECT manufacturer_id FROM meter_market_types WHERE id = :t) "
            "ORDER BY t.notation, m.name"
        ),
        {"t": type_id},
    ).mappings()
    return [dict(r) for r in rows]


def modifications(db: Session, type_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT m.*, (SELECT count(*) FROM meter_market_variants v WHERE v.modification_id = m.id) AS variants, "
            "(SELECT coalesce(sum(mv.count), 0) FROM meter_market_variants v "
            " JOIN meter_verification_monthly mv ON mv.variant_id = v.id WHERE v.modification_id = m.id) AS count "
            "FROM meter_market_modifications m WHERE m.type_id = :t ORDER BY count DESC, m.name"
        ),
        {"t": type_id},
    ).mappings()
    return [dict(r) for r in rows]


def variants_of(db: Session, modification_id: int) -> list[dict[str, Any]]:
    """Как записывали модификацию и сколько поверок у каждого написания (раздел 6.3 ТЗ)."""

    rows = db.execute(
        text(
            "SELECT v.id, v.raw, v.mapped_by, coalesce(sum(mv.count), 0) AS count FROM meter_market_variants v "
            "LEFT JOIN meter_verification_monthly mv ON mv.variant_id = v.id WHERE v.modification_id = :m "
            "GROUP BY 1, 2, 3 ORDER BY 4 DESC"
        ),
        {"m": modification_id},
    ).mappings()
    return [dict(r) for r in rows]


# ── Типы ───────────────────────────────────────────────────────────────────────────────────


def types(db: Session, *, review_status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where = ["true"]
    if review_status:
        where.append("t.review_status = :s")
        params["s"] = review_status
    if search:
        where.append("(t.mit_number ILIKE :q OR t.notation ILIKE :q OR t.title ILIKE :q OR mf.name ILIKE :q)")
        params["q"] = f"%{search}%"
    rows = db.execute(
        text(
            "SELECT t.id, t.mit_number, t.title, t.notation, t.manufacturer_id, mf.name AS manufacturer, "
            "t.cert_valid_to, t.mpi_years, t.in_report, t.review_status, t.code_notes, t.interface_codes, t.created_at, "
            "(SELECT count(*) FROM meter_market_variants v WHERE v.type_id = t.id AND v.interface_review IS NOT NULL) AS interface_review, "
            "(SELECT count(*) FROM meter_market_modifications m WHERE m.type_id = t.id) AS modifications, "
            "(SELECT count(*) FROM meter_market_variants v WHERE v.type_id = t.id AND v.modification_id IS NULL) AS unmapped, "
            "(SELECT coalesce(sum(mv.count), 0) FROM meter_market_variants v JOIN meter_verification_monthly mv "
            " ON mv.variant_id = v.id WHERE v.type_id = t.id) AS count "
            "FROM meter_market_types t LEFT JOIN meter_market_manufacturers mf ON mf.id = t.manufacturer_id "
            f"WHERE {' AND '.join(where)} ORDER BY count DESC"
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]


def update_type(db: Session, type_id: int, values: dict[str, Any]) -> MeterMarketType:
    si_type = _get(db, MeterMarketType, type_id, "Описание типа")
    if "review_status" in values:
        status = values["review_status"]
        if status not in {s.value for s in TypeReviewStatus}:
            raise HTTPException(status_code=422, detail="Неизвестное решение по типу")
        si_type.review_status = status
        # Решение «включить/исключить» и есть флаг отчёта — два поля разойтись не должны.
        si_type.in_report = status == TypeReviewStatus.INCLUDED.value
    for key in ("manufacturer_id", "cert_valid_to", "mpi_years", "notation"):
        if key in values:
            setattr(si_type, key, values[key])
    if "code_notes" in values:
        si_type.code_notes = (values["code_notes"] or "").strip() or None
    if "interface_codes" in values:
        si_type.interface_codes = (values["interface_codes"] or "").strip() or None
        db.flush()
        interface_rules.apply_interface_codes(db.connection(), [si_type.id])
    db.flush()
    return si_type


# ── Производители, группы, пороги ──────────────────────────────────────────────────────────


def manufacturers(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT mf.id, mf.name, mf.synonyms, (SELECT count(*) FROM meter_market_types t WHERE t.manufacturer_id = mf.id) "
            "AS types FROM meter_market_manufacturers mf ORDER BY mf.name"
        )
    ).mappings()
    return [dict(r) for r in rows]


def save_manufacturer(db: Session, manufacturer_id: int | None, name: str) -> MeterMarketManufacturer:
    name = clean_text(name) or ""
    if not name:
        raise HTTPException(status_code=422, detail="Название производителя не может быть пустым")
    clash = db.scalar(select(MeterMarketManufacturer).where(MeterMarketManufacturer.name == name))
    if clash is not None and clash.id != manufacturer_id:
        raise HTTPException(status_code=409, detail="Производитель с таким названием уже есть")
    if manufacturer_id is None:
        manufacturer = MeterMarketManufacturer(name=name, synonyms=[manufacturer_key(name)])
        db.add(manufacturer)
    else:
        manufacturer = _get(db, MeterMarketManufacturer, manufacturer_id, "Производитель")
        manufacturer.name = name
        key = manufacturer_key(name)
        if key not in manufacturer.synonyms:
            manufacturer.synonyms = [*manufacturer.synonyms, key]
    db.flush()
    return manufacturer


def groups(db: Session) -> list[dict[str, Any]]:
    names = dict(db.execute(text("SELECT id, name FROM meter_market_manufacturers")).all())
    result = []
    for group in db.scalars(select(MeterMarketGroup).order_by(MeterMarketGroup.name)):
        members = db.scalars(select(MeterMarketGroupMember).where(MeterMarketGroupMember.group_id == group.id)).all()
        result.append(
            {
                "id": group.id,
                "name": group.name,
                "members": [
                    {
                        "manufacturer_id": m.manufacturer_id,
                        "name": names.get(m.manufacturer_id),
                        "year_from": m.year_from,
                        "year_to": m.year_to,
                    }
                    for m in members
                ],
            }
        )
    return result


def save_group(db: Session, group_id: int | None, name: str, members: list[dict[str, Any]]) -> MeterMarketGroup:
    name = clean_text(name) or ""
    if not name:
        raise HTTPException(status_code=422, detail="Название группы не может быть пустым")
    for member in members:
        if member.get("year_from") and member.get("year_to") and member["year_from"] > member["year_to"]:
            raise HTTPException(status_code=422, detail="Год начала участия позже года окончания")
    group = MeterMarketGroup(name=name) if group_id is None else _get(db, MeterMarketGroup, group_id, "Группа")
    group.name = name
    db.add(group)
    db.flush()
    db.execute(text("DELETE FROM meter_market_group_members WHERE group_id = :g"), {"g": group.id})
    for member in members:
        db.add(
            MeterMarketGroupMember(
                group_id=group.id,
                manufacturer_id=member["manufacturer_id"],
                year_from=member.get("year_from"),
                year_to=member.get("year_to"),
            )
        )
    db.flush()
    return group


def delete_group(db: Session, group_id: int) -> None:
    db.delete(_get(db, MeterMarketGroup, group_id, "Группа"))
    db.flush()


def save_thresholds(db: Session, values: list[dict[str, Any]]) -> None:
    """Пороги баллов целиком: год → минимум. Пустой список не принимается — без порогов
    экран локализации не смог бы сказать, каким требованиям соответствует исполнение."""

    if not values:
        raise HTTPException(status_code=422, detail="Нужен хотя бы один порог баллов")
    years = [int(v["year"]) for v in values]
    if len(set(years)) != len(years):
        raise HTTPException(status_code=422, detail="Год порога указан дважды")
    db.execute(text("DELETE FROM meter_market_score_thresholds"))
    for value in values:
        db.add(MeterMarketScoreThreshold(year=int(value["year"]), min_score=float(value["min_score"])))
    db.flush()


def remap_auto(db: Session, type_id: int | None = None, *, recheck: bool = False) -> int:
    """Пересвести неразмеченные написания: по ключам уже размеченных и по шаблонам названий
    модификаций (`ModificationMatcher`) — после импорта или новой модификации в справочнике.

    `recheck` — заодно перепроверить автоматические привязки (после правки правил шаблонов,
    12.11.2026: «НЕВА СТ221 … BCS-G3E» было у «B» из-за буквы в «BCS»). Меняется привязка, только
    если шаблон дал другое исполнение; ключ, который человек разметил вручную, не трогается."""

    rows = db.execute(
        text(
            "SELECT DISTINCT ON (type_id, norm_key) type_id, norm_key, modification_id, mapped_by FROM meter_market_variants "
            "WHERE modification_id IS NOT NULL ORDER BY type_id, norm_key, mapped_by = 'manual' DESC"
        )
    ).all()
    known = {(t, k): m for t, k, m, _ in rows}
    manual_keys = {(t, k) for t, k, _, by in rows if by == "manual"}
    modifications = [tuple(r) for r in db.execute(text("SELECT id, type_id, name FROM meter_market_modifications"))]
    by_name: dict[tuple[int, str], int] = {}
    for mod_id, mod_type, name in modifications:
        by_name.setdefault((mod_type, modification_key(name)), mod_id)
        known.setdefault((mod_type, modification_key(name)), mod_id)
    matcher = ModificationMatcher(modifications, dict(db.execute(text("SELECT id, notation FROM meter_market_types")).all()))
    # Запасной путь (20.11.2026): поверитель указал номер СИ соседнего типа («МИРТЕК-12-РУ-D1-…» под
    # 67661-17 МИРТЕК-232-РУ). Если в своём типе совпадения нет — ищем модификацию с тем же названием
    # (точно, с точностью до регистра/пробелов) у других типов того же производителя; берём только
    # однозначное совпадение.
    type_mf = dict(db.execute(text("SELECT id, manufacturer_id FROM meter_market_types")).all())
    by_mf_name: dict[tuple[Any, str], set[int]] = {}
    for mod_id, mod_type, name in modifications:
        by_mf_name.setdefault((type_mf.get(mod_type), modification_key(name)), set()).add(mod_id)

    def sibling(variant_type: int, key: str) -> int | None:
        found = by_mf_name.get((type_mf.get(variant_type), key), set())
        return next(iter(found)) if len(found) == 1 and type_mf.get(variant_type) is not None else None

    params: dict[str, Any] = {}
    where = "(modification_id IS NULL OR mapped_by = 'auto')" if recheck else "modification_id IS NULL"
    if type_id is not None:
        where += " AND type_id = :t"
        params["t"] = type_id
    updates = []
    ruled_updates = []
    numbers = dict(db.execute(text("SELECT id, mit_number FROM meter_market_types")).all())
    for variant_id, variant_type, key, raw, current in db.execute(
        text(f"SELECT id, type_id, norm_key, raw, modification_id FROM meter_market_variants WHERE {where}"), params
    ).all():
        # Правила аналитика (i-prom, 19.11.2026) главнее шаблонов; привязка — как ручная.
        ruled = next(
            (by_name[(variant_type, modification_key(n))] for n in rules.rule_candidates(numbers.get(variant_type), raw)
             if (variant_type, modification_key(n)) in by_name),
            None,
        )
        if ruled is not None:
            if ruled != current:
                ruled_updates.append({"m": ruled, "v": variant_id})
            continue
        if current is None:
            mod_id = known.get((variant_type, key)) or matcher.match(variant_type, key, raw) or sibling(variant_type, key)
        elif (variant_type, key) in manual_keys:
            continue
        else:
            mod_id = by_name.get((variant_type, key)) or matcher.match(variant_type, key, raw) or sibling(variant_type, key)
            if mod_id == current:
                continue
        if mod_id is not None:
            updates.append({"m": mod_id, "v": variant_id})
    if updates:
        db.execute(
            text("UPDATE meter_market_variants SET modification_id = :m, mapped_by = 'auto' WHERE id = :v"), updates
        )
    if ruled_updates:
        db.execute(
            text("UPDATE meter_market_variants SET modification_id = :m, mapped_by = 'manual' WHERE id = :v"), ruled_updates
        )
    db.flush()
    return len(updates) + len(ruled_updates)
