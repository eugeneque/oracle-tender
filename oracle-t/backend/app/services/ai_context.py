"""От чьего имени сейчас идёт обращение к модели (персональный выбор провайдера, 18.09.2026).

Выбор модели — у каждого пользователя свой (`users.ai_provider`), а `run_structured` зовут
из глубины сервисов, куда пользователь не передаётся: `_classify`, `_ask_model`,
`read_characteristics` знают только о тендере и тексте. Протаскивать `actor` через десяток
сигнатур ради одного поля — много шума, поэтому пользователь кладётся в контекст один раз
на входе: middleware (`app/middleware/ai_context.py`) — для HTTP-запросов, `run_job` — для
фоновых задач, у которых есть автор. `ai_provider_service.get_active_provider` читает его
отсюда.

Именно `ContextVar`, а не глобальная переменная: запросы обрабатываются параллельно в пуле
потоков, а контекст у каждого запроса и каждой задачи свой. Ставить значение нужно в
middleware, а не в зависимости FastAPI: sync-зависимость выполняется в отдельном потоке с
копией контекста, и её `set` до обработчика не доходит (проверено на Starlette 0.41).
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_current_user_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "ai_current_user_id", default=None
)


def current_user_id() -> uuid.UUID | None:
    return _current_user_id.get()


@contextmanager
def acting_as(user_id: uuid.UUID | None) -> Iterator[None]:
    """На время блока обращения к модели идут с настройками этого пользователя
    (`None` — системная модель по умолчанию, как у задач по расписанию)."""

    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)
