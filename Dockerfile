# Сборка для Railway (стабильнее nixpacks с LibreOffice)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-common \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app_objects.py auth.py database.py estimate_module.py extensions.py well_passport.py price_sync.py gunicorn.conf.py ./
COPY templates/ templates/
COPY static/ static/

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app_objects:app"]
