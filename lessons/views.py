from itertools import groupby

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .models import Branch, Screenshot, Student

MAX_FILES_PER_UPLOAD = 30
MAX_FILE_SIZE = 15 * 1024 * 1024


def lesson_upload(request):
    branches = Branch.objects.order_by('name')
    students = Student.objects.filter(is_active=True).order_by('full_name').values('id', 'full_name', 'branch_id')
    return render(request, 'lessons/upload.html', {
        'branches': branches,
        'students': list(students),
        'max_files': MAX_FILES_PER_UPLOAD,
    })


@require_POST
def upload_screenshots(request):
    student = Student.objects.filter(pk=request.POST.get('student'), is_active=True).first()
    if student is None:
        return JsonResponse({'error': 'Выбери своё ФИО из списка.'}, status=400)

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
            Screenshot.objects.create(student=student, image=f)

    return JsonResponse({'saved': len(files), 'student': student.full_name})


def student_gallery(request, token):
    student = get_object_or_404(Student.objects.select_related('branch'), token=token)
    screenshots = student.screenshots.order_by('-lesson_date', 'created_at')
    lessons = [(date, list(items)) for date, items in groupby(screenshots, key=lambda s: s.lesson_date)]
    return render(request, 'lessons/gallery.html', {'student': student, 'lessons': lessons})
