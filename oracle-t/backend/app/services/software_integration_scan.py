"""Разбор сканов писем разработчиков ПО (05.11.2026): текст → поля и функции для черновика.

Письма разработчиков («Сообщаем, что в ПК „яЭнергетик“ реализована поддержка приборов…» с
таблицей функций, протокол испытаний Россетей) — официальные ответы, на которых специалист
строит таблицу интеграций. Платформа читает такой скан сама:

1. **Текст.** PDF — текстовый слой, а при его отсутствии — OCR страниц (tesseract, как и для
   документации закупок: `document_extraction.extract_pdf_text`); изображение — OCR целиком.
   Моделей с «зрением» среди подключённых нет, поэтому качество определяется распознаванием:
   нечитаемый скан получает понятный отказ, а не выдумку.
2. **Разбор моделью** (`run_structured`): протоколы, версия ПО, способ идентификации, модели
   и таблицы функций «функция — соответствие — комментарий».
3. **В черновик как «авто».** Предложения пишутся только в пустые поля (заполненное
   специалистом не перетирается — расхождения остаются в результате разбора и
   показываются рядом), функции добавляются с `verified=False`. В анализ тендеров они
   не попадут, пока специалист не подтвердит и не нажмёт «Сохранить».
"""

from __future__ import annotations

import io
import uuid

import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezones import now_utc
from app.models.software_integration import SoftwareIntegration, SoftwareIntegrationDocument
from app.services import software_integration_service as service
from app.services.ai_client import run_structured

MAX_SCAN_CHARS = 30_000

_SYSTEM_PROMPT = """Ты читаешь официальное письмо или протокол испытаний разработчика ПО \
верхнего уровня (АСКУЭ) о поддержке приборов учёта электроэнергии производства ООО «МИРТЕК». \
Текст получен распознаванием скана и может содержать опечатки и склеенные строки.

Верни только то, что прямо написано в документе. Не додумывай: чего в документе нет — пустая \
строка или пустой список.

Поля:
- protocol_mirtek — поддерживается ли фирменный протокол МИРТЕК (для каких моделей, с какой версии);
- protocol_spodes — поддерживается ли протокол СПОДЭС: версия (V3/V4…), для каких моделей, объём интеграции;
- identification — как приборы идентифицируются в ПО (по наименованию типа, по ID платы, по заводскому номеру…);
- integration_protocols — названия протоколов интеграции или писем об объёме интеграции, на которые ссылается документ;
- software_version — версия или год версии ПО, с которой поддержка реализована;
- models — обозначения моделей МИРТЕК, названные в документе (например «МИРТЕК-32-РУ»);
- functions — строки таблиц «перечень реализованных функций»: model — модель или группа моделей, к которой \
относится таблица; protocol — «СПОДЭС» или «МИРТЕК» (если указан протокол обмена), иначе пусто; name — функция; \
status — yes (реализована, «+»), partial (частично) или no (не реализована, «-»); comment — комментарий из таблицы.
- summary — одно предложение по-русски: о чём документ и от кого он.
"""


class ScanFunction(pydantic.BaseModel):
    model: str
    protocol: str
    name: str
    status: str
    comment: str


class ScanReading(pydantic.BaseModel):
    protocol_mirtek: str
    protocol_spodes: str
    identification: str
    integration_protocols: str
    software_version: str
    models: list[str]
    functions: list[ScanFunction]
    summary: str


_FIELD_KEYS = ("protocol_mirtek", "protocol_spodes", "identification", "integration_protocols", "software_version")


def extract_text(file_name: str, content: bytes) -> str:
    """Текст скана: текстовый слой PDF с OCR-подстраховкой либо OCR изображения."""

    if file_name.lower().endswith(".pdf"):
        from app.services.document_extraction import extract_pdf_text

        return extract_pdf_text(content) or ""
    settings = get_settings()
    if not settings.ocr_enabled:
        return ""
    try:
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(content))
        return pytesseract.image_to_string(image, lang=settings.ocr_languages).strip()
    except Exception as exc:  # noqa: BLE001 - OCR недоступен или изображение битое
        logger.warning(f"OCR скана {file_name} не удался: {exc}")
        return ""


