"""Адаптер «Селдон» — Seldon.API сервиса поиска тендеров Seldon (решение 30.09.2026).

Отдельный канал сбора, как Госплан и Тендерплан (`SourceType.SELDON`).

**Почему сбора пока нет.** В отличие от Госплана и Тендерплана, у Seldon.API нет
открытой документации: адрес, Swagger и ключ выдаёт сопровождающий специалист после
договора (tender.myseldon.com/news/item/416559, телефон 8-800-2000-100; проверено
30.09.2026 — `api.myseldon.com` отдаёт только лендинг Seldon.Basis, `/swagger` — 404).
Поэтому канал заведён целиком — источник, переключатель на странице тендеров, вкладка
настроек с ключом, — а опрос честно сообщает, чего не хватает. Источник помечен
`adapter_status=not_implemented` и `status=pending_access` (плановый опрос его пропускает);
когда будет доступ, здесь пишется разбор выдачи по образцу `gosplan.py`, а статусы
меняются миграцией.

Ключ, как у остальных каналов, — учётка источника «Селдон» (пароль = ключ, логин —
логин Seldon, если API его потребует).
"""

from __future__ import annotations

from datetime import datetime

from app.adapters.base import (
    DocumentRef,
    PollError,
    PollOutcome,
    SourceAdapter,
    TenderDetails,
)

SOURCE_KEY = "seldon"

NOT_CONNECTED = (
    "Seldon.API не подключён: адрес API, документацию и ключ выдаёт сопровождающий "
    "специалист Seldon по договору (8-800-2000-100). Ключ сохраните в «Настройки → Селдон»"
)


class SeldonAdapter(SourceAdapter):
    source_key = SOURCE_KEY

    def __init__(self, *, api_key: str | None = None, login: str | None = None) -> None:
        self.api_key = api_key
        self.login = login

    def use_credentials(self, credentials) -> None:
        for credential in credentials:
            if credential.password:
                self.api_key = credential.password.strip()
                self.login = (credential.username or "").strip() or None
                return

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        message = NOT_CONNECTED
        if self.api_key:
            message = (
                "Ключ Seldon сохранён, но разбор выдачи Seldon.API ещё не написан — нужна "
                "документация (Swagger), которую Seldon выдаёт вместе с доступом"
            )
        outcome.errors.append(PollError(None, message))
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        from app.adapters.eis import EisAdapter

        return EisAdapter().get_tender_details(external_id)

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        from app.adapters.eis import EisAdapter

        return EisAdapter().download_documents(external_id, source_url)
