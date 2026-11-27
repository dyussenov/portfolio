from django.apps import AppConfig
from django.db.models.signals import post_migrate

# Тьютор видит учеников и скриншоты, но ничего не может в них изменить или удалить.
# Быстрые ссылки для урока он ведёт сам. В пробных занятиях заполняет только обратную связь по своим.
TUTOR_PERMISSIONS = (
    'view_student', 'view_screenshot', 'view_studygroup',
    'view_quicklink', 'add_quicklink', 'change_quicklink', 'delete_quicklink',
    'view_triallesson', 'change_triallesson',
)

# Менеджер записывает учеников на пробные и отправляет родителям обратную связь.
MANAGER_PERMISSIONS = (
    'view_triallesson', 'add_triallesson', 'change_triallesson', 'delete_triallesson',
)


def setup_role_groups(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    from .models import MANAGER_GROUP, TUTOR_GROUP

    for name, codenames in ((TUTOR_GROUP, TUTOR_PERMISSIONS), (MANAGER_GROUP, MANAGER_PERMISSIONS)):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(Permission.objects.filter(content_type__app_label='lessons', codename__in=codenames))


class LessonsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lessons'
    verbose_name = 'Уроки'

    def ready(self):
        # Права модели создаёт auth на post_migrate этого же приложения — наш обработчик подключён позже и видит их
        post_migrate.connect(setup_role_groups, sender=self)