def _verdict_for(text: str) -> str | None:
    lowered = text.lower()
    if not lowered.strip():
        return None
    if any(word in lowered for word in ("не поддерж", "нет", "отсутств")):
        return "no" if lowered.strip().rstrip(".") in ("нет", "не поддерживается") else "partial"
    return "yes"


def apply_reading(item: SoftwareIntegration, document: SoftwareIntegrationDocument, reading: ScanReading) -> dict:
    """Добавляет предложения в черновик; возвращает, что применено и что разошлось."""

    now = now_utc().isoformat()
    draft = {
        "fields": {k: dict(v) for k, v in ((item.draft or {}).get("fields") or {}).items()},
        "models": list((item.draft or {}).get("models") or []),
        "functions": [dict(f) for f in (item.draft or {}).get("functions") or []],
    }
    for key in service.FIELDS:
        draft["fields"].setdefault(key, service._empty_field())
    applied: list[str] = []
    conflicts: list[dict] = []
    for key in _FIELD_KEYS:
        suggested = (getattr(reading, key) or "").strip()
        if not suggested:
            continue
        current = draft["fields"][key]
        if current.get("value") or current.get("verdict"):
            if suggested.lower() not in (current.get("value") or "").lower():
                conflicts.append({"field": key, "current": current.get("value"), "from_scan": suggested})
            continue
        with_verdict = service.FIELDS[key][1]
        draft["fields"][key] = {
            "value": suggested[:4000],
            "verdict": _verdict_for(suggested) if with_verdict else None,
            "status": "auto",
            "source": "scan",
            "updated_at": now,
            "updated_by": f"скан «{document.file_name}»",
            "document_id": str(document.id),
        }
        applied.append(key)

    known_models = {m.lower() for m in draft["models"]}
    for model in service._clean_models(reading.models):
        if model.lower() not in known_models:
            draft["models"].append(model)
            known_models.add(model.lower())

    seen = {(f.get("model", "").lower(), f.get("protocol", "").lower(), f.get("name", "").lower()) for f in draft["functions"]}
    added = 0
    for func in reading.functions:
        cleaned = service._clean_function(
            {
                "model": func.model, "protocol": func.protocol, "name": func.name,
                "status": func.status if func.status in service.FUNCTION_STATUSES else "yes",
                "comment": func.comment, "verified": False, "source": "scan", "document_id": str(document.id),
            },
            None,
        )
        if cleaned is None:
            continue
        key3 = (cleaned["model"].lower(), cleaned["protocol"].lower(), cleaned["name"].lower())
        if key3 in seen:
            continue
        seen.add(key3)
        draft["functions"].append(cleaned)
        added += 1
    item.draft = draft
    item.draft_updated_at = now_utc()
    return {"fields_filled": applied, "functions_added": added, "conflicts": conflicts}


def analyze_document(db: Session, document_id: uuid.UUID) -> None:
    """Разбор одного скана; состояние пишется в сам документ — интерфейс опрашивает список."""

    document = db.get(SoftwareIntegrationDocument, document_id)
    if document is None:
        return
    item = db.get(SoftwareIntegration, document.integration_id)
    document.analysis_status, document.analysis_error = "pending", None
    db.commit()
    try:
        text = extract_text(document.file_name, document.content)[:MAX_SCAN_CHARS]
        if len(text.strip()) < 40:
            raise ValueError(
                "Текст в скане не распознан (нет текстового слоя, OCR недоступен или скан нечитаем). "
                "Внесите данные вручную либо загрузите скан лучшего качества."
            )
        reading = run_structured(
            db,
            system_prompt=_SYSTEM_PROMPT,
            user_text=f"Программное обеспечение: {item.name}\n\nТекст документа:\n{text}",
            response_model=ScanReading,
        )
        outcome = apply_reading(item, document, reading)
        document.analysis_result = {"reading": reading.model_dump(), **outcome}
        document.analysis_status, document.analysis_error = "done", None
    except Exception as exc:  # noqa: BLE001 - любой сбой — понятный отказ в карточке документа
        db.rollback()
        document = db.get(SoftwareIntegrationDocument, document_id)
        document.analysis_status = "failed"
        document.analysis_error = str(exc)[:500]
        logger.warning(f"Разбор скана {document.file_name} не удался: {exc}")
    document.analyzed_at = now_utc()
    db.commit()


def analyze_in_background(document_id: uuid.UUID) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        analyze_document(db, document_id)
    finally:
        db.close()
