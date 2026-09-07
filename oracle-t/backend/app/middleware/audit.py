import uuid

import jwt
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models.log import LogLevel
from app.services.audit import log_action

# Действия, которые не имеют смысла журналировать на уровне HTTP-middleware, так как
# они не меняют состояние системы (или, как /auth/login, уже журналируются на уровне
# эндпоинта с более содержательным сообщением при неудаче).
_SKIP_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")


def _resolve_user_id(request: Request) -> uuid.UUID | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    try:
        payload = decode_access_token(token)
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Пишет в таблицу `logs` каждое не-GET обращение к API (раздел 3, 5.9 ТЗ:
    "любое действие пользователя в системе... обязательно пишется в лог")."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        if request.method == "GET" or request.url.path.startswith(_SKIP_PREFIXES):
            return response

        try:
            user_id = _resolve_user_id(request)
            level = LogLevel.INFO if response.status_code < 400 else LogLevel.ERROR
            db = SessionLocal()
            try:
                log_action(
                    db,
                    component="http",
                    action=f"{request.method} {request.url.path}",
                    result=str(response.status_code),
                    level=level,
                    user_id=user_id,
                )
                db.commit()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 - логирование не должно ронять запрос
            logger.warning(f"Не удалось записать аудит-лог запроса: {exc}")

        return response
