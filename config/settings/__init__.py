import os
from pathlib import Path

from dotenv import load_dotenv

# Значения из окружения важнее файла: в Docker переменные задаёт compose, а .env в образ не попадает
load_dotenv(Path(__file__).resolve().parent.parent.parent / '.env')

DJANGO_ENV = os.environ.get('DJANGO_ENV', 'prod')

if DJANGO_ENV == 'local':
    from .local import *  # noqa: F403
elif DJANGO_ENV == 'prod':
    from .prod import *  # noqa: F403
else:
    raise RuntimeError(f"DJANGO_ENV должен быть 'local' или 'prod', а не {DJANGO_ENV!r}")
