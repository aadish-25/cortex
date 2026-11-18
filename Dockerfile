FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install project dependencies from pyproject.toml
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

COPY backend ./backend
COPY data ./data

CMD ["celery", "-A", "backend.worker.celery_app", "worker", "--loglevel=info"]
