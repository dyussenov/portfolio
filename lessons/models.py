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


class Student(models.Model):
    external_id = models.PositiveIntegerField('ID из выгрузки', unique=True, null=True, blank=True)
    full_name = models.CharField('ФИО', max_length=255)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='students', verbose_name='филиал')
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
