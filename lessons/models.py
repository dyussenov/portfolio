import secrets
import uuid
from pathlib import Path

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone


def generate_token():
    return secrets.token_urlsafe(12)


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
    token = models.CharField('токен ссылки', max_length=32, unique=True, default=generate_token, editable=False)
    is_active = models.BooleanField('активен', default=True, help_text='Неактивные не показываются в списке на уроке')

    class Meta:
        verbose_name = 'ученик'
        verbose_name_plural = 'ученики'
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('lessons:student_gallery', args=[self.token])


def screenshot_upload_to(instance, filename):
    ext = Path(filename).suffix.lower() or '.png'
    return f'screenshots/{instance.student.token}/{instance.lesson_date:%Y-%m-%d}/{uuid.uuid4().hex}{ext}'


class Screenshot(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='screenshots', verbose_name='ученик')
    lesson_date = models.DateField('дата урока', default=timezone.localdate, db_index=True)
    image = models.ImageField('скриншот', upload_to=screenshot_upload_to)
    created_at = models.DateTimeField('загружен', auto_now_add=True)

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
