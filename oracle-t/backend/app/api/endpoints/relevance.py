"""Отбор закупок: фразы сбора, профили отбора, проверка моделью (раздел 5.1.1 ТЗ).

С 05.10.2026 здесь одна логика вместо двух:

* **фразы сбора** (`/relevance/terms`) — что система спрашивает у площадок. Меняет только
  администратор: от них зависит, что вообще попадёт в базу;
* **профили отбора** (`/relevance/profiles`) — как отбирать уже собранное. Общие (по
  умолчанию) ведёт администратор, личные заводит любой специалист;
* **проверка моделью** — второй, независимый слой (`/relevance/ai-check`).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.relevance_profile import CollectionTerm, RelevanceProfile
from app.models.source import Source
from app.models.user import User, UserRole
from app.services import ai_relevance_service, okpd2_service, relevance_profile_service, relevance_service
from app.services.audit import log_action

router = APIRouter(prefix="/relevance", tags=["relevance"])

MAX_TERMS = 60


def _clean_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        term = value.strip()
        if term and term not in result:
            result.append(term)
    if len(result) > MAX_TERMS:
        raise ValueError(f"Не больше {MAX_TERMS} значений в списке")
    return result


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


# --- фразы сбора --------------------------------------------------------------------------


class TermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phrase: str
    is_active: bool


class TermIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=2, max_length=300)

    @field_validator("phrase")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = " ".join(value.split())
        if "*" in value or "~" in value:
            # Площадки ищут обычную фразу: звёздочка ушла бы в их поиск буквально.
            raise ValueError("Фраза сбора — обычный текст, без «*» и «~»: площадки их не понимают")
        return value


class TermUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


@router.get("/terms", response_model=list[TermOut])
def list_terms(db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> list:
    relevance_service.get_or_create_profile(db)
    db.commit()
    return list(
        db.scalars(select(CollectionTerm).order_by(CollectionTerm.created_at, CollectionTerm.phrase))
    )


@router.post("/terms", response_model=TermOut, status_code=status.HTTP_201_CREATED)
def create_term(
    payload: TermIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> CollectionTerm:
    exists = db.scalar(
        select(CollectionTerm).where(func.lower(CollectionTerm.phrase) == payload.phrase.lower())
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Такая фраза уже есть")
    term = CollectionTerm(phrase=payload.phrase)
    db.add(term)
    log_action(
        db,
        component="relevance",
        action="create_term",
        result="ok",
        details=f"Фраза сбора «{payload.phrase}»",
        user_id=admin.id,
    )
    db.commit()
    db.refresh(term)
    return term


@router.patch("/terms/{term_id}", response_model=TermOut)
def update_term(
    term_id: uuid.UUID,
    payload: TermUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> CollectionTerm:
    term = db.get(CollectionTerm, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фраза не найдена")
    term.is_active = payload.is_active
    log_action(
        db,
        component="relevance",
        action="update_term",
        result="ok",
        details=f"Фраза сбора «{term.phrase}»: {'включена' if term.is_active else 'выключена'}",
        user_id=admin.id,
    )
    db.commit()
    db.refresh(term)
    return term


@router.delete("/terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_term(
    term_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> None:
    term = db.get(CollectionTerm, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фраза не найдена")
    phrase = term.phrase
    db.delete(term)
    log_action(
        db,
        component="relevance",
        action="delete_term",
        result="ok",
        details=f"Фраза сбора «{phrase}»",
        user_id=admin.id,
    )
    db.commit()


# --- профили отбора ----------------------------------------------------------------------


class ProfileRules(BaseModel):
    """Правила профиля — то, что видит движок сопоставления."""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)
    exclusion_keywords: list[str] = Field(default_factory=list)
    okpd2_codes: list[str] = Field(default_factory=list)
    match_mode: str = Field(default="any", pattern="^(any|all)$")
    # `narrow` — нужны и слова, и код; `either` — достаточно одного.
    okpd2_mode: str = Field(default="narrow", pattern="^(narrow|either)$")
    # Площадки, к которым применяется профиль; пусто — ко всем.
    source_keys: list[str] = Field(default_factory=list)

    @field_validator("source_keys")
    @classmethod
    def _source_keys(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("keywords", "exclusion_keywords")
    @classmethod
    def _terms(cls, values: list[str]) -> list[str]:
        return _clean_terms(values)

    @field_validator("okpd2_codes")
    @classmethod
    def _codes(cls, values: list[str]) -> list[str]:
        return okpd2_service.clean_prefixes(values)


class ProfileIn(ProfileRules):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    # Общий профиль (действует по умолчанию у всех) — только администратор.
    is_default: bool = False
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Название не может быть пустым")
        return value

    @model_validator(mode="after")
    def _has_rules(self) -> "ProfileIn":
        # Профиль без слов и кодов подошёл бы к любой закупке: «фильтр», который ничего не
        # фильтрует, — это ошибка заполнения, а не намерение.
        if not self.keywords and not self.okpd2_codes:
            raise ValueError("Укажите хотя бы одно ключевое слово или код ОКПД2")
        return self


class ProfileItemOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    keywords: list[str]
    exclusion_keywords: list[str]
    okpd2_codes: list[str]
    match_mode: str
    okpd2_mode: str
    source_keys: list[str]
    is_default: bool
    is_active: bool
    owner_id: uuid.UUID | None
    owner_name: str | None
    can_edit: bool
    # Сколько собранных закупок подходит профилю. Заполнено в ответе на сохранение; в
    # списке `None` — досчитывать все профили на каждое открытие страницы незачем.
    matched_count: int | None = None
    updated_at: datetime


class PreviewSampleOut(BaseModel):
    id: uuid.UUID
    title: str
    okpd2_code: str | None
    publish_date: str | None


class PreviewOut(BaseModel):
    count: int
    total: int
    samples: list[PreviewSampleOut]


def _profile_out(
    db: Session, profile: RelevanceProfile, user: User, *, matched_count: int | None = None
) -> ProfileItemOut:
    owner = db.get(User, profile.owner_id) if profile.owner_id else None
    return ProfileItemOut(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        keywords=profile.keywords or [],
        exclusion_keywords=profile.exclusion_keywords or [],
        okpd2_codes=profile.okpd2_codes or [],
        match_mode=profile.match_mode,
        okpd2_mode=profile.okpd2_mode,
        source_keys=profile.source_keys or [],
        is_default=profile.is_default,
        is_active=profile.is_active,
        owner_id=profile.owner_id,
        owner_name=owner.full_name if owner else None,
        can_edit=relevance_profile_service.can_edit(profile, user),
        matched_count=matched_count,
        updated_at=profile.updated_at,
    )


def _get_profile_or_404(db: Session, profile_id: uuid.UUID) -> RelevanceProfile:
    profile = db.get(RelevanceProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Профиль не найден")
    return profile


def _ensure_sources_exist(db: Session, keys: list[str]) -> None:
    """Привязка к несуществующей площадке молча ничего не сужала бы."""

    if not keys:
        return
    known = set(db.scalars(select(Source.key).where(Source.key.in_(keys))))
    missing = [key for key in keys if key not in known]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестная площадка: {', '.join(missing)}",
        )


def _ensure_unique_name(
    db: Session,
    name: str,
    *,
    owner_id: uuid.UUID | None,
    is_default: bool,
    exclude: uuid.UUID | None = None,
) -> None:
    """Два профиля с одним именем у одного автора (или два общих) — путаница в меню."""

    query = select(RelevanceProfile.id).where(func.lower(RelevanceProfile.name) == name.lower())
    query = (
        query.where(RelevanceProfile.is_default.is_(True))
        if is_default
        else query.where(
            RelevanceProfile.is_default.is_(False), RelevanceProfile.owner_id == owner_id
        )
    )
    if exclude is not None:
        query = query.where(RelevanceProfile.id != exclude)
    if db.scalar(query) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Профиль «{name}» уже есть")


def _ensure_may_set_default(user: User, wants_default: bool) -> None:
    if wants_default and not _is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Общий профиль по умолчанию заводит администратор",
        )


@router.get("/profiles", response_model=list[ProfileItemOut])
def list_profiles(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ProfileItemOut]:
    relevance_service.get_or_create_profile(db)
    db.commit()
    profiles = db.scalars(
        select(RelevanceProfile).order_by(
            RelevanceProfile.is_default.desc(), func.lower(RelevanceProfile.name)
        )
    )
    return [_profile_out(db, profile, user) for profile in profiles]


@router.post("/profiles/preview", response_model=PreviewOut)
def preview_profile(
    payload: ProfileRules,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """Что отберёт профиль с такими правилами — до сохранения."""

    draft = RelevanceProfile(name="предпросмотр", **payload.model_dump())
    return relevance_profile_service.preview(db, draft)


@router.post("/profiles", response_model=ProfileItemOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProfileItemOut:
    _ensure_may_set_default(user, payload.is_default)
    _ensure_unique_name(db, payload.name, owner_id=user.id, is_default=payload.is_default)
    _ensure_sources_exist(db, payload.source_keys)
    profile = RelevanceProfile(owner_id=user.id, **payload.model_dump())
    db.add(profile)
    db.flush()
    matched = relevance_profile_service.refresh_matches(db, profile)
    log_action(
        db,
        component="relevance",
        action="create_profile",
        result="ok",
        details=f"{'Общий профиль' if profile.is_default else 'Профиль'} «{profile.name}»",
        user_id=user.id,
    )
    db.commit()
    if profile.is_default:
        relevance_profile_service.after_default_change(db)
    db.refresh(profile)
    return _profile_out(db, profile, user, matched_count=matched)


@router.put("/profiles/{profile_id}", response_model=ProfileItemOut)
def update_profile(
    profile_id: uuid.UUID,
    payload: ProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProfileItemOut:
    profile = _get_profile_or_404(db, profile_id)
    if not relevance_profile_service.can_edit(profile, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Общий профиль меняет администратор"
                if profile.is_default
                else "Менять профиль может его автор или администратор"
            ),
        )
    _ensure_may_set_default(user, payload.is_default and not profile.is_default)
    _ensure_unique_name(
        db,
        payload.name,
        owner_id=profile.owner_id,
        is_default=payload.is_default,
        exclude=profile.id,
    )
    _ensure_sources_exist(db, payload.source_keys)
    was_default = profile.is_default
    data = payload.model_dump()
    rules_changed = any(
        getattr(profile, key) != data[key]
        for key in ("keywords", "exclusion_keywords", "okpd2_codes", "match_mode", "okpd2_mode")
    )
    marks_changed = rules_changed or any(
        getattr(profile, key) != data[key] for key in ("source_keys", "is_default", "is_active")
    )
    for key, value in data.items():
        setattr(profile, key, value)
    if rules_changed:
        profile.rules_version += 1
    matched = relevance_profile_service.refresh_matches(db, profile)
    log_action(
        db,
        component="relevance",
        action="update_profile",
        result="ok",
        details=f"{'Общий профиль' if profile.is_default else 'Профиль'} «{profile.name}»",
        user_id=user.id,
    )
    db.commit()
    if (was_default or profile.is_default) and marks_changed:
        relevance_profile_service.after_default_change(db)
    db.refresh(profile)
    return _profile_out(db, profile, user, matched_count=matched)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    profile = _get_profile_or_404(db, profile_id)
    if not relevance_profile_service.can_edit(profile, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Общий профиль удаляет администратор"
                if profile.is_default
                else "Удалить профиль может его автор или администратор"
            ),
        )
    name, was_default = profile.name, profile.is_default
    db.delete(profile)
    log_action(
        db,
        component="relevance",
        action="delete_profile",
        result="ok",
        details=f"{'Общий профиль' if was_default else 'Профиль'} «{name}»",
        user_id=user.id,
    )
    db.commit()
    if was_default:
        relevance_profile_service.after_default_change(db)


# --- пересчёт и проверка моделью ----------------------------------------------------------


class BackfillResultOut(BaseModel):
    processed: int
    passed: int
    rejected: int


@router.post("/recalculate", response_model=BackfillResultOut)
def recalculate(
    only_unprocessed: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BackfillResultOut:
    """Пересчитывает отметку общих профилей по всем собранным закупкам.

    После правки общего профиля это делается само; кнопка — на случай ручной правки базы.
    """

    result = relevance_service.backfill(db, only_unprocessed=only_unprocessed)
    log_action(
        db,
        component="relevance",
        action="recalculate",
        result="ok",
        details=(
            f"Обработано {result['processed']}, прошли {result['passed']}, "
            f"отсеяно {result['rejected']}"
        ),
        user_id=admin.id,
    )
    db.commit()
    return BackfillResultOut(**result)


class AiCheckResultOut(BaseModel):
    checked: int
    relevant: int
    rejected: int
    failed: int
    messages: list[str]
    # Сколько ещё ждёт очереди: без этого числа кнопка «Проверить» выглядит бесконечной.
    pending: int


class AiPendingOut(BaseModel):
    pending: int


@router.get("/ai-pending", response_model=AiPendingOut)
def ai_pending(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AiPendingOut:
    return AiPendingOut(pending=ai_relevance_service.pending_count(db))


@router.post("/ai-check", response_model=AiCheckResultOut)
def ai_check(
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AiCheckResultOut:
    """Проверка моделью: действительно ли закупка наша (раздел 5.4 ТЗ).

    Пачками, а не всё разом: каждая проверка — вызов модели, и разбор нескольких тысяч
    накопленных закупок за один запрос упёрся бы в таймаут.
    """

    result = ai_relevance_service.check_batch(db, limit=limit, actor=admin)
    return AiCheckResultOut(
        checked=result.checked,
        relevant=result.relevant,
        rejected=result.rejected,
        failed=result.failed,
        messages=result.messages,
        pending=ai_relevance_service.pending_count(db),
    )
