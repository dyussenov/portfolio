import secrets
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User, UserManager
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone


def generate_token():
    return secrets.token_urlsafe(12)


def generate_pin():
    return f'{secrets.randbelow(10_000):04d}'


class Branch(models.Model):
    name = models.CharField('название', max_length=100, unique=True)
    slug = models.SlugField('код', unique=True, help_text='Совпадает с суффиксом CSV: students_<код>.csv')

    class Meta:
        verbose_name = 'филиал'
        verbose_name_plural = 'филиалы'
        ordering = ['name']

    def __str__(self):
        return self.name


class StudyGroup(models.Model):
    """Учебная группа из AlfaCRM. Создаётся и обновляется только синхронизацией."""
    external_id = models.PositiveIntegerField('ID в AlfaCRM', unique=True)
    name = models.CharField('название', max_length=255)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='groups', verbose_name='филиал')
    is_active = models.BooleanField('есть в CRM', default=True, help_text='Снимается, если группа пропала из AlfaCRM')

    class Meta:
        verbose_name = 'группа'
        verbose_name_plural = 'группы'
        ordering = ['branch__name', 'name']

    def __str__(self):
        return self.name


class Student(models.Model):
    external_id = models.PositiveIntegerField('ID из выгрузки', unique=True, null=True, blank=True)
    full_name = models.CharField('ФИО', max_length=255)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='students', verbose_name='филиал')
    groups = models.ManyToManyField(
        StudyGroup, related_name='students', blank=True, verbose_name='группы',
        help_text='Подтягиваются из AlfaCRM при синхронизации',
    )
    birth_date = models.DateField('дата рождения', null=True, blank=True)
    token = models.CharField('токен ссылки', max_length=32, unique=True, default=generate_token, editable=False)
    pin = models.CharField(
        'пин-код', max_length=4, default=generate_pin,
        validators=[RegexValidator(r'^\d{4}$', 'Пин-код — ровно 4 цифры.')],
        help_text='Ученик вводит его при отправке скриншотов',
    )
    is_active = models.BooleanField('активен', default=True, help_text='Неактивные не показываются в списке на уроке')

    class Meta:
        verbose_name = 'ученик'
        verbose_name_plural = 'ученики'
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('lessons:student_gallery', args=[self.token])


def generate_staff_pin():
    return f'{secrets.randbelow(100_000_000):08d}'


class StaffPin(models.Model):
    employee = models.CharField('сотрудник', max_length=255, help_text='Кому выдан код')
    code = models.CharField('код', max_length=8, unique=True, default=generate_staff_pin, editable=False)
    is_active = models.BooleanField('активен', default=True, help_text='Снимите галочку, чтобы код перестал работать')
    created_at = models.DateTimeField('создан', auto_now_add=True)

    class Meta:
        verbose_name = 'секретный пин-код'
        verbose_name_plural = 'секретные пин-коды'
        ordering = ['employee']

    def __str__(self):
        return self.employee


TUTOR_GROUP = 'Тьюторы'
MANAGER_GROUP = 'Менеджеры'


def is_tutor(user):
    return user.is_authenticated and not user.is_superuser and user.groups.filter(name=TUTOR_GROUP).exists()


class TutorManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(groups__name=TUTOR_GROUP)

    def find_by_password(self, raw_password):
        # Пароль тьютора работает как секретный пин-код при загрузке скриншотов
        if not raw_password:
            return None
        for tutor in self.filter(is_active=True):
            if tutor.check_password(raw_password):
                return tutor
        return None


class Tutor(User):
    """Сотрудник, который заходит в админку только посмотреть учеников и скриншоты."""
    objects = TutorManager()

    class Meta:
        proxy = True
        verbose_name = 'тьютор'
        verbose_name_plural = 'тьюторы'


class ManagerManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(groups__name=MANAGER_GROUP)


class Manager(User):
    """Менеджер: записывает учеников на пробные занятия и отправляет родителям обратную связь."""
    objects = ManagerManager()

    class Meta:
        proxy = True
        verbose_name = 'менеджер'
        verbose_name_plural = 'менеджеры'


FEEDBACK_FIELDS = ('topic', 'activities', 'recommended_group', 'makeup_hours')
MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def hours_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return 'час'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 'часа'
    return 'часов'


def makeup_label(value):
    """«2» → «нужно отработать 2 часа», «0» → «не нужны»; любой другой текст — как написал тьютор."""
    value = value.strip()
    number = value.replace(',', '.')
    try:
        hours = float(number)
    except ValueError:
        return value
    if hours <= 0:
        return 'не нужны'
    if hours.is_integer():
        return f'нужно отработать {int(hours)} {hours_word(int(hours))}'
    return f'нужно отработать {value} часа'


def age_on(birth_date, day):
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


