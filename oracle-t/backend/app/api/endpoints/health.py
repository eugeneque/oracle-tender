from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        database_status = "ok"
        http_status = 200
    except Exception:
        database_status = "unavailable"
        http_status = 503

    return JSONResponse(
        status_code=http_status,
        content={"status": "ok" if database_status == "ok" else "degraded", "database": database_status},
    )
