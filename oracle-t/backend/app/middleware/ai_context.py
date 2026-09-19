from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.audit import _resolve_user_id
from app.services.ai_context import acting_as


class AiContextMiddleware(BaseHTTPMiddleware):
    """Кладёт пользователя из bearer-токена в контекст ИИ-модуля на время запроса — чтобы
    `run_structured` где угодно в глубине сервисов взял модель, выбранную этим пользователем
    (см. `app/services/ai_context.py`). Токен здесь только декодируется, без обращения к БД:
    проверка активности пользователя остаётся за `get_current_user`, а невалидный токен даёт
    пустой контекст, то есть модель по умолчанию, — и тут же 401 от зависимости."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        with acting_as(_resolve_user_id(request)):
            return await call_next(request)
