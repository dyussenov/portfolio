import csv
import datetime
import io

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, Max, Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin
from import_export.forms import ConfirmImportForm, ImportForm

from .models import (
    FEEDBACK_FIELDS, MANAGER_GROUP, TUTOR_GROUP, Branch, Manager, QuickLink, Screenshot, StaffPin, Student, StudyGroup, TrialLesson, Tutor,
    is_tutor,
)
from .trials import TrialLookupError, fetch_trial_student, whatsapp_url
from .reports import upload_report
from .resources import StudentResource


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': 'image/*'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)] if data else []


class StudentAdminForm(forms.ModelForm):
    new_images = MultipleImageField(
        label='Добавить скриншоты', required=False,
        help_text='Можно выбрать сразу несколько файлов — все они сохранятся за дату ниже.',
    )
    new_images_date = forms.DateField(
        label='Дата урока для новых скриншотов', required=False,
        widget=admin.widgets.AdminDateWidget, initial=timezone.localdate,
    )

    class Meta:
        model = Student
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_images') and not cleaned.get('new_images_date'):
            self.add_error('new_images_date', 'Укажите дату урока для новых скриншотов.')
        return cleaned


class BranchImportForm(ImportForm):
    branch = forms.ModelChoiceField(Branch.objects.all(), label='Филиал', help_text='Все ученики из файла попадут в этот филиал.')


class BranchConfirmImportForm(ConfirmImportForm):
    branch = forms.ModelChoiceField(Branch.objects.all(), widget=forms.HiddenInput)


def public_url(student, request=None):
    path = student.get_absolute_url()
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip('/') + path
    return request.build_absolute_uri(path) if request else path


def thumbnail(image, size=120):
    if not image:
        return '—'
    return format_html(
        '<a href="{0}" target="_blank"><img src="{0}" style="max-width:{1}px;max-height:{1}px;border-radius:4px"></a>',
        image.url, size,
    )


@admin.display(description='загрузил')
def uploader(obj):
    return obj.uploaded_by or obj.uploaded_by_tutor or '—'


class ScreenshotInline(admin.TabularInline):
    model = Screenshot
    extra = 0
    fields = ('preview', 'lesson_date', 'image', 'created_at', uploader)
    readonly_fields = ('preview', 'created_at', uploader)
    ordering = ('-lesson_date', 'created_at')

    @admin.display(description='превью')
    def preview(self, obj):
        return thumbnail(obj.image)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'students_count')
    prepopulated_fields = {'slug': ('name',)}
    actions = ['export_pins']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(students_count=Count('students'))

    @admin.display(description='учеников', ordering='students_count')
    def students_count(self, obj):
        return obj.students_count

    @admin.action(description='Выгрузить пин-коды учеников (CSV)')
    def export_pins(self, request, queryset):
        filename = f'pins_{queryset.get().slug}.csv' if queryset.count() == 1 else 'pins.csv'
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write('\ufeff')  # BOM, чтобы Excel правильно открыл кириллицу
        writer = csv.writer(response)
        writer.writerow(['ID', 'ФИО', 'Филиал', 'Пин-код'])
        students = Student.objects.filter(branch__in=queryset, is_active=True).select_related('branch')
        for s in students.order_by('branch__name', 'full_name'):
            writer.writerow([s.external_id or '', s.full_name, s.branch.name, s.pin])
        return response


@admin.register(Student)
class StudentAdmin(ImportExportModelAdmin):
    form = StudentAdminForm
    resource_classes = [StudentResource]
    import_export_change_list_template = 'admin/lessons/student/sync_change_list.html'
    import_form_class = BranchImportForm
    confirm_form_class = BranchConfirmImportForm
    list_display = ('full_name', 'branch', 'group_names', 'pin', 'screenshots_count', 'last_lesson', 'is_active', 'personal_link')
    list_filter = ('branch', ('groups', admin.RelatedOnlyFieldListFilter), 'is_active')
    search_fields = ('full_name', 'external_id')
    readonly_fields = ('group_names', 'personal_link_full')
    inlines = [ScreenshotInline]
    actions = ['export_links']
    fieldsets = (
        (None, {'fields': ('full_name', 'branch', 'group_names', 'birth_date', 'external_id', 'pin', 'is_active', 'personal_link_full')}),
        ('Загрузка скриншотов', {'fields': ('new_images_date', 'new_images')}),
    )

    def can_edit(self, request):
        return self.has_add_permission(request) and self.has_change_permission(request)

    def has_import_permission(self, request):
        # import-export по умолчанию разрешает импорт всем, кто зашёл в админку — тьютору нельзя
        return self.can_edit(request) and super().has_import_permission(request)

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {**(extra_context or {}), 'can_sync': self.can_edit(request)})

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if not self.has_change_permission(request, obj):
            return [fs for fs in fieldsets if 'new_images' not in fs[1]['fields']]
        return fieldsets

    def get_urls(self):
        return [
            path('alfacrm-sync/', self.admin_site.admin_view(self.alfacrm_sync_view), name='lessons_student_alfacrm_sync'),
            *super().get_urls(),
        ]

    def alfacrm_sync_view(self, request):
        if not self.can_edit(request):
            raise PermissionDenied
        applied = request.method == 'POST'
        output, error = io.StringIO(), None
        try:
            call_command('sync_alfacrm', apply=applied, stdout=output, stderr=output)
        except CommandError as e:
            error = str(e)
        return render(request, 'admin/lessons/student/alfacrm_sync.html', {
            **self.admin_site.each_context(request),
            'title': 'Синхронизация с AlfaCRM',
            'opts': self.model._meta,
            'report': output.getvalue(),
            'error': error,
            'applied': applied,
            'write_links': settings.ALFACRM_WRITE_LINKS,
        })

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('branch').prefetch_related(
            Prefetch('groups', queryset=StudyGroup.objects.order_by('name')),
        ).annotate(
            screenshots_count=Count('screenshots'), last_lesson=Max('screenshots__lesson_date'),
        )

    @admin.display(description='группы')
    def group_names(self, obj):
        # Группы ведутся в AlfaCRM и приходят синхронизацией, поэтому здесь только для чтения
        if not obj.pk:
            return '—'
        return ', '.join(g.name for g in obj.groups.all()) or '—'

    @admin.display(description='скриншотов', ordering='screenshots_count')
    def screenshots_count(self, obj):
        return obj.screenshots_count

    @admin.display(description='последний урок', ordering='last_lesson')
    def last_lesson(self, obj):
        return obj.last_lesson

    @admin.display(description='ссылка')
    def personal_link(self, obj):
        return format_html('<a href="{}" target="_blank">открыть</a>', obj.get_absolute_url())

    @admin.display(description='ссылка для родителей')
    def personal_link_full(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        url = public_url(obj)
        return format_html('<a href="{0}" target="_blank">{0}</a>', url)

    def get_import_resource_kwargs(self, request, **kwargs):
        resource_kwargs = super().get_import_resource_kwargs(request, **kwargs)
        form = kwargs.get('form')
        if form is not None and form.is_valid():
            resource_kwargs['branch'] = form.cleaned_data['branch']
        return resource_kwargs

    def get_confirm_form_initial(self, request, import_form):
        initial = super().get_confirm_form_initial(request, import_form)
        if import_form is not None:
            initial['branch'] = import_form.cleaned_data['branch'].pk
        return initial

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        images = form.cleaned_data.get('new_images') or []
        for image in images:
            Screenshot.objects.create(student=form.instance, image=image, lesson_date=form.cleaned_data['new_images_date'])
        if images:
            self.message_user(request, f'Добавлено скриншотов: {len(images)}')

    @admin.action(description='Выгрузить ссылки для родителей (CSV)')
    def export_links(self, request, queryset):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="student_links.csv"'
        response.write('﻿')  # BOM, чтобы Excel правильно открыл кириллицу
        writer = csv.writer(response)
        writer.writerow(['ID', 'ФИО', 'Филиал', 'Ссылка'])
        for s in queryset.order_by('branch__name', 'full_name'):
            writer.writerow([s.external_id or '', s.full_name, s.branch.name, public_url(s, request)])
        return response


@admin.register(StaffPin)
class StaffPinAdmin(admin.ModelAdmin):
    list_display = ('employee', 'code', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('employee',)
    fields = ('employee', 'pin_code', 'is_active')
    readonly_fields = ('pin_code',)

    @admin.display(description='код')
    def pin_code(self, obj):
        return obj.code if obj.pk else 'Сгенерируется при сохранении'


class UploadReportForm(forms.Form):
    date_from = forms.DateField(label='С', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    date_to = forms.DateField(label='По', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('date_from') and cleaned.get('date_to') and cleaned['date_from'] > cleaned['date_to']:
            raise forms.ValidationError('Дата начала позже даты окончания.')
        return cleaned


@admin.register(Screenshot)
class ScreenshotAdmin(admin.ModelAdmin):
    change_list_template = 'admin/lessons/screenshot/change_list.html'
    list_display = ('preview', 'student', 'branch', 'lesson_date', 'created_at', uploader)
    list_display_links = ('preview', 'student')
    list_filter = ('lesson_date', 'student__branch', 'uploaded_by', 'uploaded_by_tutor')
    readonly_fields = ('uploaded_by', 'uploaded_by_tutor')
    search_fields = ('student__full_name',)
    date_hierarchy = 'lesson_date'
    autocomplete_fields = ('student',)
    list_select_related = ('student__branch', 'uploaded_by', 'uploaded_by_tutor')
    list_per_page = 50

    @admin.display(description='превью')
    def preview(self, obj):
        return thumbnail(obj.image, 80)

    @admin.display(description='филиал', ordering='student__branch__name')
    def branch(self, obj):
        return obj.student.branch

    def get_urls(self):
        return [
            path('report/', self.admin_site.admin_view(self.report_view), name='lessons_screenshot_report'),
            *super().get_urls(),
        ]

    def report_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        today = timezone.localdate()
        if 'date_from' in request.GET or 'date_to' in request.GET:
            form = UploadReportForm(request.GET)
        else:
            # По умолчанию — текущая неделя с понедельника по сегодня
            form = UploadReportForm(initial={'date_from': today - datetime.timedelta(days=today.weekday()), 'date_to': today})
        report = None
        if form.is_bound and form.is_valid():
            report = upload_report(form.cleaned_data['date_from'], form.cleaned_data['date_to'])
        return render(request, 'admin/lessons/screenshot/report.html', {
            **self.admin_site.each_context(request),
            'title': 'Отчёт по загруженным картинкам',
            'opts': self.model._meta,
            'form': form,
            'report': report,
        })


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
    """Группы приходят из AlfaCRM — здесь их можно только посмотреть."""
    list_display = ('name', 'branch', 'students_count', 'is_active', 'external_id')
    list_filter = ('branch', 'is_active')
    search_fields = ('name',)
    list_select_related = ('branch',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(students_count=Count('students'))

    @admin.display(description='учеников', ordering='students_count')
    def students_count(self, obj):
        return obj.students_count

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StaffRoleAdmin(UserAdmin):
    """Сотрудник с одной ролью: заходит в админку, права даёт группа role_group."""
    role_group = None
    role_description = ''
    list_display = ('username', 'first_name', 'last_name', 'is_active', 'last_login')
    list_filter = ('is_active',)
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Сотрудник', {'fields': ('first_name', 'last_name', 'is_active')}),
        ('Даты', {'fields': ('last_login', 'date_joined')}),
    )
    readonly_fields = ('last_login', 'date_joined')

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {
                'classes': ('wide',),
                'fields': ('username', 'first_name', 'last_name', 'password1', 'password2'),
                'description': self.role_description,
            }),)
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        obj.is_staff = True
        obj.is_superuser = False
        super().save_model(request, obj, form, change)
        obj.groups.add(Group.objects.get_or_create(name=self.role_group)[0])


@admin.register(Tutor)
class TutorAdmin(StaffRoleAdmin):
    role_group = TUTOR_GROUP
    role_description = (
        'Тьютор может заходить в админку и смотреть учеников и скриншоты, но не может ничего менять. '
        'Его пароль также работает как секретный пин-код при загрузке скриншотов на уроке. '
        'В пробных занятиях он заполняет обратную связь по своим ученикам.'
    )


@admin.register(Manager)
class ManagerAdmin(StaffRoleAdmin):
    role_group = MANAGER_GROUP
    role_description = (
        'Менеджер записывает учеников на пробные занятия, назначает тьютора, '
        'читает обратную связь и отправляет её родителям.'
    )


class TrialLessonForm(forms.ModelForm):
    class Meta:
        model = TrialLesson
        fields = ('crm_url', 'lesson_date', 'tutor', *FEEDBACK_FIELDS, 'feedback')
        widgets = {'feedback': forms.Textarea(attrs={'rows': 14, 'style': 'width:100%;max-width:720px'})}

    crm_data = None

    def clean(self):
        cleaned = super().clean()
        if 'crm_url' in self.fields and cleaned.get('crm_url') and (not self.instance.pk or 'crm_url' in self.changed_data):
            try:
                self.crm_data = fetch_trial_student(cleaned['crm_url'])
            except TrialLookupError as e:
                self.add_error('crm_url', str(e))
        return cleaned


class TrialStatusFilter(admin.SimpleListFilter):
    title = 'статус'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return [('waiting', 'ждём ОС от тьютора'), ('ready', 'ОС готова, не отправлена'), ('sent', 'отправлено родителю')]

    def queryset(self, request, queryset):
        if self.value() == 'waiting':
            return queryset.filter(feedback_at__isnull=True)
        if self.value() == 'ready':
            return queryset.filter(feedback_at__isnull=False, sent_at__isnull=True)
        if self.value() == 'sent':
            return queryset.filter(sent_at__isnull=False)
        return queryset


@admin.register(TrialLesson)
class TrialLessonAdmin(admin.ModelAdmin):
    form = TrialLessonForm
    list_display = ('lesson_date', 'full_name', 'age', 'tutor_name', 'status_label', 'sent_at')
    list_display_links = ('lesson_date', 'full_name')
    list_filter = (TrialStatusFilter, 'lesson_date', ('tutor', admin.RelatedOnlyFieldListFilter))
    search_fields = ('full_name', 'parent_phone', 'crm_customer_id')
    date_hierarchy = 'lesson_date'
    list_select_related = ('tutor',)
    actions = ['refresh_from_crm']

    MANAGER_INFO = (
        'full_name', 'age', 'birth_date', 'parent_name', 'parent_phone', 'crm_card',
    )
    MANAGER_FEEDBACK = (
        'status_label', *FEEDBACK_FIELDS, 'feedback', 'feedback_at', 'send_to_parent', 'sent_at', 'sent_by', 'created_by',
    )
    EDITABLE_FEEDBACK = (*FEEDBACK_FIELDS, 'feedback')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_tutor(request.user):
            return queryset.filter(tutor=request.user)
        return queryset

    def has_change_permission(self, request, obj=None):
        if is_tutor(request.user) and obj is not None and obj.tutor_id != request.user.pk:
            return False
        return super().has_change_permission(request, obj)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {'fields': ('crm_url', 'lesson_date', 'tutor')}),)
        if is_tutor(request.user):
            return (
                ('Пробное занятие', {'fields': ('full_name', 'age', 'lesson_date', 'status_label')}),
                ('Обратная связь', {'fields': FEEDBACK_FIELDS}),
            )
        return (
            ('Пробное занятие', {'fields': ('crm_url', 'lesson_date', 'tutor')}),
            ('Ученик из AlfaCRM', {'fields': self.MANAGER_INFO}),
            ('Обратная связь', {'fields': self.MANAGER_FEEDBACK}),
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        if is_tutor(request.user):
            return ('full_name', 'age', 'lesson_date', 'status_label')
        return (*self.MANAGER_INFO, *(f for f in self.MANAGER_FEEDBACK if f not in self.EDITABLE_FEEDBACK))

    def save_model(self, request, obj, form, change):
        if form.crm_data:
            obj.apply_crm_data(form.crm_data)
        elif 'lesson_date' in form.changed_data:
            obj.apply_crm_data({f: getattr(obj, f) for f in (
                'crm_customer_id', 'crm_branch_id', 'full_name', 'birth_date', 'parent_name', 'parent_phone')})
        if set(FEEDBACK_FIELDS) & set(form.changed_data):
            obj.feedback = obj.compose_feedback()
        if set(self.EDITABLE_FEEDBACK) & set(form.changed_data):
            obj.feedback_at = timezone.now() if obj.feedback.strip() else None
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='тьютор', ordering='tutor__first_name')
    def tutor_name(self, obj):
        return obj.tutor.get_full_name() or obj.tutor.username

    @admin.display(description='статус')
    def status_label(self, obj):
        return obj.status

    @admin.display(description='карточка в CRM')
    def crm_card(self, obj):
        host = settings.ALFACRM_HOST.strip().removeprefix('https://').removeprefix('http://').strip('/')
        if not host:
            return obj.crm_customer_id
        url = f'https://{host}/company/{obj.crm_branch_id}/customer/view?id={obj.crm_customer_id}'
        return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', url)

    @admin.display(description='отправить родителю')
    def send_to_parent(self, obj):
        if not obj.feedback_at:
            return 'Появится, когда тьютор заполнит обратную связь'
        if not whatsapp_url(obj.parent_phone, obj.feedback):
            return 'В AlfaCRM нет телефона родителя — скопируйте текст обратной связи вручную'
        label = 'Отправить ещё раз в WhatsApp' if obj.sent_at else 'Отправить в WhatsApp'
        return format_html(
            '<a class="button" href="{}" target="_blank" rel="noopener">{}</a> '
            '<span class="help">Сохраните изменения в тексте перед отправкой</span>',
            reverse('admin:lessons_triallesson_send', args=[obj.pk]), label,
        )

    def get_urls(self):
        return [
            path('<int:pk>/send/', self.admin_site.admin_view(self.send_view), name='lessons_triallesson_send'),
            *super().get_urls(),
        ]

    def send_view(self, request, pk):
        trial = get_object_or_404(TrialLesson, pk=pk)
        if is_tutor(request.user) or not self.has_change_permission(request, trial):
            raise PermissionDenied
        url = whatsapp_url(trial.parent_phone, trial.feedback) if trial.feedback_at else None
        if url is None:
            self.message_user(request, 'Отправлять нечего: нет обратной связи или телефона родителя.', level='error')
            return redirect('admin:lessons_triallesson_change', pk)
        TrialLesson.objects.filter(pk=pk).update(sent_at=timezone.now(), sent_by=request.user)
        return redirect(url)

    @admin.action(description='Обновить данные учеников из AlfaCRM', permissions=['add'])
    def refresh_from_crm(self, request, queryset):
        updated = 0
        for trial in queryset:
            try:
                trial.apply_crm_data(fetch_trial_student(trial.crm_url))
            except TrialLookupError as e:
                self.message_user(request, f'{trial}: {e}', level='error')
                continue
            trial.save()
            updated += 1
        self.message_user(request, f'Обновлено: {updated}')


LIFETIME_CHOICES = [(15, '15 минут'), (30, '30 минут'), (45, '45 минут'), (60, '1 час'), (90, '1,5 часа'), (120, '2 часа'), (180, '3 часа')]


class QuickLinkAddForm(forms.ModelForm):
    lifetime = forms.TypedChoiceField(
        label='Показывать', choices=LIFETIME_CHOICES, coerce=int, initial=45,
        help_text='Пока ссылка жива, она висит на главной странице. Потом исчезнет сама.',
    )

    class Meta:
        model = QuickLink
        fields = ('title', 'url')

    def save(self, commit=True):
        self.instance.expires_at = timezone.now() + datetime.timedelta(minutes=self.cleaned_data['lifetime'])
        return super().save(commit)


@admin.register(QuickLink)
class QuickLinkAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'link', 'expires_at', 'status', 'created_by')
    list_select_related = ('created_by',)
    readonly_fields = ('created_by', 'created_at')
    actions = ['expire_now']

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = QuickLinkAddForm
            kwargs['fields'] = ('title', 'url', 'lifetime')
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ('title', 'url', 'lifetime')
        return ('title', 'url', 'expires_at', 'created_by', 'created_at')

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj else ()

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='адрес')
    def link(self, obj):
        return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', obj.url)

    @admin.display(description='статус', boolean=True)
    def status(self, obj):
        return obj.is_alive

    @admin.action(description='Убрать с главной страницы сейчас', permissions=['change'])
    def expire_now(self, request, queryset):
        updated = queryset.alive().update(expires_at=timezone.now())
        self.message_user(request, f'Убрано ссылок: {updated}')
