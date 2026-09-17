"""Профили компаний-заявителей (раздел 5.6 «Моя компания», раздел 7 ТЗ).

Компаний несколько (решение 07.09.2026), ограничения на количество нет. Одна из них —
основная (`is_primary`): именно её видят расчёт AI-оценки и синхронизация истории участий,
чтобы «наша компания» осталась однозначной там, где от неё зависят цифры. Остальные
карточки пока только хранятся — под аналитику по юрлицам, которая появится позже.

Каждый профиль заполняется вручную и целиком — форма в модальном окне, а не пополняемый
справочник: лицензий и проектов немного, а редактировать их построчно через API значило бы
городить CRUD там, где хватает одной формы.

Здесь же живёт превращение профиля в текст для модели: измерения Task и Competencies
(раздел 5.5.1) сравнивают требования тендера именно с этими сведениями, и формат подачи —
часть методики, а не деталь промпта.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_profile import CompanyProfile
from app.models.manufacturer import Manufacturer


class CompanyProfileError(RuntimeError):
    """Профиля нет или он пуст — считать Task/Competencies не из чего (раздел 5.5.1 ТЗ)."""


def get_mirtek(db: Session) -> Manufacturer | None:
    return db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))


def get_profile(db: Session) -> CompanyProfile | None:
    """Основная компания, если она заведена.

    Всё, что считает цифры — AI-оценка, синхронизация истории участий, — спрашивает профиль
    именно этой функцией и получает одну компанию, а не список: измерения «Задача» и
    «Компетенции» сравнивают тендер с конкретным юрлицом, и «какая из наших» здесь не должно
    быть вопросом.

    Отсутствие — нормальное состояние до первого заполнения, а не ошибка: раздел «Моя
    компания» показывает пустой список.
    """

    primary = db.scalar(
        select(CompanyProfile).where(CompanyProfile.is_primary.is_(True))
    )
    if primary is not None:
        return primary
    # Профиль МИРТЕК без пометки — след схемы, где компания была одна: до миграции 0037
    # признака `is_primary` не существовало, и такая запись по-прежнему «наша».
    mirtek = get_mirtek(db)
    if mirtek is None:
        return None
    return db.scalar(
        select(CompanyProfile).where(CompanyProfile.manufacturer_id == mirtek.id)
    )


def list_profiles(db: Session) -> list[CompanyProfile]:
    """Все компании: основная первой, дальше — в порядке добавления.

    Порядок фиксированный и не зависит от правок, чтобы строки в списке не перескакивали
    после каждого сохранения.
    """

    return list(
        db.scalars(
            select(CompanyProfile).order_by(
                CompanyProfile.is_primary.desc(), CompanyProfile.created_at
            )
        )
    )


def get_by_id(db: Session, profile_id: uuid.UUID) -> CompanyProfile | None:
    return db.get(CompanyProfile, profile_id)


def get_or_create(db: Session) -> CompanyProfile:
    """Основная компания; если её нет — заводится пустая, привязанная к МИРТЕК.

    Привязка к производителю нужна только первой, основной компании: она же «наш
    производитель» в справочнике. Дополнительные компании заводятся через `create_profile`
    и производителя не имеют.
    """

    profile = get_profile(db)
    if profile is not None:
        return profile
    mirtek = get_mirtek(db)
    if mirtek is None:
        raise CompanyProfileError(
            "В справочнике производителей нет записи МИРТЕК — основную компанию привязывать "
            "не к чему. Проверьте раздел «Настройки → Производители»."
        )
    profile = CompanyProfile(
        manufacturer_id=mirtek.id,
        is_primary=True,
        licenses=[],
        past_projects=[],
        field_sources={},
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def create_profile(db: Session, changes: dict[str, Any]) -> CompanyProfile:
    """Заводит ещё одну компанию.

    Основной она не становится: смена основной — отдельное осознанное действие, потому что
    от него меняются входы AI-оценки по всем тендерам разом. Исключение — самая первая
    компания в системе: без основной не считаются «Задача» и «Компетенции», и оставлять
    систему в этом состоянии из-за формальности незачем.
    """

    profile = CompanyProfile(
        manufacturer_id=None,
        is_primary=get_profile(db) is None,
        licenses=[],
        past_projects=[],
        field_sources={},
    )
    db.add(profile)
    _apply_changes(profile, changes)
    db.commit()
    db.refresh(profile)
    return profile


def delete_profile(db: Session, profile: CompanyProfile) -> None:
    """Удаляет компанию. Основную — только после передачи роли другой.

    Иначе система осталась бы без «нашей компании», и AI-оценка молча перестала бы считать
    два измерения из трёх.
    """

    if profile.is_primary:
        raise CompanyProfileError(
            "Это основная компания: на ней держатся измерения «Задача» и «Компетенции». "
            "Сначала сделайте основной другую компанию, потом удаляйте эту."
        )
    db.delete(profile)
    db.commit()


def set_primary(db: Session, profile: CompanyProfile) -> CompanyProfile:
    """Делает компанию основной, снимая пометку с прежней.

    Снятие и установка идут одной транзакцией с промежуточным `flush`: частичный уникальный
    индекс не терпит двух основных даже на миг внутри операции.
    """

    if profile.is_primary:
        return profile
    for other in db.scalars(
        select(CompanyProfile).where(CompanyProfile.is_primary.is_(True))
    ):
        other.is_primary = False
    db.flush()
    profile.is_primary = True
    db.commit()
    db.refresh(profile)
    return profile


# Поля, у которых бывает автоподстановка из ЕГРЮЛ: только для них имеет смысл помнить
# источник значения (`field_sources`). Остальное вводится руками по определению.
AUTO_FILLABLE_FIELDS = (
    "legal_name",
    "inn",
    "kpp",
    "ogrn",
    "registration_date",
    "legal_address",
    "years_of_experience",
)


def update_profile(
    db: Session, changes: dict[str, Any], profile: CompanyProfile | None = None
) -> CompanyProfile:
    """Сохраняет форму профиля. Приходят только присланные поля (`exclude_unset` на уровне
    схемы), поэтому частичное сохранение не затирает соседние разделы.

    Без `profile` правится основная компания — так работают прежние вызовы, где компания в
    системе была одна.
    """

    if profile is None:
        profile = get_or_create(db)
    _apply_changes(profile, changes)
    db.commit()
    db.refresh(profile)
    return profile


def _apply_changes(profile: CompanyProfile, changes: dict[str, Any]) -> None:
    """Переносит поля формы в запись, не коммитя.

    Правка руками помечает поле как подтверждённое человеком: значение, пришедшее из
    автопоиска и затем просмотренное в форме, перестаёт быть «непроверенным». Форма может
    прислать `field_sources` и сама — так подтверждается результат автопоиска, принятый
    целиком, без ручной правки каждой строки.
    """

    changes = dict(changes)
    explicit_sources = changes.pop("field_sources", None)
    sources: dict[str, Any] = dict(profile.field_sources or {})

    for field_name, value in changes.items():
        if field_name in AUTO_FILLABLE_FIELDS and getattr(profile, field_name) != value:
            sources[field_name] = {"source": "manual", "verified_by_user": True}
        setattr(profile, field_name, value)

    if explicit_sources:
        sources.update(explicit_sources)
    profile.field_sources = sources


def require_filled(db: Session) -> CompanyProfile:
    profile = get_profile(db)
    if profile is None or not profile.is_filled():
        raise CompanyProfileError(
            "Профиль компании не заполнен: без допусков, стажа и списка проектов измерения "
            "«Задача» и «Компетенции» считать не из чего. Заполните раздел "
            "«Настройки → Профиль компании»."
        )
    return profile


def snapshot(profile: CompanyProfile) -> dict:
    """Копия профиля, которая кладётся рядом с оценкой (`company_profile_snapshot`).

    Без неё оценка становится невоспроизводимой: профиль поправили — и ссылки `*_evidence`
    на поля профиля указывают уже на другой текст (раздел 5.5.1 ТЗ).
    """

    return {
        "legal_name": profile.legal_name,
        "inn": profile.inn,
        "kpp": profile.kpp,
        "ogrn": profile.ogrn,
        "registration_date": (
            profile.registration_date.isoformat() if profile.registration_date else None
        ),
        "legal_address": profile.legal_address,
        "years_of_experience": profile.years_of_experience,
        "licenses": profile.licenses or [],
        "past_projects": profile.past_projects or [],
        "field_sources": profile.field_sources or {},
    }


def profile_fields(profile: CompanyProfile) -> list[tuple[str, str]]:
    """Разворачивает профиль в пронумерованный список «ключ поля → текст».

    Ключ (`years_of_experience`, `license:2`, `project:5`) уходит в `*_evidence` как
    `ref_id`: модель ссылается на строку профиля по номеру, а сохраняем мы устойчивый ключ,
    по которому интерфейс потом покажет, из чего сложилось число.
    """

    fields: list[tuple[str, str]] = []
    # Юридические данные идут первыми: измерение Competencies сверяет с ними требования
    # к участнику (год регистрации против «опыт не менее N лет», адрес против условий о
    # регионе), и без них модель домысливает то, что записано в реестре.
    if profile.legal_name:
        fields.append(("legal_name", f"Юридическое наименование: {profile.legal_name}"))
    if profile.inn:
        fields.append(("inn", f"ИНН: {profile.inn}"))
    if profile.kpp:
        fields.append(("kpp", f"КПП: {profile.kpp}"))
    if profile.ogrn:
        fields.append(("ogrn", f"ОГРН: {profile.ogrn}"))
    if profile.registration_date:
        fields.append(
            (
                "registration_date",
                f"Дата регистрации компании: {profile.registration_date.strftime('%d.%m.%Y')}",
            )
        )
    if profile.legal_address:
        fields.append(("legal_address", f"Юридический адрес: {profile.legal_address}"))
    if profile.years_of_experience:
        fields.append(
            ("years_of_experience", f"Стаж работы компании: {profile.years_of_experience} лет")
        )
    for index, license_item in enumerate(profile.licenses or [], start=1):
        parts = [
            str(license_item.get(key))
            for key in ("name", "number", "valid_until", "issuer")
            if license_item.get(key)
        ]
        fields.append((f"license:{index}", "Допуск/лицензия: " + ", ".join(parts)))
    for index, project in enumerate(profile.past_projects or [], start=1):
        parts = [
            str(project.get(key))
            for key in ("work_type", "customer", "volume", "year", "description")
            if project.get(key)
        ]
        fields.append((f"project:{index}", "Реализованный проект: " + ", ".join(parts)))
    return fields


def profile_block(profile: CompanyProfile) -> str:
    """Тот же список, но текстом для промпта — с номерами, по которым модель ссылается."""

    lines = [
        f"{number}. {text}" for number, (_, text) in enumerate(profile_fields(profile), start=1)
    ]
    return "\n".join(lines) if lines else "(профиль компании пуст)"


def field_key_by_number(profile: CompanyProfile, number: int) -> str | None:
    fields = profile_fields(profile)
    if 1 <= number <= len(fields):
        return fields[number - 1][0]
    return None
