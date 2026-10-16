import csv

from django import forms
from django.conf import settings
from django.contrib import admin
from django.db.models import Count, Max
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin
from import_export.forms import ConfirmImportForm, ImportForm

from .models import Branch, Screenshot, Student
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


class ScreenshotInline(admin.TabularInline):
    model = Screenshot
    extra = 0
    fields = ('preview', 'lesson_date', 'image', 'created_at')
    readonly_fields = ('preview', 'created_at')
    ordering = ('-lesson_date', 'created_at')

    @admin.display(description='превью')
    def preview(self, obj):
        return thumbnail(obj.image)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'students_count')
    prepopulated_fields = {'slug': ('name',)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(students_count=Count('students'))

    @admin.display(description='учеников', ordering='students_count')
    def students_count(self, obj):
        return obj.students_count


@admin.register(Student)
class StudentAdmin(ImportExportModelAdmin):
    form = StudentAdminForm
    resource_classes = [StudentResource]
    import_form_class = BranchImportForm
    confirm_form_class = BranchConfirmImportForm
    list_display = ('full_name', 'branch', 'screenshots_count', 'last_lesson', 'is_active', 'personal_link')
    list_filter = ('branch', 'is_active')
    search_fields = ('full_name', 'external_id')
    readonly_fields = ('personal_link_full',)
    inlines = [ScreenshotInline]
    actions = ['export_links']
    fieldsets = (
        (None, {'fields': ('full_name', 'branch', 'external_id', 'is_active', 'personal_link_full')}),
        ('Загрузка скриншотов', {'fields': ('new_images_date', 'new_images')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('branch').annotate(
            screenshots_count=Count('screenshots'), last_lesson=Max('screenshots__lesson_date'),
        )

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


@admin.register(Screenshot)
class ScreenshotAdmin(admin.ModelAdmin):
    list_display = ('preview', 'student', 'branch', 'lesson_date', 'created_at')
    list_display_links = ('preview', 'student')
    list_filter = ('lesson_date', 'student__branch')
    search_fields = ('student__full_name',)
    date_hierarchy = 'lesson_date'
    autocomplete_fields = ('student',)
    list_select_related = ('student__branch',)
    list_per_page = 50

    @admin.display(description='превью')
    def preview(self, obj):
        return thumbnail(obj.image, 80)

    @admin.display(description='филиал', ordering='student__branch__name')
    def branch(self, obj):
        return obj.student.branch
