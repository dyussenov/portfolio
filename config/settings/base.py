import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Адрес сайта для ссылок родителям, например https://results.example.kz
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '')

# Интеграция с AlfaCRM (команда sync_alfacrm)
ALFACRM_HOST = os.environ.get('ALFACRM_HOST', '')
ALFACRM_EMAIL = os.environ.get('ALFACRM_EMAIL', '')
ALFACRM_API_KEY = os.environ.get('ALFACRM_API_KEY', '')
ALFACRM_LINK_FIELD = os.environ.get('ALFACRM_LINK_FIELD', '')
# CRM одна на все окружения, поэтому ссылки на портфолио в неё пишет только прод
ALFACRM_WRITE_LINKS = False

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'import_export',
    'lessons',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('DJANGO_DB_PATH', BASE_DIR / 'db.sqlite3'),
        'OPTIONS': {
            # WAL + ожидание блокировки: несколько учеников могут отправлять скриншоты одновременно
            'init_command': 'PRAGMA journal_mode=WAL;',
            'transaction_mode': 'IMMEDIATE',
            'timeout': 20,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Картинки пока лежат в папке проекта. Для S3 достаточно поставить django-storages
# и заменить бэкенд "default" на storages.backends.s3.S3Storage.
MEDIA_URL = 'media/'
MEDIA_ROOT = Path(os.environ.get('DJANGO_MEDIA_ROOT', BASE_DIR / 'media'))
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

# Папка с CSV-выгрузками учеников (students_<филиал>.csv)
STUDENTS_CSV_DIR = BASE_DIR / 'students'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
