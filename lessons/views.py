import datetime
import heapq
from itertools import groupby

from django import forms
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Branch, Screenshot, StaffPin, Student

MAX_FILES_PER_UPLOAD = 30
MAX_FILE_SIZE = 15 * 1024 * 1024
MAX_PIN_ATTEMPTS = 10
PIN_LOCK_SECONDS = 15 * 60
UPCOMING_BIRTHDAYS = 5


def birthday_in_year(birth_date, year):
    try:
        return birth_date.replace(year=year)
    except ValueError:  # 29 февраля в невисокосный год
        return datetime.date(year, 2, 28)


def next_birthday(birth_date, today):
    for year in (today.year, today.year + 1):
        birthday = birthday_in_year(birth_date, year)
        if birthday >= today:
            return birthday


def short_name(full_name):
    return ' '.join(full_name.split()[:2])


def students_with_birth_date():
    return Student.objects.filter(is_active=True, birth_date__isnull=False).values_list('full_name', 'birth_date')


def upcoming_birthdays(today, limit=UPCOMING_BIRTHDAYS):
    upcoming = ((next_birthday(birth_date, today), full_name) for full_name, birth_date in students_with_birth_date())
    return [
        {'full_name': full_name, 'short_name': short_name(full_name), 'date': date, 'is_today': date == today}
        for date, full_name in heapq.nsmallest(limit, upcoming)
    ]


def past_birthdays_this_week(today):
    monday = today - datetime.timedelta(days=today.weekday())
    past = [
        (birthday, full_name)
        for full_name, birth_date in students_with_birth_date()
        for birthday in {birthday_in_year(birth_date, year) for year in (monday.year, today.year)}
        if monday <= birthday < today
    ]
    return [
        {'full_name': full_name, 'short_name': short_name(full_name), 'date': date}
        for date, full_name in sorted(past, reverse=True)
    ]


def lesson_upload(request):
    branches = Branch.objects.order_by('name')
    students = Student.objects.filter(is_active=True).order_by('full_name').values('id', 'full_name', 'branch_id')
    today = timezone.localdate()
    return render(request, 'lessons/upload.html', {
        'branches': branches,
        'students': list(students),
        'max_files': MAX_FILES_PER_UPLOAD,
        'upcoming_birthdays': upcoming_birthdays(today),
        'past_birthdays': past_birthdays_this_week(today),
    })


@require_POST
def upload_screenshots(request):
    student = Student.objects.filter(pk=request.POST.get('student'), is_active=True).first()
    if student is None:
        return JsonResponse({'error': 'Выбери своё ФИО из списка.'}, status=400)

    # Пин-код всего 4 цифры — после серии ошибок временно блокируем ученика, чтобы его нельзя было подобрать
    attempts_key = f'pin-attempts:{student.pk}'
    attempts = cache.get(attempts_key, 0)
    if attempts >= MAX_PIN_ATTEMPTS:
        return JsonResponse({'error': 'Слишком много неверных попыток. Попробуй через 15 минут или позови преподавателя.'}, status=429)
    pin = request.POST.get('pin', '').strip()
    staff_pin = None
    if pin != student.pin:
        staff_pin = StaffPin.objects.filter(code=pin, is_active=True).first()
        if staff_pin is None:
            cache.set(attempts_key, attempts + 1, PIN_LOCK_SECONDS)
            return JsonResponse({'error': 'Неверный пин-код.', 'field': 'pin'}, status=403)
    cache.delete(attempts_key)

    files = request.FILES.getlist('images')
    if not files:
        return JsonResponse({'error': 'Нет скриншотов для отправки.'}, status=400)
    if len(files) > MAX_FILES_PER_UPLOAD:
        return JsonResponse({'error': f'Можно отправить не больше {MAX_FILES_PER_UPLOAD} скриншотов за раз.'}, status=400)

    field = forms.ImageField()
    for f in files:
        if f.size > MAX_FILE_SIZE:
            return JsonResponse({'error': f'Файл «{f.name}» больше 15 МБ.'}, status=400)
        try:
            field.clean(f)
        except ValidationError:
            return JsonResponse({'error': f'Файл «{f.name}» не похож на картинку.'}, status=400)

    with transaction.atomic():
        for f in files:
            Screenshot.objects.create(student=student, image=f, uploaded_by=staff_pin)

    return JsonResponse({'saved': len(files), 'student': student.full_name})


def student_gallery(request, token):
    student = get_object_or_404(Student.objects.select_related('branch'), token=token)
    screenshots = student.screenshots.order_by('-lesson_date', 'created_at')
    lessons = [(date, list(items)) for date, items in groupby(screenshots, key=lambda s: s.lesson_date)]
    return render(request, 'lessons/gallery.html', {'student': student, 'lessons': lessons})
