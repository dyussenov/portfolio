from django.apps import AppConfig
from django.db.models.signals import post_migrate

# Тьютор видит учеников и скриншоты, но ничего не может в них изменить или удалить.
# Быстрые ссылки для урока он ведёт сам.
TUTOR_PERMISSIONS = (
    'view_student', 'view_screenshot',
    'view_quicklink', 'add_quicklink', 'change_quicklink', 'delete_quicklink',
)


def setup_tutor_group(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    from .models import TUTOR_GROUP

    group, _ = Group.objects.get_or_create(name=TUTOR_GROUP)
    group.permissions.set(Permission.objects.filter(content_type__app_label='lessons', codename__in=TUTOR_PERMISSIONS))


class LessonsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lessons'
    verbose_name = 'Уроки'

    def ready(self):
        # Права модели создаёт auth на post_migrate этого же приложения — наш обработчик подключён позже и видит их
        post_migrate.connect(setup_tutor_group, sender=self)
