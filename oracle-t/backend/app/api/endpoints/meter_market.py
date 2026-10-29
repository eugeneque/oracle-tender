"""«Аналитика → Рынок ПУ»: отчёт по поверкам ФГИС «Аршин» и его справочник (раздел 79
ARCHITECTURE.md). Раздел «Аналитика» — у ролей «Аналитик» и «Администратор» (проверка на
включении роутера, `require_analytics`); они же правят справочник."""

from __future__ import annotations

import io
from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_analytics
from app.db.session import get_db
from app.models.meter_market import MeterMarketModification
from app.models.user import User
from app.schemas.meter_market import (
    GroupIn,
    ManufacturerIn,
    ModificationFromVariantIn,
    ModificationIn,
    ThresholdsIn,
    TypeIn,
    VariantMapIn,
)
from app.services import meter_market_dictionary as dictionary
from app.services import meter_market_report as report
from app.services.audit import log_action
from app.services.meter_market_import import import_file

router = APIRouter(prefix="/meter-market", tags=["meter-market"])

SOURCE_NOTE = "по данным ФГИС «Аршин»"


def _edge(db: Session) -> date:
    edge = report.data_edge(db)
    if edge is None:
        raise HTTPException(
            status_code=409,
            detail="Поверки ФГИС «Аршин» ещё не загружены — отчёт появится после первой загрузки",
        )
    return edge


def _period(db: Session, value: str | None) -> report.Period:
    try:
        return report.parse_period(value, _edge(db))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _filters(manufacturer_id: int | None, design: str | None, phase: str | None, interface: str | None) -> report.Filters:
    return report.Filters(manufacturer_id=manufacturer_id, design=design, phase=phase, interface=interface)


# ── Отчёт ──────────────────────────────────────────────────────────────────────────────────


@router.get("/status")
def get_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    result = report.status(db)
    result["can_edit"] = dictionary.can_edit(user)
    return result


