"""Профили компаний (раздел 5.6 «Моя компания», раздел 7 ТЗ).

Без заполненного профиля измерения Task и Competencies (раздел 5.5.1) считать не из чего,
поэтому раздел заведён отдельным эндпоинтом, а не полем в общих настройках: его заполняют
один раз и целиком, и он должен быть виден как самостоятельный шаг настройки системы.

Компаний с 07.09.2026 может быть несколько, и роутера здесь два. `/company-profile`
(единственное число) — про основную компанию: этим путём пользуются места, где «наша
компания» должна быть одна, и его форма ответа не изменилась. `/company-profiles` — список
карточек с обычным CRUD и переключением основной.

Правит только администратор: профиль — вход главной метрики, и незаметная правка списка
допусков меняет оценки по всем тендерам разом.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.tender import CompanyProfileOut, CompanyProfileUpdate
from app.adapters import rusprofile
from app.adapters.rusprofile import RusprofileError
from app.services import company_profile_service, egrul_service
from app.services.company_profile_service import CompanyProfileError
from app.services.egrul_service import EgrulError

router = APIRouter(prefix="/company-profile", tags=["company-profile"])
profiles_router = APIRouter(prefix="/company-profiles", tags=["company-profile"])


def _changes(payload: CompanyProfileUpdate) -> dict:
    """Форма → словарь полей. Присланы только заполненные разделы (`exclude_unset`), поэтому
    частичное сохранение не затирает соседние; вложенные списки разворачиваются в обычные
    словари, какими они и лежат в JSONB."""

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("licenses") is not None:
        changes["licenses"] = [item.model_dump() for item in payload.licenses]
    if changes.get("past_projects") is not None:
        changes["past_projects"] = [item.model_dump() for item in payload.past_projects]
    return changes


def _out(profile) -> CompanyProfileOut:
    return CompanyProfileOut(
        id=profile.id,
        manufacturer_id=profile.manufacturer_id,
        is_primary=profile.is_primary,
        legal_name=profile.legal_name,
        inn=profile.inn,
        kpp=profile.kpp,
        ogrn=profile.ogrn,
        registration_date=profile.registration_date,
        legal_address=profile.legal_address,
        field_sources=profile.field_sources or {},
        years_of_experience=profile.years_of_experience,
        licenses=profile.licenses or [],
        past_projects=profile.past_projects or [],
        bank_requisites=profile.bank_requisites,
        letterhead_file_path=profile.letterhead_file_path,
        updated_at=profile.updated_at,
        is_filled=profile.is_filled(),
        has_inn=profile.has_inn(),
    )


@router.get("", response_model=CompanyProfileOut | None)
def get_company_profile(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Профиль или `null`, если он ещё не заводился — форма настроек показывает пустые поля.

    Читать может любой пользователь: карточка тендера объясняет оценку ссылками на поля
    профиля (`*_evidence`), и без доступа к ним объяснение было бы нечитаемым.
    """

    profile = company_profile_service.get_profile(db)
    return _out(profile) if profile is not None else None


