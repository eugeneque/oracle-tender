"""Автопоиск юридических данных компании по ЕГРЮЛ (раздел 7 ТЗ, `company_profile`).

Почему именно ЕГРЮЛ, а не общий веб-поиск: наименование, ОГРН, дата регистрации и
юридический адрес — это реестровые факты, и брать их из выдачи поисковика значит подставлять
в `*_evidence` источник, которому нельзя верить. Тот же принцип, что у `si_types.source` в
разделе 5.3: авторитетный реестр или ничего.

**Автопоиск не сохраняет.** Он возвращает кандидата, человек его подтверждает, и только после
подтверждения поле получает метку `verified_by_user` в `company_profile.field_sources`.
Молча записать в профиль результат внешнего сервиса — тот же класс ошибки, что
необъяснимая цифра в финансовом блоке (раздел 5.5.1 ТЗ).

Публичного документированного API у сервиса нет; используется тот же путь, которым ходит его
собственная веб-форма: POST с запросом → токен → GET результата по токену. Поэтому все сбои
здесь — ожидаемое состояние, а не авария: не нашли или сервис недоступен — говорим об этом
прямо и оставляем поля для ручного ввода.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry

SEARCH_URL = "https://egrul.nalog.ru/"
RESULT_URL = "https://egrul.nalog.ru/search-result/{token}"

# Источник значения поля профиля (`company_profile.field_sources`).
SOURCE_AUTO = "auto_search"
SOURCE_MANUAL = "manual"


class EgrulError(RuntimeError):
    """Поиск не дал результата или сервис недоступен — эндпоинт превращает в 4xx с текстом."""


@dataclass
class EgrulCompany:
    """Кандидат из реестра. Ничего не сохраняет — только показывается человеку на подтверждение."""

    legal_name: str | None
    inn: str | None
    ogrn: str | None
    registration_date: date | None
    legal_address: str | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _valid_query(query: str) -> str:
    """ИНН/ОГРН — только цифры; название — как есть.

    Отдельная проверка нужна потому, что чаще всего сюда прилетает ИНН с пробелами из
    буфера обмена, и запрос «7 7 0 1…» реестр просто не найдёт.
    """

    cleaned = query.strip()
    if not cleaned:
        raise EgrulError("Пустой запрос: укажите ИНН, ОГРН или наименование организации.")
    digits = re.sub(r"\D", "", cleaned)
    if digits and len(digits) >= 10 and len(digits) == len(re.sub(r"\s", "", cleaned)):
        return digits
    return cleaned


def search(query: str) -> list[EgrulCompany]:
    """Ищет организацию в ЕГРЮЛ. Возвращает кандидатов, не сохраняя ничего в профиль."""

    prepared = _valid_query(query)
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        ) as client:
            response = fetch_with_retry(
                client,
                "POST",
                SEARCH_URL,
                data={
                    "vyp3CaptchaToken": "",
                    "page": "",
                    "query": prepared,
                    "region": "",
                    "PreparedQuery": "",
                },
                max_attempts=2,
            )
            if response.status_code >= 400:
                raise EgrulError(
                    f"Сервис ЕГРЮЛ ответил HTTP {response.status_code}. Заполните юридические "
                    "данные вручную."
                )
            token = (response.json() or {}).get("t")
            if not token:
                raise EgrulError(
                    "Сервис ЕГРЮЛ не выдал ключ поиска — вероятно, включена защита от "
                    "автоматических запросов. Заполните юридические данные вручную."
                )

            result = fetch_with_retry(
                client, "GET", RESULT_URL.format(token=token), max_attempts=3
            )
            if result.status_code >= 400:
                raise EgrulError(
                    f"Сервис ЕГРЮЛ ответил HTTP {result.status_code} на запрос результата."
                )
            rows = (result.json() or {}).get("rows") or []
    except EgrulError:
        raise
    except httpx.HTTPError as exc:
        logger.warning(f"Автопоиск по ЕГРЮЛ не удался: {exc}")
        raise EgrulError(
            f"Не удалось обратиться к сервису ЕГРЮЛ: {exc}. Заполните юридические данные "
            "вручную."
        ) from exc
    except ValueError as exc:
        raise EgrulError(
            "Сервис ЕГРЮЛ вернул неожиданный ответ. Заполните юридические данные вручную."
        ) from exc

    # Однобуквенные ключи — формат самого сервиса: `n` наименование, `i` ИНН, `o` ОГРН,
    # `a` адрес, `r` дата регистрации.
    companies = [
        EgrulCompany(
            legal_name=row.get("n") or row.get("c"),
            inn=row.get("i"),
            ogrn=row.get("o"),
            registration_date=_parse_date(row.get("r")),
            legal_address=row.get("a"),
        )
        for row in rows
        if isinstance(row, dict)
    ]
    if not companies:
        raise EgrulError(
            f"В ЕГРЮЛ ничего не найдено по запросу «{query}». Проверьте ИНН или введите "
            "данные вручную."
        )
    return companies[:10]