class TrialLesson(models.Model):
    crm_url = models.CharField(
        'ссылка на ученика в AlfaCRM', max_length=500,
        help_text='Скопируйте адрес карточки ученика из AlfaCRM. Имя, возраст и телефон подтянутся сами.',
    )
    crm_customer_id = models.PositiveIntegerField('ID в AlfaCRM', editable=False)
    crm_branch_id = models.PositiveIntegerField('филиал в AlfaCRM', editable=False)
    full_name = models.CharField('ученик', max_length=255, editable=False)
    birth_date = models.DateField('дата рождения', null=True, blank=True, editable=False)
    age = models.PositiveSmallIntegerField('возраст', null=True, blank=True, editable=False, help_text='На дату пробного')
    parent_name = models.CharField('родитель', max_length=255, blank=True, editable=False)
    parent_phone = models.CharField('телефон родителя', max_length=50, blank=True, editable=False)
    lesson_date = models.DateField('дата пробного', db_index=True)
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='trial_lessons', verbose_name='ответственный тьютор',
        limit_choices_to={'groups__name': TUTOR_GROUP, 'is_active': True},
    )
    topic = models.CharField('тема', max_length=255, blank=True)
    activities = models.TextField('что делали', blank=True)
    recommended_group = models.CharField('рекомендованная группа', max_length=255, blank=True)
    makeup_hours = models.CharField('часов отработок', max_length=100, blank=True)
    feedback = models.TextField(
        'обратная связь', blank=True,
        help_text='Собирается из полей тьютора. Именно этот текст получит родитель.',
    )
    feedback_at = models.DateTimeField('ОС заполнена', null=True, blank=True, editable=False)
    sent_at = models.DateTimeField('отправлено родителю', null=True, blank=True, editable=False)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='отправил', editable=False,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='записал', editable=False,
    )
    created_at = models.DateTimeField('создано', auto_now_add=True)

    class Meta:
        verbose_name = 'пробное занятие'
        verbose_name_plural = 'пробные занятия'
        ordering = ['-lesson_date', 'full_name']

    def __str__(self):
        return f'{self.full_name} — {self.lesson_date:%d.%m.%Y}'

    @property
    def status(self):
        if self.sent_at:
            return 'отправлено родителю'
        if self.feedback_at:
            return 'ОС готова'
        if self.feedback.strip():
            return 'тьютор пишет ОС'
        return 'ждём ОС от тьютора'

    @property
    def first_name(self):
        # В AlfaCRM ученики записаны как «Фамилия Имя»
        parts = self.full_name.split()
        return parts[1] if len(parts) > 1 else (parts[0] if parts else '')

    def feedback_parts(self):
        return {name: (getattr(self, name) or '').strip() for name in FEEDBACK_FIELDS}

    def compose_feedback(self):
        """Текст для родителя из полей тьютора. *…* — жирный шрифт в WhatsApp."""
        parts = self.feedback_parts()
        if not any(parts.values()):
            return ''
        day = f'{self.lesson_date.day} {MONTHS_GENITIVE[self.lesson_date.month - 1]}'
        lines = [f'Здравствуйте! Обратная связь по пробному занятию {day} — {self.first_name}'.rstrip(' —')]
        sections = (
            ('📌', 'Тема', parts['topic']),
            ('🛠', 'Что делали', parts['activities']),
            ('🎯', 'Рекомендуем группу', parts['recommended_group']),
            ('⏱', 'Отработки', makeup_label(parts['makeup_hours'])),
        )
        for icon, title, value in sections:
            if value:
                lines += ['', f'{icon} *{title}*', value[0].upper() + value[1:]]
        return '\n'.join(lines)

    def apply_crm_data(self, data):
        for field in ('crm_customer_id', 'crm_branch_id', 'full_name', 'birth_date', 'parent_name', 'parent_phone'):
            setattr(self, field, data[field])
        self.age = age_on(self.birth_date, self.lesson_date) if self.birth_date and self.lesson_date else None


class QuickLinkQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(expires_at__gt=timezone.now())


class QuickLink(models.Model):
    title = models.CharField('название', max_length=120, blank=True, help_text='Например: «Тест по Scratch». Можно оставить пустым — покажем адрес.')
    url = models.URLField('ссылка', max_length=1000)
    expires_at = models.DateTimeField('показывать до', db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='quick_links',
        verbose_name='добавил', editable=False,
    )
    created_at = models.DateTimeField('добавлена', auto_now_add=True)

    objects = QuickLinkQuerySet.as_manager()

    class Meta:
        verbose_name = 'быстрая ссылка'
        verbose_name_plural = 'быстрые ссылки'
        ordering = ['-created_at']

    def __str__(self):
        return self.title or self.url

    @property
    def is_alive(self):
        return self.expires_at > timezone.now()


def screenshot_upload_to(instance, filename):
    ext = Path(filename).suffix.lower() or '.png'
    return f'screenshots/{instance.student.token}/{instance.lesson_date:%Y-%m-%d}/{uuid.uuid4().hex}{ext}'


class Screenshot(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='screenshots', verbose_name='ученик')
    lesson_date = models.DateField('дата урока', default=timezone.localdate, db_index=True)
    image = models.ImageField('скриншот', upload_to=screenshot_upload_to)
    created_at = models.DateTimeField('загружен', auto_now_add=True)
    uploaded_by = models.ForeignKey(
        StaffPin, on_delete=models.SET_NULL, null=True, blank=True, related_name='screenshots',
        verbose_name='загрузил сотрудник',
    )
    uploaded_by_tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_screenshots',
        verbose_name='загрузил тьютор',
    )

    class Meta:
        verbose_name = 'скриншот'
        verbose_name_plural = 'скриншоты'
        ordering = ['-lesson_date', 'created_at']

    def __str__(self):
        return f'{self.student} — {self.lesson_date:%d.%m.%Y}'


@receiver(post_delete, sender=Screenshot)
def delete_screenshot_file(sender, instance, **kwargs):
    # Срабатывает и при массовом удалении из админки, и при каскадном удалении ученика
    if instance.image:
        instance.image.delete(save=False)