@router.put("", response_model=CompanyProfileOut)
def update_company_profile(
    payload: CompanyProfileUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> CompanyProfileOut:
    """Сохраняет форму профиля. Присланы только изменённые разделы — остальные не трогаются.

    Оценки при этом не пересчитываются автоматически: пересчёт стоит денег и времени по
    каждому тендеру, а какие из них актуальны — решает человек. Прежние оценки остаются
    воспроизводимыми благодаря снимку профиля (`company_profile_snapshot`, раздел 7 ТЗ).
    """

    try:
        profile = company_profile_service.update_profile(db, _changes(payload))
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _out(profile)


def _require_profile(db: Session, profile_id: uuid.UUID):
    profile = company_profile_service.get_by_id(db, profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Компания не найдена"
        )
    return profile


@profiles_router.get("", response_model=list[CompanyProfileOut])
def list_company_profiles(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[CompanyProfileOut]:
    """Все компании: основная первой, дальше в порядке добавления.

    Читать может любой пользователь — по тем же причинам, что и основной профиль: карточка
    тендера объясняет оценку ссылками на поля компании.
    """

    return [_out(profile) for profile in company_profile_service.list_profiles(db)]


@profiles_router.post("", response_model=CompanyProfileOut, status_code=status.HTTP_201_CREATED)
def create_company_profile(
    payload: CompanyProfileUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> CompanyProfileOut:
    """Заводит ещё одну компанию. Ограничения на количество нет.

    Новая компания основной не становится (кроме случая, когда она первая в системе): смена
    основной меняет входы AI-оценки сразу по всем тендерам и делается отдельной кнопкой.
    """

    try:
        profile = company_profile_service.create_profile(db, _changes(payload))
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _out(profile)


@profiles_router.put("/{profile_id}", response_model=CompanyProfileOut)
def update_company_profile_by_id(
    profile_id: uuid.UUID,
    payload: CompanyProfileUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> CompanyProfileOut:
    profile = _require_profile(db, profile_id)
    try:
        profile = company_profile_service.update_profile(db, _changes(payload), profile)
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _out(profile)


@profiles_router.post("/{profile_id}/primary", response_model=CompanyProfileOut)
def make_company_profile_primary(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> CompanyProfileOut:
    """Делает компанию основной — той, с которой считается AI-оценка и синхронизируется
    история участий.

    Прежние оценки не пересчитываются: каждая хранит снимок профиля на момент расчёта и
    остаётся объяснимой, а что пересчитывать — решает человек.
    """

    profile = _require_profile(db, profile_id)
    return _out(company_profile_service.set_primary(db, profile))


@profiles_router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company_profile(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> Response:
    profile = _require_profile(db, profile_id)
    try:
        company_profile_service.delete_profile(db, profile)
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class EgrulCandidateOut(BaseModel):
    """Кандидат автопоиска. Отдаётся на подтверждение человеку и никуда не сохраняется —
    решение «это мы» принимает пользователь, а не автопоиск (раздел 7 ТЗ)."""

    legal_name: str | None
    inn: str | None
    kpp: str | None = None
    ogrn: str | None
    registration_date: date | None
    legal_address: str | None
    # Откуда взят кандидат: показывается рядом с ним, чтобы человек подтверждал данные
    # зная источник, а не безымянную строку.
    source: str
    source_url: str | None = None


@router.get("/egrul-lookup", response_model=list[EgrulCandidateOut])
def egrul_lookup(
    query: str = Query(..., min_length=3, max_length=300, description="ИНН, ОГРН, название или ссылка rusprofile"),
    _admin: User = Depends(require_admin),
) -> list[EgrulCandidateOut]:
    """Автопоиск юридических данных (раздел 7 ТЗ).

    Два источника за одной кнопкой, выбор — по виду запроса:

    * ссылка на карточку **rusprofile.ru** → разбор карточки. Даёт КПП, которого нет в
      выдаче ЕГРЮЛ, и работает, когда у ЕГРЮЛ включается защита от автозапросов;
    * ИНН, ОГРН или наименование → **ЕГРЮЛ**, авторитетный первоисточник.

    Поиск по ИНН на самом rusprofile недоступен программно (отвечает 404 при любых
    заголовках), поэтому от него принимается именно ссылка на карточку — притворяться, что
    мы умеем там искать, значило бы падать на каждом втором вызове.

    Ничего не сохраняет: возвращает кандидатов, а в профиль они попадают обычным PUT после
    того, как человек выбрал нужного. Разделение сознательное — автоматически записанный в
    профиль результат внешнего сервиса нельзя отличить от подтверждённого факта, а профиль
    подаётся в evidence AI-оценки.
    """

    if "rusprofile" in query.lower() or query.strip().startswith("http"):
        try:
            company = rusprofile.fetch_company(query)
        except RusprofileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return [
            EgrulCandidateOut(
                legal_name=company.legal_name or company.short_name,
                inn=company.inn,
                kpp=company.kpp,
                ogrn=company.ogrn,
                registration_date=company.registration_date,
                legal_address=company.legal_address,
                source="rusprofile",
                source_url=company.source_url,
            )
        ]

    try:
        companies = egrul_service.search(query)
    except EgrulError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return [
        EgrulCandidateOut(
            legal_name=item.legal_name,
            inn=item.inn,
            kpp=None,
            ogrn=item.ogrn,
            registration_date=item.registration_date,
            legal_address=item.legal_address,
            source="egrul",
        )
        for item in companies
    ]
