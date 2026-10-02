# Результаты уроков

Ученик в конце урока выбирает своё ФИО и вставляет скриншоты из буфера обмена (Ctrl+V).
Родители смотрят все результаты по личной ссылке `/s/<токен>/`.

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py import_students      # из students/students_*.csv
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

- `/` — страница для учеников
- `/s/<токен>/` — галерея ученика для родителей
- `/admin/` — админка

## Деплой через Docker

```bash
cp .env.example .env          # заполнить домен и DJANGO_SECRET_KEY
docker compose up -d --build
docker compose exec app python manage.py import_students
docker compose exec app python manage.py createsuperuser
```

Сгенерировать ключ: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.

Что внутри:
- `app` — Django + gunicorn. Миграции применяются при старте контейнера, статика собирается при сборке
  образа и раздаётся через whitenoise.
- `nginx` — принимает запросы на `HTTP_PORT` (по умолчанию 80), отдаёт `/media/` с диска и проксирует
  остальное в `app`. Лимит на загрузку — 500 МБ за запрос.
- Тома `db` (SQLite) и `media` (скриншоты) сохраняются между пересборками.
- Папка `./students` монтируется в контейнер, поэтому для обновлённых CSV достаточно заменить файлы
  и снова запустить `import_students` — пересобирать образ не нужно.

HTTPS: compose не получает сертификаты сам. Поставьте перед ним внешний прокси с TLS (Caddy,
Traefik, nginx на хосте с certbot или прокси хостинга) и задайте `DJANGO_SECURE_COOKIES=1`.
Внешний прокси должен передавать заголовок `X-Forwarded-Proto`.

Бэкап — это два тома:
```bash
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/db/db.sqlite3').backup(sqlite3.connect('/data/db/backup.sqlite3'))"
docker run --rm -v portfolio_db:/db -v portfolio_media:/media -v "$PWD":/out alpine \
  tar czf /out/backup-$(date +%F).tar.gz /db/backup.sqlite3 /media
```
(Имена томов начинаются с имени папки проекта, точные имена покажет `docker volume ls`.)

## Импорт учеников

`import_students` читает `students/students_<филиал>.csv` (колонки `ID`, `ФИО`).
Повторный запуск обновляет ФИО и филиал по `ID` и не меняет токены ссылок.
С `--deactivate-missing` ученики, которых больше нет в CSV, скрываются из списка на уроке
(их скриншоты и ссылки сохраняются).

## Админка

- **Ученики → ученик**: все скриншоты с превью, можно поменять дату или удалить (галочка «Удалить»).
  В блоке «Загрузка скриншотов» можно выбрать несколько файлов сразу и указать дату урока.
- **Ученики → действие «Выгрузить ссылки для родителей (CSV)»**: таблица ФИО + ссылка.
- **Скриншоты**: общий список с фильтрами по дате и филиалу и массовым удалением.

Файлы удаляются с диска вместе с записью.

## Настройки (переменные окружения)

| Переменная | Назначение |
|---|---|
| `DJANGO_SECRET_KEY` | секретный ключ (обязательно на проде) |
| `DJANGO_DEBUG` | `0` на проде |
| `DJANGO_ALLOWED_HOSTS` | через запятую |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | например `https://results.example.kz` |
| `PUBLIC_BASE_URL` | адрес сайта для ссылок родителям в админке и CSV |
| `DJANGO_SECURE_COOKIES` | `1` за HTTPS-прокси (secure-куки и доверие `X-Forwarded-Proto`) |
| `DJANGO_DB_PATH`, `DJANGO_MEDIA_ROOT` | где лежат база и скриншоты (в Docker — `/data/...`) |

## Переход на S3

Картинки сейчас лежат в `media/`. Для S3: `pip install django-storages[s3]` и в `config/settings.py`
заменить `STORAGES['default']` на `storages.backends.s3.S3Storage` с нужными `OPTIONS`.
Код приложения менять не нужно. Старые файлы из `media/screenshots/` нужно скопировать в бакет с теми же путями.
