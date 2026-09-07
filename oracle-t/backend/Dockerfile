FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tesseract — OCR для сканов PDF (раздел 5.2 ТЗ).
# libarchive-tools даёт bsdtar, которым распаковываются RAR-архивы документации
# (app/services/document_extraction.py): на macOS он входит в систему, в slim-образе — нет,
# и без него RAR на сервере остался бы без текста.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright ставится вместе с образом (не отдельным шагом развёртывания) — часть источников
# из раздела 4.1 ТЗ отдаёт разметку только после выполнения JS и требует браузерного рендеринга.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
