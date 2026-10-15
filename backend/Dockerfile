FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
# 1) create/upgrade tables  2) put model v1 in production if no model is in production yet  3) start the API
# (docker-compose and Render also run scripts/seed.py --if-empty; the image itself has no data/ folder)
CMD ["sh", "-c", "alembic upgrade head && python scripts/register_model.py ml_artifacts/model_v1_report.json --if-no-production && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
