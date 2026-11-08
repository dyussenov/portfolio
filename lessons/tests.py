import datetime
import io
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Branch, Screenshot, Student

MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(
    ALFACRM_HOST='demo.s20.online', ALFACRM_EMAIL='a@a.a', ALFACRM_API_KEY='key',
    ALFACRM_LINK_FIELD='custom_link', PUBLIC_BASE_URL='https://r.example.kz/', ALFACRM_WRITE_LINKS=True,
)
class SyncAlfaCRMTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Орбита', slug='orbita')
        self.student = Student.objects.create(full_name='Иванов Иван', branch=self.branch, external_id=1)
        self.link = f'https://r.example.kz{self.student.get_absolute_url()}'

    def sync(self, customers, **options):
        from django.core.management import call_command
        with patch('lessons.management.commands.sync_alfacrm.AlfaCRMClient') as client_class:
            client = client_class.return_value
            client.branches.return_value = [{'id': 2, 'name': 'Филиал Орбита'}, {'id': 3, 'name': 'Другой'}]
            client.students.side_effect = lambda branch_id: iter(customers if branch_id == 2 else [])
            out, err = io.StringIO(), io.StringIO()
            call_command('sync_alfacrm', stdout=out, stderr=err, **options)
        return client, out.getvalue(), err.getvalue()

    def test_branch_matches_by_slug(self):
        from .management.commands.sync_alfacrm import find_local_branch
        self.assertEqual(find_local_branch('KIBERone_Orbita', [self.branch]), self.branch)
        self.assertIsNone(find_local_branch('KIBERone_Gagarina', [self.branch]))

    def test_dry_run_changes_nothing(self):
        client, out, _ = self.sync([{'id': 1, 'name': 'Иванов Иван'}, {'id': 2, 'name': 'Петров Пётр'}])
        client.update_customer.assert_not_called()
        self.assertEqual(Student.objects.count(), 1)
        self.assertIn('новых учеников 1, ссылок записать 2', out)

    def test_apply_creates_new_and_writes_links(self):
        client, _, _ = self.sync(
            [{'id': 1, 'name': 'ИВАНОВ  Иван'}, {'id': 2, 'name': 'Петров Пётр'}], apply=True,
        )
        new = Student.objects.get(external_id=2)
        self.assertEqual(new.branch, self.branch)
        client.update_customer.assert_any_call(2, 1, {'custom_link': self.link})
        client.update_customer.assert_any_call(
            2, 2, {'custom_link': f'https://r.example.kz{new.get_absolute_url()}'},
        )

    def test_name_and_birth_date_come_from_crm(self):
        customers = [
            {'id': 1, 'name': 'Иванов Иван Иванович', 'dob': '05.03.2015'},
            {'id': 2, 'name': 'Петров Пётр', 'dob': '07.04.2016'},
        ]
        _, out, _ = self.sync(customers)
        self.student.refresh_from_db()
        self.assertEqual((self.student.full_name, self.student.birth_date), ('Иванов Иван', None))
        self.assertIn('ФИО обновить 1, дат рождения обновить 1', out)

        self.sync(customers, apply=True)
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, 'Иванов Иван Иванович')
        self.assertEqual(self.student.birth_date, datetime.date(2015, 3, 5))
        self.assertEqual(Student.objects.get(external_id=2).birth_date, datetime.date(2016, 4, 7))

    def test_empty_or_bad_birth_date_keeps_existing(self):
        date = datetime.date(2015, 3, 5)
        Student.objects.filter(pk=self.student.pk).update(birth_date=date)
        _, _, err = self.sync([{'id': 1, 'name': 'Иванов Иван', 'dob': ''}], apply=True)
        self.assertNotIn('дату рождения', err)
        _, _, err = self.sync([{'id': 1, 'name': 'Иванов Иван', 'dob': '2015-99'}], apply=True)
        self.assertIn('дату рождения', err)
        self.student.refresh_from_db()
        self.assertEqual(self.student.birth_date, date)

    @override_settings(ALFACRM_WRITE_LINKS=False, ALFACRM_LINK_FIELD='', PUBLIC_BASE_URL='https://results.example.kz')
    def test_local_env_updates_database_but_never_writes_to_crm(self):
        client, out, _ = self.sync(
            [{'id': 1, 'name': 'Иванов Иван Иванович'}, {'id': 2, 'name': 'Петров Пётр'}], apply=True,
        )
        client.update_customer.assert_not_called()
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, 'Иванов Иван Иванович')
        self.assertTrue(Student.objects.filter(external_id=2).exists())
        self.assertIn('не записываются', out)

    def test_foreign_value_is_not_overwritten(self):
        client, _, err = self.sync([{'id': 1, 'name': 'Иванов Иван', 'custom_link': 'https://x'}], apply=True)
        client.update_customer.assert_not_called()
        self.assertIn('не перезаписано', err)

    @override_settings(PUBLIC_BASE_URL='https://results.example.kz')
    def test_apply_refuses_placeholder_domain(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.sync([{'id': 1, 'name': 'Иванов Иван'}], apply=True)

    def test_admin_previews_then_applies(self):
        url = reverse('admin:lessons_student_alfacrm_sync')
        self.assertEqual(self.client.get(url).status_code, 302)
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        self.assertContains(self.client.get(reverse('admin:lessons_student_changelist')), url)
        with patch('lessons.management.commands.sync_alfacrm.AlfaCRMClient') as client_class:
            client = client_class.return_value
            client.branches.return_value = [{'id': 2, 'name': 'Орбита'}]
            client.students.side_effect = lambda branch_id: iter([{'id': 1, 'name': 'Иванов Иван'}])
            self.assertContains(self.client.get(url), 'Пробный запуск')
            client.update_customer.assert_not_called()
            self.assertContains(self.client.post(url), 'Применено')
        client.update_customer.assert_called_once_with(2, 1, {'custom_link': self.link})

    def test_admin_shows_command_error(self):
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        with override_settings(ALFACRM_API_KEY=''):
            self.assertContains(self.client.get(reverse('admin:lessons_student_alfacrm_sync')), 'ALFACRM_API_KEY')

    def test_repeat_run_is_idempotent(self):
        client, out, _ = self.sync([{'id': 1, 'name': 'Иванов Иван', 'custom_link': self.link}], apply=True)
        client.update_customer.assert_not_called()
        self.assertIn('уже верных ссылок 1', out)

    def test_client_paginates_and_reports_failed_update(self):
        from .alfacrm import PAGE_SIZE, AlfaCRMClient, AlfaCRMError
        client = AlfaCRMClient('https://demo.s20.online/', 'a@a.a', 'key')
        self.assertEqual(client.base_url, 'https://demo.s20.online/v2api')
        pages = [
            {'total': PAGE_SIZE + 1, 'items': [{'id': i} for i in range(PAGE_SIZE)]},
            {'total': PAGE_SIZE + 1, 'items': [{'id': PAGE_SIZE}]},
        ]
        with patch.object(client, 'post', side_effect=pages) as post:
            self.assertEqual(len(list(client.students(2))), PAGE_SIZE + 1)
        self.assertEqual(post.call_args.args[1], {'is_study': 1, 'page': 1, 'pageSize': PAGE_SIZE})
        with patch.object(client, 'post', return_value={'success': False, 'errors': {'x': ['bad']}}):
            with self.assertRaises(AlfaCRMError):
                client.update_customer(2, 1, {'custom_link': 'u'})


class BirthdayTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Орбита', slug='orbita')

    def add(self, name, month, day, **kwargs):
        return Student.objects.create(
            full_name=name, branch=self.branch, birth_date=datetime.date(2015, month, day), **kwargs,
        )

    def test_nearest_five_wrap_over_new_year_and_skip_inactive(self):
        from .views import upcoming_birthdays
        self.add('Январь', 1, 10)
        self.add('Вчера', 12, 19)
        self.add('Сегодня', 12, 20)
        self.add('Скрыт', 12, 21, is_active=False)
        self.add('Завтра', 12, 21)
        self.add('Послезавтра', 12, 22)
        self.add('Ещё', 12, 25)
        Student.objects.create(full_name='Без даты', branch=self.branch)
        result = upcoming_birthdays(datetime.date(2026, 12, 20))
        self.assertEqual(
            [b['full_name'] for b in result], ['Сегодня', 'Завтра', 'Послезавтра', 'Ещё', 'Январь'],
        )
        self.assertTrue(result[0]['is_today'])
        self.assertEqual(result[4]['date'], datetime.date(2027, 1, 10))

    def test_past_birthdays_only_from_monday_to_yesterday(self):
        from .views import past_birthdays_this_week
        self.add('Прошлая неделя', 11, 1)
        self.add('Понедельник', 11, 2)
        self.add('Четверг', 11, 5)
        self.add('Скрыт', 11, 5, is_active=False)
        self.add('Сегодня', 11, 6)
        self.add('Завтра', 11, 7)
        result = past_birthdays_this_week(datetime.date(2026, 11, 6))
        self.assertEqual([b['full_name'] for b in result], ['Четверг', 'Понедельник'])

    def test_past_birthdays_when_week_crosses_new_year(self):
        from .views import past_birthdays_this_week
        self.add('Декабрь', 12, 29)
        self.add('Январь', 1, 1)
        result = past_birthdays_this_week(datetime.date(2027, 1, 2))
        self.assertEqual(
            [(b['full_name'], b['date']) for b in result],
            [('Январь', datetime.date(2027, 1, 1)), ('Декабрь', datetime.date(2026, 12, 29))],
        )

    def test_feb_29_in_non_leap_year(self):
        from .views import next_birthday
        leap = datetime.date(2016, 2, 29)
        self.assertEqual(next_birthday(leap, datetime.date(2026, 2, 1)), datetime.date(2026, 2, 28))
        self.assertEqual(next_birthday(leap, datetime.date(2027, 3, 1)), datetime.date(2028, 2, 29))

    def test_home_page_shows_day_without_birth_year(self):
        student = self.add('Сидоров Сидор', 3, 5)
        resp = self.client.get(reverse('lessons:upload'))
        self.assertContains(resp, '5 марта')
        self.assertNotContains(resp, str(student.birth_date.year))


def png(name='image.png', color='red'):
    buf = io.BytesIO()
    Image.new('RGB', (20, 20), color).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class LessonsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.branch = Branch.objects.create(name='Орбита', slug='orbita')
        self.student = Student.objects.create(full_name='Иванов Иван', branch=self.branch, external_id=1)

    def test_upload_and_gallery(self):
        resp = self.client.post(reverse('lessons:upload_screenshots'), {
            'student': self.student.pk, 'pin': self.student.pin, 'images': [png(), png(color='blue')],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['saved'], 2)
        self.assertEqual(self.student.screenshots.count(), 2)

        resp = self.client.get(self.student.get_absolute_url())
        self.assertContains(resp, 'Иванов Иван')
        self.assertContains(resp, 'class="gallery-item"', count=2)

    def test_upload_rejects_non_image_and_unknown_student(self):
        url = reverse('lessons:upload_screenshots')
        bad = SimpleUploadedFile('x.png', b'not an image', content_type='image/png')
        pin = self.student.pin
        self.assertEqual(self.client.post(url, {'student': self.student.pk, 'pin': pin, 'images': [bad]}).status_code, 400)
        self.assertEqual(self.client.post(url, {'student': 999, 'pin': pin, 'images': [png()]}).status_code, 400)
        self.assertFalse(Screenshot.objects.exists())

    def test_upload_requires_correct_pin_and_locks_after_many_attempts(self):
        from django.core.cache import cache
        from .views import MAX_PIN_ATTEMPTS
        self.addCleanup(cache.clear)
        url = reverse('lessons:upload_screenshots')
        wrong = '0000' if self.student.pin != '0000' else '1111'
        self.assertEqual(self.client.post(url, {'student': self.student.pk, 'images': [png()]}).status_code, 403)
        for _ in range(MAX_PIN_ATTEMPTS - 1):
            self.assertEqual(self.client.post(url, {'student': self.student.pk, 'pin': wrong, 'images': [png()]}).status_code, 403)
        resp = self.client.post(url, {'student': self.student.pk, 'pin': self.student.pin, 'images': [png()]})
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(Screenshot.objects.exists())

    def test_staff_pin_works_for_any_student_until_deactivated(self):
        from .models import StaffPin
        url = reverse('lessons:upload_screenshots')
        other = Student.objects.create(full_name='Петров Пётр', branch=self.branch)
        staff = StaffPin.objects.create(employee='Ассистент Анна')
        self.assertRegex(staff.code, r'^\d{8}$')
        for student in (self.student, other):
            resp = self.client.post(url, {'student': student.pk, 'pin': staff.code, 'images': [png()]})
            self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Screenshot.objects.filter(uploaded_by=staff).count(), 2)

        self.client.post(url, {'student': self.student.pk, 'pin': self.student.pin, 'images': [png()]})
        self.assertEqual(Screenshot.objects.filter(uploaded_by__isnull=True).count(), 1)

        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        self.assertContains(self.client.get(reverse('admin:lessons_screenshot_changelist')), 'Ассистент Анна')

        StaffPin.objects.filter(pk=staff.pk).update(is_active=False)
        resp = self.client.post(url, {'student': self.student.pk, 'pin': staff.code, 'images': [png()]})
        self.assertEqual(resp.status_code, 403)

    def test_admin_creates_staff_pin_for_employee(self):
        from .models import StaffPin
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        self.assertContains(self.client.get(reverse('admin:lessons_staffpin_add')), 'Сгенерируется при сохранении')
        resp = self.client.post(reverse('admin:lessons_staffpin_add'), {'employee': 'Ассистент Анна', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:2000])
        staff = StaffPin.objects.get()
        self.assertEqual(staff.employee, 'Ассистент Анна')
        self.assertContains(self.client.get(reverse('admin:lessons_staffpin_changelist')), staff.code)

    def test_pins_are_four_digits_and_exported_per_branch(self):
        other = Branch.objects.create(name='Алма', slug='alma')
        Student.objects.create(full_name='Петров Пётр', branch=other)
        self.assertRegex(self.student.pin, r'^\d{4}$')
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        resp = self.client.post(reverse('admin:lessons_branch_changelist'), {
            'action': 'export_pins', '_selected_action': [self.branch.pk],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('pins_orbita.csv', resp['Content-Disposition'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn(f'1,Иванов Иван,Орбита,{self.student.pin}', content)
        self.assertNotIn('Петров', content)

    def test_unknown_token_404(self):
        self.assertEqual(self.client.get('/s/nope/').status_code, 404)

    def test_admin_adds_images_for_date_and_delete_removes_file(self):
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        resp = self.client.post(reverse('admin:lessons_student_change', args=[self.student.pk]), {
            'full_name': self.student.full_name, 'branch': self.branch.pk, 'external_id': 1, 'pin': '1234', 'is_active': 'on',
            'new_images_date': '15.09.2026', 'new_images': [png(), png(color='green')],
            'screenshots-TOTAL_FORMS': 0, 'screenshots-INITIAL_FORMS': 0,
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        shots = list(self.student.screenshots.all())
        self.assertEqual(len(shots), 2)
        self.assertTrue(all(s.lesson_date == datetime.date(2026, 9, 15) for s in shots))

        storage, name = shots[0].image.storage, shots[0].image.name
        self.assertTrue(storage.exists(name))
        Screenshot.objects.filter(pk=shots[0].pk).delete()
        self.assertFalse(storage.exists(name))

    def test_import_command_is_idempotent(self):
        from django.core.management import call_command
        call_command('import_students', stdout=io.StringIO())
        count = Student.objects.count()
        call_command('import_students', stdout=io.StringIO())
        self.assertEqual(Student.objects.count(), count)
        self.assertEqual(Branch.objects.count(), 4)

    def test_admin_import_puts_students_into_chosen_branch(self):
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        alma = Branch.objects.create(name='Алма', slug='alma')
        csv_file = SimpleUploadedFile(
            'new.csv', '﻿ID,ФИО\n1,"Иванов  Иван"\n500,"Петров Пётр"\n,\n'.encode(), content_type='text/csv',
        )
        url = reverse('admin:lessons_student_import')
        resp = self.client.post(url, {'import_file': csv_file, 'format': 0, 'branch': alma.pk, 'resource': 0})
        self.assertEqual(resp.status_code, 200)
        confirm = resp.context['confirm_form']
        self.assertIsNotNone(confirm, resp.content.decode()[:3000])
        self.assertFalse(Student.objects.filter(external_id=500).exists())  # превью ничего не сохраняет

        resp = self.client.post(reverse('admin:lessons_student_process_import'), confirm.initial)
        self.assertEqual(resp.status_code, 302)
        new = Student.objects.get(external_id=500)
        self.assertEqual((new.full_name, new.branch), ('Петров Пётр', alma))
        self.student.refresh_from_db()
        self.assertEqual(self.student.branch, alma)
        self.assertEqual(Student.objects.count(), 2)
