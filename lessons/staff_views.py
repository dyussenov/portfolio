"""Кабинет сотрудника: менеджер записывает на пробные и отправляет ОС, тьютор пишет ОС."""
import datetime
import json
from functools import wraps
from itertools import groupby

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import FEEDBACK_FIELDS, StudyGroup, TrialLesson, Tutor, age_on, is_tutor
from .trials import TrialLookupError, fetch_trial_student, whatsapp_url

MAX_TRIALS_PER_SUBMIT = 50
FEEDBACK_TEXT_LIMIT = 3000
FEEDBACK_LABELS = {name: TrialLesson._meta.get_field(name).verbose_name.capitalize() for name in FEEDBACK_FIELDS}
SENT_HISTORY_DAYS = 14


def is_manager(user):
    return user.has_perm('lessons.add_triallesson')


def can_write_feedback(user):
    return user.is_superuser or is_tutor(user)


def role_required(check):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def error(message, status=400, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


def age_label(age):
    if age is None:
        return ''
    if age % 10 == 1 and age % 100 != 11:
        word = 'год'
    elif 2 <= age % 10 <= 4 and not 12 <= age % 100 <= 14:
        word = 'года'
    else:
        word = 'лет'
    return f'{age} {word}'


def tutor_name(user):
    return user.get_full_name() or user.username


def parse_date(value):
    try:
        return datetime.date.fromisoformat(value or '')
    except ValueError:
        return None


def nearest_weekend(today):
    # Пробные обычно в выходные: по умолчанию ближайшая суббота (в воскресенье — сегодня)
    if today.weekday() >= 5:
        return today
    return today + datetime.timedelta(days=5 - today.weekday())


class StaffLoginView(auth_views.LoginView):
    template_name = 'lessons/staff/login.html'
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return reverse('lessons:staff_home')


@login_required
def staff_home(request):
    user = request.user
    if is_manager(user):
        return redirect('lessons:staff_trials')
    if is_tutor(user):
        return redirect('lessons:staff_feedback')
    if user.is_staff:
        return redirect('admin:index')
    raise PermissionDenied


# ---------- менеджер ----------

def trial_dict(trial):
    return {
        'id': trial.pk,
        'full_name': trial.full_name,
        'age': age_label(trial.age),
        'lesson_date': trial.lesson_date.isoformat(),
        'tutor': trial.tutor_id,
        'status': trial.status,
    }


@role_required(is_manager)
def trials_page(request):
    today = timezone.localdate()
    trials = TrialLesson.objects.select_related('tutor', 'sent_by')
    ready = list(trials.filter(feedback_at__isnull=False, sent_at__isnull=True).order_by('lesson_date', 'full_name'))
    for trial in ready:
        trial.whatsapp = whatsapp_url(trial.parent_phone, trial.feedback)
    waiting = trials.filter(feedback_at__isnull=True).filter(lesson_date__gte=today - datetime.timedelta(days=30))
    waiting = waiting.order_by('lesson_date', 'full_name')
    sent = trials.filter(sent_at__gte=timezone.now() - datetime.timedelta(days=SENT_HISTORY_DAYS)).order_by('-sent_at')
    tutors = Tutor.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    return render(request, 'lessons/staff/trials.html', {
        'ready': ready,
        'waiting_days': [(day, list(items)) for day, items in groupby(waiting, key=lambda t: t.lesson_date)],
        'sent': sent,
        'today': today,
        'tutors': [{'id': t.pk, 'name': tutor_name(t)} for t in tutors],
        'default_date': nearest_weekend(today),
        'quick_dates': quick_dates(today),
    })


def quick_dates(today):
    names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    days = [today + datetime.timedelta(days=i) for i in range(8)]
    labels = []
    for day in days:
        if day == today:
            label = 'Сегодня'
        elif day == today + datetime.timedelta(days=1):
            label = 'Завтра'
        else:
            label = names[day.weekday()]
        labels.append({'value': day.isoformat(), 'label': label, 'day': f'{day:%d.%m}', 'weekend': day.weekday() >= 5})
    return labels


def lookup_payload(data, lesson_date=None):
    previous = TrialLesson.objects.filter(crm_customer_id=data['crm_customer_id']).order_by('-lesson_date')
    age = age_on(data['birth_date'], lesson_date or timezone.localdate()) if data['birth_date'] else None
    return {
        'customer_id': data['crm_customer_id'],
        'full_name': data['full_name'],
        'age': age_label(age),
        'parent_name': data['parent_name'],
        'parent_phone': data['parent_phone'],
        'previous': [{'date': t.lesson_date.isoformat(), 'label': f'{t.lesson_date:%d.%m.%Y}'} for t in previous[:3]],
    }


@require_POST
@role_required(is_manager)
def trial_lookup(request):
    try:
        data = fetch_trial_student(json_body(request).get('link', ''))
    except TrialLookupError as e:
        return error(str(e))
    return JsonResponse(lookup_payload(data))


@require_POST
@role_required(is_manager)
def trials_create(request):
    rows = json_body(request).get('rows')
    if not isinstance(rows, list) or not rows:
        return error('Добавьте хотя бы одного ученика.')
    if len(rows) > MAX_TRIALS_PER_SUBMIT:
        return error(f'За раз можно записать не больше {MAX_TRIALS_PER_SUBMIT} учеников.')

    tutors = {t.pk: t for t in Tutor.objects.filter(is_active=True)}
    prepared, errors, seen = [], {}, set()
    for index, row in enumerate(rows):
        row = row if isinstance(row, dict) else {}
        lesson_date = parse_date(row.get('date'))
        tutor = tutors.get(row.get('tutor')) if isinstance(row.get('tutor'), int) else None
        if lesson_date is None:
            errors[index] = 'Укажите дату.'
            continue
        if tutor is None:
            errors[index] = 'Выберите тьютора.'
            continue
        try:
            data = fetch_trial_student(row.get('link', ''))
        except TrialLookupError as e:
            errors[index] = str(e)
            continue
        key = (data['crm_customer_id'], lesson_date)
        if key in seen or TrialLesson.objects.filter(crm_customer_id=key[0], lesson_date=lesson_date).exists():
            errors[index] = f'{data["full_name"]} уже записан на {lesson_date:%d.%m.%Y}.'
            continue
        seen.add(key)
        prepared.append((row.get('link', '').strip(), lesson_date, tutor, data))

    if errors:
        # Записываем всё или ничего, чтобы после исправления ошибок не появились дубли
        return error('Исправьте отмеченные строки — пока никто не записан.', errors=errors)

    with transaction.atomic():
        for link, lesson_date, tutor, data in prepared:
            trial = TrialLesson(crm_url=link, lesson_date=lesson_date, tutor=tutor, created_by=request.user)
            trial.apply_crm_data(data)
            trial.save()
    messages.success(request, f'Записано на пробные: {len(prepared)}')
    return JsonResponse({'created': len(prepared)})


@require_POST
@role_required(is_manager)
def trial_update(request, pk):
    trial = get_object_or_404(TrialLesson, pk=pk)
    body = json_body(request)
    if 'date' in body:
        lesson_date = parse_date(body['date'])
        if lesson_date is None:
            return error('Неверная дата.')
        trial.lesson_date = lesson_date
        trial.age = age_on(trial.birth_date, lesson_date) if trial.birth_date else None
    if 'tutor' in body:
        tutor = Tutor.objects.filter(is_active=True, pk=body['tutor']).first() if isinstance(body['tutor'], int) else None
        if tutor is None:
            return error('Выберите тьютора.')
        trial.tutor = tutor
    trial.save()
    return JsonResponse(trial_dict(trial))


@require_POST
@role_required(is_manager)
def trial_delete(request, pk):
    trial = get_object_or_404(TrialLesson, pk=pk)
    if trial.sent_at:
        return error('Обратная связь уже отправлена родителю — такую запись удалить нельзя.')
    trial.delete()
    return JsonResponse({'deleted': pk})


@require_POST
@role_required(is_manager)
def trial_mark_sent(request, pk):
    trial = get_object_or_404(TrialLesson, pk=pk)
    if not trial.feedback_at:
        return error('Тьютор ещё не заполнил обратную связь.')
    if request.POST.get('undo') or json_body(request).get('undo'):
        TrialLesson.objects.filter(pk=pk).update(sent_at=None, sent_by=None)
        return JsonResponse({'sent': False})
    TrialLesson.objects.filter(pk=pk).update(sent_at=timezone.now(), sent_by=request.user)
    return JsonResponse({'sent': True})


# ---------- тьютор ----------

@role_required(can_write_feedback)
def feedback_page(request):
    today = timezone.localdate()
    trials = TrialLesson.objects.select_related('tutor').order_by('lesson_date', 'full_name')
    if not request.user.is_superuser:
        trials = trials.filter(tutor=request.user)
    trials = list(trials.filter(lesson_date__gte=today - datetime.timedelta(days=60)))
    for trial in trials:
        # ОС, написанные до появления полей, показываем в «Что делали», чтобы текст не потерялся
        if trial.feedback and not any(trial.feedback_parts().values()):
            trial.activities = trial.feedback
    group_names = StudyGroup.objects.filter(is_active=True).order_by('name').values_list('name', flat=True).distinct()
    return render(request, 'lessons/staff/feedback.html', {
        'group_names': list(group_names),
        'todo': [t for t in trials if t.lesson_date <= today and not t.feedback_at],
        'upcoming': [t for t in trials if t.lesson_date > today],
        'done': sorted((t for t in trials if t.feedback_at), key=lambda t: t.lesson_date, reverse=True),
        'today': today,
        'show_tutor': request.user.is_superuser,
    })


@require_POST
@role_required(can_write_feedback)
def feedback_save(request, pk):
    trials = TrialLesson.objects.all() if request.user.is_superuser else TrialLesson.objects.filter(tutor=request.user)
    trial = get_object_or_404(trials, pk=pk)
    if trial.sent_at:
        return error('Менеджер уже отправил эту обратную связь родителю — изменить её нельзя.', status=409)
    body = json_body(request)
    for name in FEEDBACK_FIELDS:
        value = str(body.get(name, '')).strip()
        limit = TrialLesson._meta.get_field(name).max_length or FEEDBACK_TEXT_LIMIT
        if len(value) > limit:
            return error(f'Поле «{FEEDBACK_LABELS[name]}» слишком длинное — не больше {limit} символов.', field=name)
        setattr(trial, name, value)
    if body.get('done'):
        missing = [name for name in FEEDBACK_FIELDS if not getattr(trial, name)]
        if missing:
            return error('Заполните: ' + ', '.join(FEEDBACK_LABELS[n] for n in missing).lower() + '.', missing=missing)
    trial.feedback = trial.compose_feedback()
    if not trial.feedback:
        trial.feedback_at = None
    elif body.get('done'):
        # Только по кнопке «Готово» ОС уходит менеджеру; автосохранение пишет черновик
        trial.feedback_at = timezone.now()
    trial.save(update_fields=[*FEEDBACK_FIELDS, 'feedback', 'feedback_at'])
    saved = timezone.localtime(trial.feedback_at) if trial.feedback_at else None
    return JsonResponse({'saved': True, 'done': bool(trial.feedback_at), 'status': trial.status,
                         'saved_at': f'{saved:%H:%M}' if saved else '', 'feedback': trial.feedback})
