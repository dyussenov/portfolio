FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_DEBUG=0 \
    DJANGO_DB_PATH=/data/db/db.sqlite3 \
    DJANGO_MEDIA_ROOT=/data/media

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Статику собираем при сборке; ключ нужен только чтобы Django загрузил настройки
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --uid 1000 --create-home app \
    && mkdir -p /data/db /data/media \
    && chown -R app:app /data

USER app
VOLUME ["/data/db", "/data/media"]
EXPOSE 8000

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
