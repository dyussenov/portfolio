import os

from .base import *  # noqa: F403

DEBUG = False
ALFACRM_WRITE_LINKS = True

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    raise RuntimeError('В .env не задан DJANGO_SECRET_KEY. Для локального запуска укажите DJANGO_ENV=local')

ALLOWED_HOSTS = [h for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',') if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o]

# За обратным прокси с HTTPS (nginx/traefik/caddy передают X-Forwarded-Proto)
if os.environ.get('DJANGO_SECURE_COOKIES', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
