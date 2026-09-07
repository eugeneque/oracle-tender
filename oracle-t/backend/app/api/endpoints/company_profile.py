"""Профиль компании (раздел 5.6 «Настройки» → «Профиль компании», раздел 7 ТЗ).

Без заполненного профиля измерения Task и Competencies (раздел 5.5.1) считать не из чего,
поэтому раздел заведён отдельным эндпоинтом, а не полем в общих настройках: его заполняют
один раз и целиком, и он должен быть виден как самостоятельный шаг настройки системы.

Правит только администратор: профиль — вход главной метрики, и незаметная правка списка
допусков меняет оценки по всем тендерам разом.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


def _out(profile) -> CompanyProfileOut:
    return CompanyProfileOut(
        id=profile.id,
        manufacturer_id=profile.manufacturer_id,
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

    changes = payload.model_dump(exclude_unset=True)
    if "licenses" in changes and changes["licenses"] is not None:
        changes["licenses"] = [item.model_dump() for item in payload.licenses]
    if "past_projects" in changes and changes["past_projects"] is not None:
        changes["past_projects"] = [item.model_dump() for item in payload.past_projects]

    try:
        profile = company_profile_service.update_profile(db, changes)
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _out(profile)


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
