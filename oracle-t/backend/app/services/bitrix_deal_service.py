"""Тендеры → сделки Bitrix24 (08.10.2026).

Продолжение `bitrix_service` (CSV лидов для ручного импорта): теперь тендер кладётся в CRM
сам, через входящий вебхук, и не лидом, а **сделкой** — так решил заказчик: под тендеры на
портале заведена отдельная воронка («Тендеры — тест ИИ», id 5) со стадией «Парсинг
опубликованных тендеров», и работа по закупке дальше идёт по её стадиям.

Что куда идёт:

- стандартные поля сделки — название, сумма и валюта, комментарий (тот же, что у лида:
  заключение ИИ, процент победителя, ссылка);
- поля портала `UF_CRM_*` — НМЦК, организатор, сроки приёма заявок, дата публикации, ссылка,
  наименование процедуры, способ закупки. Коды полей — конкретного портала МИРТЕК (их завёл
  администратор, сняты через `crm.deal.fields` 08.10.2026) и собраны в одну таблицу
  `DEAL_FIELDS`; «Проверить подключение» сверяет её с порталом и называет поля, которых
  там нет;
- остальное (позиции, требования ТЗ, сопоставление с каталогом) пойдёт в локальное
  приложение портала — таблицу во вкладке сделки, которую заказчик добавит отдельно.

Правила:

1. **Отправка выключена, пока её не включили** (`push_enabled`). Портал боевой, тестового
   нет; настроить, проверить подключение (только чтение) и посмотреть предпросмотр сделки
   можно, не записав в CRM ничего.
2. **Одна закупка — одна сделка.** Сделка помечается стандартными `ORIGINATOR_ID` /
   `ORIGIN_ID` (наш id тендера), и перед созданием ищется по ним — повторная отправка
   обновляет найденную, даже если связь у нас потеряна.
3. **Обновление не трогает работу менеджера.** Стадия и воронка задаются только при
   создании: менеджер мог сдвинуть сделку дальше, и возврат её в «Парсинг» при повторной
   отправке сломал бы ему воронку. Пустые у нас значения не отправляются вовсе — иначе они
   стёрли бы то, что менеджер заполнил руками.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import SecretStorageError, decrypt_secret, encrypt_secret
from app.models.bitrix_deal import BitrixDealLink
from app.models.integration_setting import Bitrix24Settings
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.user import User
from app.services import bitrix_service
from app.services.audit import log_action
from app.services.bitrix_client import (
    BitrixClient,
    BitrixError,
    mask_webhook,
    normalize_webhook,
    portal_of,
)

_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Метка «сделка заведена нами» в стандартном поле ORIGINATOR_ID.
ORIGINATOR_ID = "sova-scanner"


class BitrixNotConfiguredError(BitrixError):
    """Вебхук не задан — эндпоинт отвечает 400 с подсказкой, где его указать."""


class BitrixPushDisabledError(BitrixError):
    """Отправка в CRM выключена администратором — эндпоинт отвечает 409."""


# --- маппинг полей ----------------------------------------------------------------------------


def _iso_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _iso_datetime(value: datetime | None) -> str:
    # Со смещением: портал хранит время в своём часовом поясе и без смещения прочитал бы UTC
    # как московское — окончание приёма заявок съехало бы на три часа.
    return value.isoformat() if value is not None else ""


def _amount(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else ""


def _money(tender: Tender) -> str:
    # Поле типа «деньги» Bitrix24 принимает строкой «сумма|валюта».
    if tender.price is None:
        return ""
    return f"{tender.price:.2f}|{tender.currency or 'RUB'}"


@dataclass(frozen=True)
class DealField:
    key: str
    title: str
    value: Callable[[Tender], str]


# Пользовательские поля сделки портала b24.mirtek.info. Код — как в `crm.deal.fields`, title —
# подпись поля на портале, чтобы при сверке было понятно, о каком поле речь.
DEAL_FIELDS: list[DealField] = [
    DealField("UF_CRM_1783504196144", "Наименование процедуры на площадке", lambda t: t.title or ""),
    DealField("UF_CRM_1784720624836", "НМЦК, руб.", _money),
    DealField(
        "UF_CRM_1783501056164",
        "Организатор",
        lambda t: t.organizer_name or t.customer_name or "",
    ),
    DealField("UF_CRM_1784729171691", "Способ закупки", lambda t: t.procurement_method or ""),
    DealField("UF_CRM_1783509007461", "Дата публикации на площадке", lambda t: _iso_date(t.publish_date)),
    DealField("UF_CRM_1783503128609", "Начало приёма заявок", lambda t: _iso_date(t.application_start)),
    DealField("UF_CRM_1783503179", "Окончание приёма заявок", lambda t: _iso_datetime(t.application_end)),
    DealField("UF_CRM_1783503317927", "Ссылка на процедуру", lambda t: t.source_url or ""),
]

# Подписи стандартных полей — для предпросмотра.
_STANDARD_TITLES = {
    "TITLE": "Название сделки",
    "CATEGORY_ID": "Воронка",
    "STAGE_ID": "Стадия",
    "OPPORTUNITY": "Сумма",
    "CURRENCY_ID": "Валюта",
    "SOURCE_DESCRIPTION": "Дополнительно об источнике",
    "COMMENTS": "Комментарий",
    "ORIGINATOR_ID": "Внешний источник",
    "ORIGIN_ID": "Идентификатор во внешнем источнике",
}


def build_deal_fields(db: Session, tender: Tender) -> dict[str, Any]:
    """Поля сделки без воронки и стадии — их добавляет только создание (см. правило 3)."""

    lead = bitrix_service.build_lead(db, tender)
    platform = tender.source.name if tender.source else ""
    fields: dict[str, Any] = {
        # Номер закупки в названии — по нему сделку ищут на портале и узнают в канбане.
        "TITLE": f"Тендер {tender.external_id}: {tender.title}"[:250],
        "OPPORTUNITY": _amount(tender.price),
        "CURRENCY_ID": tender.currency or "RUB",
        "SOURCE_DESCRIPTION": f"Sova Scanner / {platform}" if platform else "Sova Scanner",
        "COMMENTS": lead["COMMENTS"],
        "ORIGINATOR_ID": ORIGINATOR_ID,
        "ORIGIN_ID": str(tender.id),
    }
    for field in DEAL_FIELDS:
        fields[field.key] = field.value(tender)
    # Пустое не отправляем: на обновлении оно стёрло бы значение, введённое менеджером.
    return {key: value for key, value in fields.items() if value not in ("", None)}


def field_title(key: str) -> str:
    if key in _STANDARD_TITLES:
        return _STANDARD_TITLES[key]
    for field in DEAL_FIELDS:
        if field.key == key:
            return field.title
    return key


def deal_url(portal: str, deal_id: int) -> str:
    return f"{portal}/crm/deal/details/{deal_id}/"


# --- настройки --------------------------------------------------------------------------------


def _get_or_create(db: Session) -> Bitrix24Settings:
    settings = db.get(Bitrix24Settings, _SINGLETON_ID)
    if settings is None:
        settings = Bitrix24Settings(id=_SINGLETON_ID)
        db.add(settings)
        db.flush()
    return settings


def _webhook(settings: Bitrix24Settings | None) -> str:
    if settings is None or not settings.webhook_url:
        raise BitrixNotConfiguredError(
            "Bitrix24 не подключён: укажите вебхук в разделе «Интеграции → Bitrix24»."
        )
    try:
        return decrypt_secret(settings.webhook_url)
    except SecretStorageError as exc:
        raise BitrixNotConfiguredError(
            "Сохранённый вебхук не расшифровывается (сменился ключ шифрования) — введите его заново."
        ) from exc


def to_out(db: Session, settings: Bitrix24Settings) -> dict[str, Any]:
    updated_by = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    webhook = None
    if settings.webhook_url:
        try:
            webhook = decrypt_secret(settings.webhook_url)
        except SecretStorageError:
            webhook = None
    return {
        "is_configured": webhook is not None,
        "webhook_masked": mask_webhook(webhook) if webhook else None,
        "portal": portal_of(webhook) if webhook else None,
        "category_id": settings.category_id,
        "stage_id": settings.stage_id,
        "push_enabled": settings.push_enabled,
        "updated_at": settings.updated_at if settings.webhook_url else None,
        "updated_by": updated_by.full_name if updated_by else None,
        "last_check_at": settings.last_check_at,
        "last_check_status": settings.last_check_status,
        "last_check_message": settings.last_check_message,
    }


def get_settings_out(db: Session) -> dict[str, Any]:
    return to_out(db, _get_or_create(db))


def update_settings(db: Session, changes: dict[str, Any], *, actor: User) -> dict[str, Any]:
    """PATCH-семантика: не присланное поле не трогаем, пустой вебхук — отключение."""

    settings = _get_or_create(db)
    notes: list[str] = []

    if "webhook_url" in changes:
        raw = (changes["webhook_url"] or "").strip()
        if raw:
            webhook = normalize_webhook(raw)
            settings.webhook_url = encrypt_secret(webhook)
            notes.append(f"вебхук задан ({portal_of(webhook)})")
        else:
            settings.webhook_url = None
            # Без вебхука отправлять некуда — и включённой отправка оставаться не должна,
            # иначе новый вебхук начнёт писать в CRM сразу после сохранения.
            settings.push_enabled = False
            notes.append("вебхук удалён")
        settings.last_check_at = None
        settings.last_check_status = None
        settings.last_check_message = None

    if changes.get("category_id") is not None:
        settings.category_id = int(changes["category_id"])
        notes.append(f"воронка {settings.category_id}")
    if changes.get("stage_id"):
        settings.stage_id = str(changes["stage_id"]).strip()
        notes.append(f"стадия {settings.stage_id}")

    if changes.get("push_enabled") is not None:
        enabled = bool(changes["push_enabled"])
        if enabled and not settings.webhook_url:
            raise BitrixNotConfiguredError("Сначала укажите вебхук — отправлять сделки некуда.")
        settings.push_enabled = enabled
        notes.append("отправка включена" if enabled else "отправка выключена")

    settings.updated_by_id = actor.id
    log_action(
        db,
        component="integrations",
        action="update_bitrix24_settings",
        result="success",
        details=", ".join(notes) or "без изменений",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    return to_out(db, settings)


def _client(db: Session) -> BitrixClient:
    return BitrixClient(_webhook(db.get(Bitrix24Settings, _SINGLETON_ID)))


# --- проверка подключения (только чтение) -----------------------------------------------------


def _stage_entity(category_id: int) -> str:
    # Стадии основной воронки лежат в справочнике DEAL_STAGE, остальных — DEAL_STAGE_<id>.
    return "DEAL_STAGE" if category_id == 0 else f"DEAL_STAGE_{category_id}"


def check_connection(db: Session, *, actor: User) -> dict[str, Any]:
    """Проверяет вебхук, воронку, стадию и поля портала. Ничего на портале не меняет:
    вызываются только `crm.status.list` и `crm.deal.fields`."""

    settings = _get_or_create(db)
    stages: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    stage_found = False
    try:
        with _client(db) as client:
            raw_stages = client.call(
                "crm.status.list",
                {"filter": {"ENTITY_ID": _stage_entity(settings.category_id)}, "order": {"SORT": "ASC"}},
            ) or []
            stages = [
                {"id": str(item.get("STATUS_ID")), "name": str(item.get("NAME") or "")}
                for item in raw_stages
            ]
            stage_found = any(stage["id"] == settings.stage_id for stage in stages)
            portal_fields = client.call("crm.deal.fields") or {}
            missing = [
                {"key": field.key, "title": field.title}
                for field in DEAL_FIELDS
                if field.key not in portal_fields
            ]
            portal = client.portal
    except BitrixError as exc:
        return _save_check(db, settings, actor, success=False, message=str(exc))

    if not stages:
        message = f"Вебхук работает, но у воронки {settings.category_id} на портале нет стадий — проверьте номер воронки."
        success = False
    elif not stage_found:
        message = f"Вебхук работает, но в воронке {settings.category_id} нет стадии {settings.stage_id} — выберите стадию из списка."
        success = False
    else:
        stage_name = next(stage["name"] for stage in stages if stage["id"] == settings.stage_id)
        message = f"Подключено к {portal}: сделки будут вставать на стадию «{stage_name}»."
        if missing:
            message += f" На портале не найдено полей: {len(missing)} — они отправляться не будут."
        success = True

    result = _save_check(db, settings, actor, success=success, message=message)
    result.update(stages=stages, stage_found=stage_found, missing_fields=missing)
    return result


def _save_check(
    db: Session, settings: Bitrix24Settings, actor: User, *, success: bool, message: str
) -> dict[str, Any]:
    settings.last_check_at = datetime.now(timezone.utc)
    settings.last_check_status = "ok" if success else "error"
    settings.last_check_message = message
    log_action(
        db,
        component="integrations",
        action="test_bitrix24_connection",
        result="success" if success else "error",
        level=LogLevel.INFO if success else LogLevel.WARNING,
        details=message,
        user_id=actor.id,
    )
    db.commit()
    return {"success": success, "message": message, "stages": [], "stage_found": False, "missing_fields": []}


# --- предпросмотр и отправка ------------------------------------------------------------------


def _tender(db: Session, tender_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise LookupError("Тендер не найден")
    return tender


def preview_deal(db: Session, tender_id: uuid.UUID) -> dict[str, Any]:
    """Какая сделка получится из тендера — без обращения к порталу."""

    tender = _tender(db, tender_id)
    settings = _get_or_create(db)
    fields = build_deal_fields(db, tender)
    link = db.get(BitrixDealLink, tender.id)
    action = "update" if link and link.deal_id else "add"
    if action == "add":
        fields = {"CATEGORY_ID": settings.category_id, "STAGE_ID": settings.stage_id, **fields}
    return {
        "tender_id": tender.id,
        "deal_id": link.deal_id if link else None,
        "action": action,
        "push_enabled": settings.push_enabled,
        "fields": [
            {"key": key, "title": field_title(key), "value": str(value)}
            for key, value in fields.items()
        ],
    }


def _find_deal(client: BitrixClient, tender: Tender) -> int | None:
    found = client.call(
        "crm.deal.list",
        {
            "filter": {"ORIGINATOR_ID": ORIGINATOR_ID, "ORIGIN_ID": str(tender.id)},
            "select": ["ID"],
            "order": {"ID": "ASC"},
        },
    ) or []
    return int(found[0]["ID"]) if found else None


def push_tender(db: Session, tender_id: uuid.UUID, *, actor: User) -> dict[str, Any]:
    """Создаёт сделку под тендер или обновляет уже заведённую."""

    tender = _tender(db, tender_id)
    settings = _get_or_create(db)
    if not settings.push_enabled:
        raise BitrixPushDisabledError(
            "Отправка в Bitrix24 выключена. Включите её в разделе «Интеграции → Bitrix24», "
            "когда проверите подключение и предпросмотр сделки."
        )

    link = db.get(BitrixDealLink, tender.id) or BitrixDealLink(tender_id=tender.id)
    fields = build_deal_fields(db, tender)
    try:
        with _client(db) as client:
            deal_id = _find_deal(client, tender)
            if deal_id is None:
                created = True
                deal_id = int(
                    client.call(
                        "crm.deal.add",
                        {
                            "fields": {
                                "CATEGORY_ID": settings.category_id,
                                "STAGE_ID": settings.stage_id,
                                **fields,
                            },
                            # Без записи в живую ленту: сотня тендеров за утро завалила бы её.
                            "params": {"REGISTER_SONET_EVENT": "N"},
                        },
                    )
                )
            else:
                created = False
                client.call(
                    "crm.deal.update",
                    {"id": deal_id, "fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}},
                )
            portal = client.portal
    except BitrixError as exc:
        link.last_error = str(exc)
        db.merge(link)
        log_action(
            db,
            component="integrations",
            action="push_bitrix24_deal",
            result="error",
            level=LogLevel.WARNING,
            details=f"Тендер {tender.external_id}: {exc}",
            user_id=actor.id,
        )
        db.commit()
        raise

    link.deal_id = deal_id
    link.category_id = settings.category_id if created else link.category_id or settings.category_id
    link.pushed_at = datetime.now(timezone.utc)
    link.pushed_by_id = actor.id
    link.last_error = None
    db.merge(link)
    log_action(
        db,
        component="integrations",
        action="push_bitrix24_deal",
        result="success",
        details=f"Тендер {tender.external_id} → сделка {deal_id} ({'создана' if created else 'обновлена'})",
        user_id=actor.id,
    )
    db.commit()
    return {
        "deal_id": deal_id,
        "created": created,
        "url": deal_url(portal, deal_id),
        "message": f"Сделка {deal_id} {'создана' if created else 'обновлена'}.",
    }


# --- тестовая сделка --------------------------------------------------------------------------


def push_test_deal(
    db: Session, *, actor: User, tender_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Одна тестовая сделка — проверить на портале, как ложатся поля (08.10.2026).

    Собирается из настоящего тендера (указанного или последнего релевантного), чтобы проверка
    шла на живых данных, а не на выдуманных. От настоящей отправки отличается тремя вещами:
    название с пометкой «[ТЕСТ]»; `ORIGIN_ID` свой (`test:…`), поэтому сделка не считается
    сделкой тендера и не мешает будущей настоящей отправке; связь в `bitrix_deal_links` не
    пишется. Выключатель `push_enabled` её не блокирует — это разовое явное действие
    администратора, ради которого выключатель и держат выключенным, пока идёт проверка.
    """

    settings = _get_or_create(db)
    if tender_id is not None:
        tender = _tender(db, tender_id)
    else:
        # Показательный образец: прошедший отбор, с ценой и сроком подачи — иначе на портале
        # половина полей окажется пустой и проверять будет нечего. Если таких нет — любой.
        newest = (Tender.publish_date.desc().nullslast(), Tender.created_at.desc())
        tender = db.scalar(
            select(Tender)
            .where(
                Tender.passed_relevance_filter.is_(True),
                Tender.price.is_not(None),
                Tender.application_end.is_not(None),
            )
            .order_by(*newest)
        ) or db.scalar(select(Tender).order_by(*newest))
        if tender is None:
            raise LookupError("В базе нет ни одного тендера — тестовую сделку не из чего собрать.")

    stamp = datetime.now(timezone.utc)
    fields = build_deal_fields(db, tender)
    fields["TITLE"] = f"[ТЕСТ] {fields['TITLE']}"[:250]
    fields["ORIGIN_ID"] = f"test:{tender.id}:{stamp:%Y%m%d%H%M%S}"
    fields["COMMENTS"] = (
        "Тестовая сделка Sova Scanner для проверки интеграции — можно удалить.\n\n"
        + str(fields.get("COMMENTS") or "")
    ).strip()

    try:
        with _client(db) as client:
            deal_id = int(
                client.call(
                    "crm.deal.add",
                    {
                        "fields": {
                            "CATEGORY_ID": settings.category_id,
                            "STAGE_ID": settings.stage_id,
                            **fields,
                        },
                        "params": {"REGISTER_SONET_EVENT": "N"},
                    },
                )
            )
            portal = client.portal
    except BitrixError as exc:
        log_action(
            db,
            component="integrations",
            action="push_bitrix24_test_deal",
            result="error",
            level=LogLevel.WARNING,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        raise

    log_action(
        db,
        component="integrations",
        action="push_bitrix24_test_deal",
        result="success",
        details=f"Тестовая сделка {deal_id} из тендера {tender.external_id}",
        user_id=actor.id,
    )
    db.commit()
    return {
        "deal_id": deal_id,
        "created": True,
        "url": deal_url(portal, deal_id),
        "message": f"Тестовая сделка {deal_id} создана из тендера {tender.external_id}.",
    }