@router.get("/overview")
def get_overview(
    period: str | None = None,
    manufacturer_id: int | None = None,
    design: str | None = None,
    phase: str | None = None,
    interface: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    return report.overview(db, _period(db, period), _filters(manufacturer_id, design, phase, interface))


@router.get("/designs")
def get_designs(phase: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return report.designs_by_year(db, _edge(db), phase)


@router.get("/groups")
def get_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return report.groups_by_year(db, _edge(db))


@router.get("/localization")
def get_localization(
    requirement: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict[str, Any]:
    return report.localization(db, _edge(db), requirement)


@router.get("/drill")
def get_drill(
    period: str | None = None,
    manufacturer_id: int | None = None,
    type_id: int | None = None,
    modification_id: int | None = None,
    unmapped_type_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return report.drill(
            db,
            _period(db, period),
            manufacturer_id=manufacturer_id,
            type_id=type_id,
            modification_id=modification_id,
            unmapped_type_id=unmapped_type_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/variants/{variant_id}/samples")
def get_samples(
    variant_id: int, period: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    return report.variant_samples(db, variant_id, _period(db, period))


def _xlsx_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/export.xlsx")
def export_table(
    table: str = Query(pattern="^(manufacturers|designs|phases|interfaces|designs_by_year|groups|localization)$"),
    period: str | None = None,
    manufacturer_id: int | None = None,
    design: str | None = None,
    phase: str | None = None,
    interface: str | None = None,
    requirement: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    """Любая таблица экрана — в Excel (раздел 8 ТЗ) с теми же фильтрами, что на экране."""

    edge = _edge(db)
    subtitle = f"{SOURCE_NOTE}, данные на {report.status(db)['edge_label']}"
    if table in ("manufacturers", "designs", "phases", "interfaces"):
        p = _period(db, period)
        data = report.overview(db, p, _filters(manufacturer_id, design, phase, interface))
        titles = {
            "manufacturers": "Производитель",
            "designs": "Исполнение",
            "phases": "Тип ПУ",
            "interfaces": "Интерфейс",
        }
        rows = [[r.get("name", r["key"]), r["count"], r["share"]] for r in data[table]]
        rows.append(["Всего", sum(r[1] for r in rows), 100.0 if rows else 0.0])
        content = report.to_xlsx(f"Поверки ПУ ЭЭ, {p.label}", subtitle, [titles[table], "Количество", "Доля, %"], rows)
        return _xlsx_response(f"Рынок ПУ — {titles[table]} — {p.label}.xlsx", content)
    if table in ("designs_by_year", "groups"):
        data = report.designs_by_year(db, edge, phase) if table == "designs_by_year" else report.groups_by_year(db, edge)
        children = "designs" if table == "designs_by_year" else "companies"
        child_name = "design" if table == "designs_by_year" else "name"
        years = data["years"]
        rows = []
        for row in data["rows"]:
            rows.append([row["name"], "", *[row["years"][y] for y in years], row["total"]])
            for child in row[children]:
                rows.append(["", child[child_name], *[child["years"][y] for y in years], child["total"]])
        rows.append(["Всего", "", *[data["totals"][y] for y in years], data["total"]])
        first = "Производитель" if table == "designs_by_year" else "Группа"
        second = "Исполнение" if table == "designs_by_year" else "Компания"
        title = "Исполнения и типы ПУ ЭЭ" if table == "designs_by_year" else "Поверка ПУ ЭЭ по группам компаний"
        content = report.to_xlsx(f"{title}, {data['label']}", subtitle, [first, second, *map(str, years), "Всего"], rows)
        return _xlsx_response(f"{title}.xlsx", content)
    data = report.localization(db, edge, requirement)
    quarters = data["quarters"]
    rows = []
    for row in data["rows"]:
        rows.append([row["name"], "", *[row["quarters"][q] for q in quarters], row["total"]])
        for score in row["scores"]:
            rows.append(["", score["score"], *[score["quarters"][q] for q in quarters], score["total"]])
    rows.append(["Всего", "", *[data["totals"][q] for q in quarters], data["total"]])
    header = ["Производитель", "Баллы", *[f"{q.split('-')[1]} кв. {q.split('-')[0]}" for q in quarters], "Всего"]
    content = report.to_xlsx("Локализованные ПУ ЭЭ по поверкам", subtitle, header, rows)
    return _xlsx_response("Рынок ПУ — локализация.xlsx", content)


# ── Справочник ─────────────────────────────────────────────────────────────────────────────


@router.get("/unmapped")
def get_unmapped(
    type_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict[str, Any]:
    return dictionary.unmapped_queue(db, type_id=type_id)


@router.put("/variants/{variant_id}")
def put_variant(
    variant_id: int,
    payload: VariantMapIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_analytics),
) -> dict[str, Any]:
    variant = dictionary.map_variant(db, variant_id, modification_id=payload.modification_id)
    same = dictionary.map_same_key(db, variant) if payload.apply_same_key else 0
    log_action(
        db,
        component="meter_market",
        action="map_variant",
        result="success",
        details=f"«{variant.raw}» → модификация {payload.modification_id}; тем же ключом — ещё {same}",
        user_id=user.id,
    )
    db.commit()
    return {"id": variant.id, "modification_id": variant.modification_id, "same_key_mapped": same}


@router.get("/types")
def get_types(
    review_status: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return dictionary.types(db, review_status=review_status, search=search)


@router.patch("/types/{type_id}")
def patch_type(
    type_id: int, payload: TypeIn, db: Session = Depends(get_db), user: User = Depends(require_analytics)
) -> dict[str, Any]:
    si_type = dictionary.update_type(db, type_id, payload.model_dump(exclude_unset=True))
    log_action(
        db,
        component="meter_market",
        action=f"update_type:{si_type.mit_number}",
        result="success",
        details=str(payload.model_dump(exclude_unset=True, mode="json")),
        user_id=user.id,
    )
    db.commit()
    return {"id": si_type.id, "in_report": si_type.in_report, "review_status": si_type.review_status}


@router.get("/types/{type_id}/modifications")
def get_modifications(type_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return dictionary.modifications(db, type_id)


@router.post("/types/{type_id}/modifications")
def post_modification(
    type_id: int,
    payload: ModificationFromVariantIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_analytics),
) -> dict[str, Any]:
    """Новая модификация из неразмеченного написания: сразу с привязкой этого написания."""

    values = payload.model_dump(exclude_unset=True, exclude={"variant_id"})
    modification = dictionary.create_modification(db, type_id, values)
    variant = dictionary.map_variant(db, payload.variant_id, modification_id=modification.id)
    same = dictionary.map_same_key(db, variant) + dictionary.remap_auto(db, type_id)
    log_action(
        db,
        component="meter_market",
        action="create_modification",
        result="success",
        details=f"«{modification.name}» из «{variant.raw}»; тем же ключом — ещё {same}",
        user_id=user.id,
    )
    db.commit()
    return {"id": modification.id, "name": modification.name}


@router.patch("/modifications/{modification_id}")
def patch_modification(
    modification_id: int,
    payload: ModificationIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_analytics),
) -> dict[str, Any]:
    modification = db.get(MeterMarketModification, modification_id)
    if modification is None:
        raise HTTPException(status_code=404, detail="Модификация не найдена")
    dictionary.update_modification(db, modification, payload.model_dump(exclude_unset=True))
    log_action(
        db,
        component="meter_market",
        action="update_modification",
        result="success",
        details=f"«{modification.name}»: {payload.model_dump(exclude_unset=True, mode='json')}",
        user_id=user.id,
    )
    db.commit()
    return {"id": modification.id}


@router.get("/modifications/{modification_id}/variants")
def get_variants(modification_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return dictionary.variants_of(db, modification_id)


@router.get("/manufacturers")
def get_manufacturers(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return dictionary.manufacturers(db)


@router.post("/manufacturers")
def post_manufacturer(
    payload: ManufacturerIn, db: Session = Depends(get_db), _: User = Depends(require_analytics)
) -> dict[str, Any]:
    manufacturer = dictionary.save_manufacturer(db, None, payload.name)
    db.commit()
    return {"id": manufacturer.id, "name": manufacturer.name}


@router.put("/manufacturers/{manufacturer_id}")
def put_manufacturer(
    manufacturer_id: int,
    payload: ManufacturerIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_analytics),
) -> dict[str, Any]:
    manufacturer = dictionary.save_manufacturer(db, manufacturer_id, payload.name)
    db.commit()
    return {"id": manufacturer.id, "name": manufacturer.name}


@router.get("/company-groups")
def get_company_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return dictionary.groups(db)


@router.post("/company-groups")
def post_company_group(
    payload: GroupIn, db: Session = Depends(get_db), _: User = Depends(require_analytics)
) -> dict[str, Any]:
    group = dictionary.save_group(db, None, payload.name, [m.model_dump() for m in payload.members])
    db.commit()
    return {"id": group.id}


@router.put("/company-groups/{group_id}")
def put_company_group(
    group_id: int, payload: GroupIn, db: Session = Depends(get_db), _: User = Depends(require_analytics)
) -> dict[str, Any]:
    group = dictionary.save_group(db, group_id, payload.name, [m.model_dump() for m in payload.members])
    db.commit()
    return {"id": group.id}


@router.delete("/company-groups/{group_id}", status_code=204)
def remove_company_group(
    group_id: int, db: Session = Depends(get_db), _: User = Depends(require_analytics)
) -> Response:
    dictionary.delete_group(db, group_id)
    db.commit()
    return Response(status_code=204)


@router.put("/thresholds")
def put_thresholds(
    payload: ThresholdsIn, db: Session = Depends(get_db), user: User = Depends(require_analytics)
) -> dict[str, Any]:
    dictionary.save_thresholds(db, [t.model_dump() for t in payload.thresholds])
    log_action(
        db,
        component="meter_market",
        action="update_thresholds",
        result="success",
        details=", ".join(f"{t.year}: от {t.min_score:g}" for t in payload.thresholds),
        user_id=user.id,
    )
    db.commit()
    return {"thresholds": report.thresholds(db)}


@router.post("/import")
async def post_import(
    file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_analytics)
) -> dict[str, Any]:
    """Импорт справочника аналитика из Excel (лист «Лист1», колонки файла «3 кв 2026.xlsx»)."""

    content = await file.read()
    try:
        stats = import_file(db, io.BytesIO(content))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Не удалось разобрать файл: {exc}") from exc
    changed = dictionary.remap_auto(db)
    log_action(
        db,
        component="meter_market",
        action="import_dictionary",
        result="success",
        details=(
            f"{file.filename}: строк {stats.rows}, новых типов {stats.types}, модификаций {stats.modifications}, "
            f"написаний {stats.variants}; пересведено {changed}"
        ),
        user_id=user.id,
    )
    db.commit()
    return {**stats.__dict__, "remapped": changed}
