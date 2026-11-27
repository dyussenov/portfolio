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

from .models import Branch, Screenshot, Student, StudyGroup

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

    def sync(self, customers, groups=(), members=None, **options):
        from django.core.management import call_command
        members = members or {}
        with patch('lessons.management.commands.sync_alfacrm.AlfaCRMClient') as client_class:
            client = client_class.return_value
            client.branches.return_value = [{'id': 2, 'name': 'Филиал Орбита'}, {'id': 3, 'name': 'Другой'}]
            client.students.side_effect = lambda branch_id: iter(customers if branch_id == 2 else [])
            client.groups.side_effect = lambda branch_id: list(groups) if branch_id == 2 else []
            client.group_members.side_effect = lambda branch_id, group_id: members.get(group_id, [])
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

    @patch('lessons.management.commands.sync_alfacrm.timezone.localdate', return_value=datetime.date(2026, 11, 24))
    def test_groups_and_memberships_come_from_crm(self, _):
        customers = [{'id': 1, 'name': 'Иванов Иван'}, {'id': 2, 'name': 'Петров Пётр'}]
        groups = [{'id': 10, 'name': 'младшая_суббота_09.30'}, {'id': 11, 'name': 'старшая  воскресенье'}]
        members = {
            10: [{'customer_id': 1, 'e_date': '31.05.2027'}, {'customer_id': 2, 'e_date': '21.09.2026'}],
            11: [{'customer_id': 2, 'e_date': ''}, {'customer_id': 999, 'e_date': '31.05.2027'}],
        }
        _, out, _ = self.sync(customers, groups, members)
        self.assertFalse(StudyGroup.objects.exists())
        self.assertIn('групп новых 2', out)

        self.sync(customers, groups, members, apply=True)
        self.assertEqual(StudyGroup.objects.get(external_id=11).name, 'старшая воскресенье')
        self.assertEqual(StudyGroup.objects.get(external_id=10).branch, self.branch)
        self.assertEqual([g.external_id for g in self.student.groups.all()], [10])
        # Петрова вывели из группы 10 в сентябре — остаётся только 11
        self.assertEqual([g.external_id for g in Student.objects.get(external_id=2).groups.all()], [11])

        _, out, _ = self.sync(customers, groups, members, apply=True)
        self.assertIn('групп новых 0, групп обновить 0, групп закрыть 0, учеников со сменой групп 0', out)

        # Группу 11 переименовали, группу 10 удалили в CRM, Иванова перевели в 11
        groups = [{'id': 11, 'name': 'старшая_воскресенье_14.20'}]
        _, out, _ = self.sync(customers, groups, {11: [{'customer_id': 1}, {'customer_id': 2}]}, apply=True)
        self.assertIn('групп обновить 1, групп закрыть 1, учеников со сменой групп 1', out)
        self.assertFalse(StudyGroup.objects.get(external_id=10).is_active)
        self.assertEqual(StudyGroup.objects.get(external_id=11).name, 'старшая_воскресенье_14.20')
        self.assertEqual([g.external_id for g in self.student.groups.all()], [11])

    def test_client_keeps_only_groups_of_requested_branch(self):
        from .alfacrm import AlfaCRMClient
        client = AlfaCRMClient('demo.s20.online', 'a@a.a', 'key')
        page = {'total': 2, 'items': [{'id': 1, 'branch_ids': [2]}, {'id': 2, 'branch_ids': [1]}]}
        with patch.object(client, 'post', return_value=page):
            self.assertEqual([g['id'] for g in client.groups(2)], [1])
        with patch.object(client, 'post', return_value={'total': 1, 'items': [{'customer_id': 5}]}) as post:
            self.assertEqual(client.group_members(2, 10), [{'customer_id': 5}])
        self.assertEqual(post.call_args.args[0], '2/cgi/index')
        self.assertEqual(post.call_args.args[2], {'group_id': 10})

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

    def make_tutor(self, password='Tutor-pass-123'):
        from django.contrib.auth.models import Group
        from .models import TUTOR_GROUP, Tutor
        tutor = Tutor.objects.create_user('tutor', password=password, first_name='Тьютор', last_name='Таня', is_staff=True)
        tutor.groups.add(Group.objects.get(name=TUTOR_GROUP))
        return tutor

    def test_admin_creates_tutor_who_can_log_in(self):
        from .models import Tutor
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        resp = self.client.post(reverse('admin:lessons_tutor_add'), {
            'username': 'tanya', 'first_name': 'Таня', 'last_name': '', 'usable_password': 'true',
            'password1': 'Very-strong-77', 'password2': 'Very-strong-77',
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        tutor = Tutor.objects.get(username='tanya')
        self.assertTrue(tutor.is_staff)
        self.assertFalse(tutor.is_superuser)
        self.assertContains(self.client.get(reverse('admin:lessons_tutor_changelist')), 'class="action-select"', count=1)

        self.client.logout()
        self.assertTrue(self.client.login(username='tanya', password='Very-strong-77'))
        self.assertEqual(self.client.get(reverse('admin:lessons_student_changelist')).status_code, 200)

    def test_tutor_only_views_students_and_screenshots(self):
        shot = Screenshot.objects.create(student=self.student, image=png())
        self.make_tutor()
        self.client.login(username='tutor', password='Tutor-pass-123')

        index = self.client.get(reverse('admin:index'))
        self.assertContains(index, reverse('admin:lessons_student_changelist'))
        self.assertContains(index, reverse('admin:lessons_screenshot_changelist'))
        for name in ('branch', 'staffpin', 'tutor'):
            self.assertNotContains(index, reverse(f'admin:lessons_{name}_changelist'))
            self.assertEqual(self.client.get(reverse(f'admin:lessons_{name}_changelist')).status_code, 403)

        changelist = self.client.get(reverse('admin:lessons_student_changelist'))
        self.assertContains(changelist, 'Иванов Иван')
        self.assertNotContains(changelist, 'Синхронизация с AlfaCRM')
        self.assertNotContains(changelist, reverse('admin:lessons_student_import'))
        self.assertNotContains(changelist, 'delete_selected')
        self.assertNotContains(changelist, reverse('admin:lessons_student_add'))
        self.assertEqual(self.client.get(reverse('admin:lessons_student_import')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:lessons_student_alfacrm_sync')).status_code, 403)

        change_url = reverse('admin:lessons_student_change', args=[self.student.pk])
        page = self.client.get(change_url)
        self.assertContains(page, 'Иванов Иван')
        self.assertContains(page, shot.image.url)
        self.assertNotContains(page, 'name="new_images"')
        self.assertNotContains(page, 'name="_save"')
        resp = self.client.post(change_url, {
            'full_name': 'Взломан', 'branch': self.branch.pk, 'pin': '1234',
            'screenshots-TOTAL_FORMS': 0, 'screenshots-INITIAL_FORMS': 0,
        })
        self.assertEqual(resp.status_code, 403)
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, 'Иванов Иван')

        self.assertContains(self.client.get(reverse('admin:lessons_screenshot_changelist')), 'Иванов Иван')
        self.assertEqual(self.client.get(reverse('admin:lessons_screenshot_change', args=[shot.pk])).status_code, 200)
        for url in (
            reverse('admin:lessons_screenshot_delete', args=[shot.pk]),
            reverse('admin:lessons_student_delete', args=[self.student.pk]),
            reverse('admin:lessons_screenshot_add'),
        ):
            self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403, url)
        self.assertTrue(Screenshot.objects.filter(pk=shot.pk).exists())

    def test_tutor_password_works_as_secret_pin(self):
        tutor = self.make_tutor()
        url = reverse('lessons:upload_screenshots')
        resp = self.client.post(url, {'student': self.student.pk, 'pin': 'Tutor-pass-123', 'images': [png()]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Screenshot.objects.get().uploaded_by_tutor, tutor)

        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        self.assertContains(self.client.get(reverse('admin:lessons_screenshot_changelist')), 'tutor')

        # Пароль обычного сотрудника админки и отключённого тьютора не подходят
        self.assertEqual(self.client.post(url, {'student': self.student.pk, 'pin': 'pass', 'images': [png()]}).status_code, 403)
        tutor.is_active = False
        tutor.save()
        resp = self.client.post(url, {'student': self.student.pk, 'pin': 'Tutor-pass-123', 'images': [png()]})
        self.assertEqual(resp.status_code, 403)

    def test_tutor_adds_quick_link_that_shows_on_home_page_until_expired(self):
        from django.utils import timezone
        from .models import QuickLink
        tutor = self.make_tutor()
        self.client.login(username='tutor', password='Tutor-pass-123')
        self.assertContains(self.client.get(reverse('admin:index')), reverse('admin:lessons_quicklink_changelist'))
        resp = self.client.post(reverse('admin:lessons_quicklink_add'), {
            'title': 'Тест по Scratch', 'url': 'https://forms.example.com/scratch', 'lifetime': 30,
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        link = QuickLink.objects.get()
        self.assertEqual(link.created_by, tutor)
        minutes_left = (link.expires_at - timezone.now()).total_seconds() / 60
        self.assertTrue(29 < minutes_left <= 30, minutes_left)

        self.client.logout()
        home = self.client.get(reverse('lessons:upload'))
        self.assertContains(home, 'Тест по Scratch')
        self.assertContains(home, 'href="https://forms.example.com/scratch"')
        api = self.client.get(reverse('lessons:quick_links')).json()
        added = f'добавлена в {timezone.localtime(link.created_at):%H:%M}'
        self.assertContains(home, added)
        self.assertEqual(api['links'], [{'id': link.pk, 'title': 'Тест по Scratch', 'url': 'https://forms.example.com/scratch', 'added': added}])

        QuickLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertNotContains(self.client.get(reverse('lessons:upload')), 'Тест по Scratch')
        self.assertEqual(self.client.get(reverse('lessons:quick_links')).json()['links'], [])

    def test_quick_link_added_label_mentions_date_when_not_today(self):
        from django.utils import timezone
        from .views import added_label
        moment = timezone.make_aware(datetime.datetime(2026, 11, 18, 9, 5))
        self.assertEqual(added_label(moment, datetime.date(2026, 11, 18)), 'добавлена в 09:05')
        self.assertEqual(added_label(moment, datetime.date(2026, 11, 19)), 'добавлена 18 ноября в 09:05')

    def test_quick_link_can_be_removed_early_and_rejects_non_urls(self):
        from django.utils import timezone
        from .models import QuickLink
        self.make_tutor()
        self.client.login(username='tutor', password='Tutor-pass-123')
        resp = self.client.post(reverse('admin:lessons_quicklink_add'), {'title': '', 'url': 'javascript:alert(1)', 'lifetime': 15})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(QuickLink.objects.exists())

        link = QuickLink.objects.create(url='https://example.com/a', expires_at=timezone.now() + datetime.timedelta(hours=1))
        self.assertContains(self.client.get(reverse('lessons:upload')), 'https://example.com/a')
        self.client.post(reverse('admin:lessons_quicklink_changelist'), {'action': 'expire_now', '_selected_action': [link.pk]})
        self.assertFalse(QuickLink.objects.alive().exists())
        self.assertNotContains(self.client.get(reverse('lessons:upload')), 'https://example.com/a')

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


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class UploadReportTests(TestCase):
    def setUp(self):
        self.orbita = Branch.objects.create(name='Орбита', slug='orbita')
        self.alma = Branch.objects.create(name='Алма', slug='alma')
        self.junior = StudyGroup.objects.create(external_id=1, name='младшая_суббота', branch=self.orbita)
        self.senior = StudyGroup.objects.create(external_id=2, name='старшая_воскресенье', branch=self.orbita)
        StudyGroup.objects.create(external_id=3, name='закрытая', branch=self.orbita, is_active=False)
        self.alma_group = StudyGroup.objects.create(external_id=4, name='средняя_пн', branch=self.alma)

    def student(self, name, branch, *groups):
        student = Student.objects.create(full_name=name, branch=branch)
        student.groups.set(groups)
        return student

    def shots(self, student, day, count=1):
        for _ in range(count):
            Screenshot.objects.create(student=student, lesson_date=datetime.date(2026, 11, day), image='x.png')

    def test_report_groups_by_branch_and_respects_range(self):
        from .reports import upload_report
        self.shots(self.student('А', self.orbita, self.junior), 21, 3)
        self.shots(self.student('Б', self.orbita, self.junior, self.senior), 22, 2)  # в двух группах
        self.shots(self.student('В', self.orbita), 16)  # без группы, начало периода
        self.shots(self.student('Г', self.alma, self.alma_group), 15)  # до периода
        self.shots(self.student('Д', self.alma, self.alma_group), 23)  # после периода

        report = upload_report(datetime.date(2026, 11, 16), datetime.date(2026, 11, 22))
        self.assertEqual(report, '\n'.join([
            'Отчёт по загруженным картинкам',
            'Период: 16.11.2026 – 22.11.2026',
            '',
            'Алма — 0',
            '• средняя_пн — 0',
            '',
            'Орбита — 6',
            '• младшая_суббота — 5',
            '• старшая_воскресенье — 2',
            '• без группы — 1',
            '',
            'Итого: 6',
        ]))

    def test_closed_group_shown_only_with_uploads(self):
        from .reports import upload_report
        closed = StudyGroup.objects.get(external_id=3)
        self.shots(self.student('А', self.orbita, closed), 20)
        self.assertIn('• закрытая — 1', upload_report(datetime.date(2026, 11, 20), datetime.date(2026, 11, 20)))
        self.assertNotIn('закрытая', upload_report(datetime.date(2026, 11, 21), datetime.date(2026, 11, 21)))

    def test_report_page_for_admin_and_tutor(self):
        from django.contrib.auth.models import Group
        from .models import TUTOR_GROUP, Tutor
        self.shots(self.student('А', self.orbita, self.junior), 21, 2)
        url = reverse('admin:lessons_screenshot_report')
        self.assertEqual(self.client.get(url).status_code, 302)

        tutor = Tutor.objects.create_user('tutor', password='Tutor-pass-123', is_staff=True)
        tutor.groups.add(Group.objects.get(name=TUTOR_GROUP))
        self.client.login(username='tutor', password='Tutor-pass-123')
        self.assertContains(self.client.get(reverse('admin:lessons_screenshot_changelist')), url)
        self.assertContains(self.client.get(reverse('admin:lessons_studygroup_changelist')), 'младшая_суббота')

        page = self.client.get(url)
        self.assertContains(page, 'type="date"')
        self.assertIsNone(page.context['report'])
        page = self.client.get(url, {'date_from': '2026-11-16', 'date_to': '2026-11-22'})
        self.assertContains(page, '• младшая_суббота — 2')
        self.assertContains(page, 'Период: 16.11.2026 – 22.11.2026')
        page = self.client.get(url, {'date_from': '2026-11-22', 'date_to': '2026-11-16'})
        self.assertContains(page, 'Дата начала позже даты окончания')
        self.assertIsNone(page.context['report'])


@override_settings(ALFACRM_HOST='demo.s20.online', ALFACRM_EMAIL='a@a.a', ALFACRM_API_KEY='key')
class TrialLessonTests(TestCase):
    CUSTOMER = {
        'id': 3388, 'branch_ids': [1], 'name': 'Хван  Артур', 'dob': '28.05.2013',
        'phone': ['+7(708)764-38-54'], 'legal_name': 'S', 'custom_parent_name': 'Хван Анна', 'custom_parent_phone': '',
    }
    LINK = 'https://demo.s20.online/company/1/customer/view?id=3388'

    def setUp(self):
        from django.contrib.auth.models import Group
        from .models import MANAGER_GROUP, TUTOR_GROUP, Manager, Tutor
        self.tutor = Tutor.objects.create_user('tutor', password='Tutor-pass-123', first_name='Таня', is_staff=True)
        self.tutor.groups.add(Group.objects.get(name=TUTOR_GROUP))
        self.other_tutor = Tutor.objects.create_user('other', password='Other-pass-123', is_staff=True)
        self.other_tutor.groups.add(Group.objects.get(name=TUTOR_GROUP))
        self.manager = Manager.objects.create_user('manager', password='Manager-pass-123', is_staff=True)
        self.manager.groups.add(Group.objects.get(name=MANAGER_GROUP))

    def crm(self):
        patcher = patch('lessons.trials.AlfaCRMClient')
        client = patcher.start().return_value
        self.addCleanup(patcher.stop)
        client.customer.side_effect = lambda branch_id, customer_id: self.CUSTOMER if customer_id == 3388 else None
        client.branches.return_value = [{'id': 7}, {'id': 1}]
        return client

    def create_trial(self, link=None):
        self.client.login(username='manager', password='Manager-pass-123')
        return self.client.post(reverse('admin:lessons_triallesson_add'), {
            'crm_url': link or self.LINK, 'lesson_date': '29.11.2026', 'tutor': self.tutor.pk,
        })

    def test_parse_crm_link(self):
        from .trials import TrialLookupError, parse_crm_link
        self.assertEqual(parse_crm_link(self.LINK), (1, 3388))
        self.assertEqual(parse_crm_link(' 3388 '), (None, 3388))
        self.assertEqual(parse_crm_link('demo.s20.online/company/3/customer/view?id=5&tab=1'), (3, 5))
        for bad in ('https://evil.example/company/1/customer/view?id=1', 'https://demo.s20.online/company/1/group/view?id=1', 'Артур'):
            with self.assertRaises(TrialLookupError):
                parse_crm_link(bad)

    def test_whatsapp_url_normalizes_phone(self):
        from .trials import whatsapp_url
        self.assertEqual(whatsapp_url('8 (708) 764-38-54', 'Привет'), 'https://wa.me/77087643854?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82')
        self.assertTrue(whatsapp_url('7087643854', 'x').startswith('https://wa.me/77087643854'))
        self.assertIsNone(whatsapp_url('', 'x'))

    def test_manager_creates_trial_and_data_comes_from_crm(self):
        from .models import TrialLesson
        client = self.crm()
        resp = self.create_trial()
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        trial = TrialLesson.objects.get()
        self.assertEqual(
            (trial.full_name, trial.birth_date, trial.age, trial.parent_name, trial.parent_phone, trial.crm_branch_id),
            ('Хван Артур', datetime.date(2013, 5, 28), 13, 'Хван Анна', '+7(708)764-38-54', 1),
        )
        self.assertEqual(trial.created_by_id, self.manager.pk)
        self.assertEqual(trial.status, 'ждём ОС от тьютора')
        client.customer.assert_called_once_with(1, 3388)

        # Ссылка без филиала: ищем по всем филиалам CRM
        self.assertEqual(self.create_trial('3388').status_code, 302)

    def test_unknown_or_foreign_link_shows_error(self):
        from .models import TrialLesson
        self.crm()
        resp = self.create_trial('https://demo.s20.online/company/1/customer/view?id=1')
        self.assertContains(resp, 'не найден')
        resp = self.create_trial('https://other.s20.online/company/1/customer/view?id=3388')
        self.assertContains(resp, 'не на нашу AlfaCRM')
        self.assertFalse(TrialLesson.objects.exists())

    def test_tutor_fills_feedback_only_for_own_trials(self):
        from .models import TrialLesson
        self.crm()
        self.create_trial()
        trial = TrialLesson.objects.get()
        foreign = TrialLesson.objects.create(
            crm_url='1', crm_customer_id=1, crm_branch_id=1, full_name='Чужой', lesson_date=trial.lesson_date,
            tutor=self.other_tutor,
        )
        self.client.logout()
        self.client.login(username='tutor', password='Tutor-pass-123')
        changelist = self.client.get(reverse('admin:lessons_triallesson_changelist'))
        self.assertContains(changelist, 'Хван Артур')
        self.assertNotContains(changelist, 'Чужой')
        self.assertNotContains(changelist, reverse('admin:lessons_triallesson_add'))
        self.assertEqual(self.client.get(reverse('admin:lessons_triallesson_change', args=[foreign.pk])).status_code, 302)

        url = reverse('admin:lessons_triallesson_change', args=[trial.pk])
        page = self.client.get(url)
        self.assertContains(page, 'name="activities"')
        self.assertNotContains(page, 'name="feedback"')
        self.assertNotContains(page, 'name="crm_url"')
        self.assertNotContains(page, '+7(708)')  # телефон родителя тьютору не нужен
        resp = self.client.post(url, {
            'topic': 'Roblox', 'activities': 'Собрал обби', 'recommended_group': 'старшая', 'makeup_hours': '0',
            'lesson_date': '01.01.2030',
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        trial.refresh_from_db()
        self.assertIn('Собрал обби', trial.feedback)
        self.assertIn('Не нужны', trial.feedback)
        self.assertEqual(trial.lesson_date, datetime.date(2026, 11, 29))
        self.assertIsNotNone(trial.feedback_at)
        self.assertEqual(self.client.get(reverse('admin:lessons_triallesson_send', args=[trial.pk])).status_code, 403)

    def test_manager_sends_feedback_to_parent(self):
        from .models import TrialLesson
        self.crm()
        self.create_trial()
        trial = TrialLesson.objects.get()
        send = reverse('admin:lessons_triallesson_send', args=[trial.pk])
        change = reverse('admin:lessons_triallesson_change', args=[trial.pk])
        self.assertContains(self.client.get(change), 'Появится, когда тьютор заполнит')
        self.assertRedirects(self.client.get(send), change)

        TrialLesson.objects.filter(pk=trial.pk).update(feedback='Отлично позанимались', feedback_at=datetime.datetime.now(datetime.UTC))
        self.assertContains(self.client.get(change), send)
        resp = self.client.get(send)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('https://wa.me/77087643854?text='))
        trial.refresh_from_db()
        self.assertEqual(trial.sent_by_id, self.manager.pk)
        self.assertEqual(trial.status, 'отправлено родителю')
        filtered = self.client.get(reverse('admin:lessons_triallesson_changelist'), {'status': 'sent'})
        self.assertContains(filtered, 'Хван Артур')

    def test_manager_sees_only_trials_and_admin_creates_manager(self):
        self.client.login(username='manager', password='Manager-pass-123')
        index = self.client.get(reverse('admin:index'))
        self.assertContains(index, reverse('admin:lessons_triallesson_changelist'))
        for name in ('student', 'screenshot', 'tutor', 'manager', 'branch'):
            self.assertNotContains(index, reverse(f'admin:lessons_{name}_changelist'))

        from .models import Manager
        self.client.logout()
        User.objects.create_superuser('admin', 'a@a.a', 'pass')
        self.client.login(username='admin', password='pass')
        resp = self.client.post(reverse('admin:lessons_manager_add'), {
            'username': 'dana', 'first_name': 'Дана', 'last_name': '', 'usable_password': 'true',
            'password1': 'Very-strong-77', 'password2': 'Very-strong-77',
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000])
        dana = Manager.objects.get(username='dana')
        self.assertTrue(dana.is_staff)
        self.assertFalse(dana.is_superuser)


@override_settings(ALFACRM_HOST='demo.s20.online', ALFACRM_EMAIL='a@a.a', ALFACRM_API_KEY='key')
class StaffPagesTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        from .models import MANAGER_GROUP, TUTOR_GROUP, Manager, Tutor
        self.tutor = Tutor.objects.create_user('tutor', password='Tutor-pass-123', first_name='Таня', is_staff=True)
        self.tutor.groups.add(Group.objects.get(name=TUTOR_GROUP))
        self.other = Tutor.objects.create_user('other', password='Other-pass-123', is_staff=True)
        self.other.groups.add(Group.objects.get(name=TUTOR_GROUP))
        self.manager = Manager.objects.create_user('manager', password='Manager-pass-123', is_staff=True)
        self.manager.groups.add(Group.objects.get(name=MANAGER_GROUP))
        patcher = patch('lessons.trials.AlfaCRMClient')
        self.crm = patcher.start().return_value
        self.addCleanup(patcher.stop)
        customers = {
            1: {'id': 1, 'branch_ids': [1], 'name': 'Хван Артур', 'dob': '28.05.2013', 'phone': ['87087643854']},
            2: {'id': 2, 'branch_ids': [1], 'name': 'Ли Мария', 'dob': '', 'phone': []},
        }
        self.crm.customer.side_effect = lambda branch_id, customer_id: customers.get(customer_id)

    def post(self, name, body, *args):
        import json
        return self.client.post(reverse(name, args=args), json.dumps(body), content_type='application/json')

    def link(self, customer_id):
        return f'https://demo.s20.online/company/1/customer/view?id={customer_id}'

    def make_trial(self, customer_id=1, tutor=None, day=datetime.date(2026, 11, 21), **kwargs):
        from .models import TrialLesson
        trial = TrialLesson(crm_url=self.link(customer_id), lesson_date=day, tutor=tutor or self.tutor, **kwargs)
        trial.apply_crm_data({
            'crm_customer_id': customer_id, 'crm_branch_id': 1, 'full_name': f'Ученик {customer_id}',
            'birth_date': None, 'parent_name': '', 'parent_phone': '87087643854',
        })
        trial.save()
        return trial

    def test_home_has_staff_button_and_login_routes_by_role(self):
        home = self.client.get(reverse('lessons:upload'))
        self.assertContains(home, 'Я сотрудник')
        resp = self.client.get(reverse('lessons:staff_home'))
        self.assertRedirects(resp, reverse('lessons:staff_login') + '?next=' + reverse('lessons:staff_home'))

        login = reverse('lessons:staff_login')
        self.assertContains(self.client.post(login, {'username': 'manager', 'password': 'wrong'}), 'Неверный логин или пароль')
        resp = self.client.post(login, {'username': 'manager', 'password': 'Manager-pass-123'}, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('lessons:staff_trials'))
        self.client.post(reverse('lessons:staff_logout'))

        resp = self.client.post(login, {'username': 'tutor', 'password': 'Tutor-pass-123'}, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('lessons:staff_feedback'))
        self.assertEqual(self.client.get(reverse('lessons:staff_trials')).status_code, 403)
        self.assertEqual(self.post('lessons:staff_trials_create', {'rows': []}).status_code, 403)

    def test_manager_creates_many_trials_at_once(self):
        from .models import TrialLesson
        self.client.login(username='manager', password='Manager-pass-123')
        page = self.client.get(reverse('lessons:staff_trials'))
        self.assertContains(page, 'Записать на пробные')
        self.assertContains(page, 'Таня')

        resp = self.post('lessons:staff_trial_lookup', {'link': self.link(1)})
        self.assertEqual(resp.json()['full_name'], 'Хван Артур')
        self.assertEqual(self.post('lessons:staff_trial_lookup', {'link': self.link(9)}).status_code, 400)

        rows = [
            {'link': self.link(1), 'date': '2026-11-28', 'tutor': self.tutor.pk},
            {'link': self.link(2), 'date': '2026-11-29', 'tutor': self.other.pk},
            {'link': self.link(9), 'date': '2026-11-29', 'tutor': self.other.pk},
        ]
        resp = self.post('lessons:staff_trials_create', {'rows': rows})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(list(resp.json()['errors']), ['2'])
        self.assertFalse(TrialLesson.objects.exists())  # всё или ничего

        resp = self.post('lessons:staff_trials_create', {'rows': rows[:2]})
        self.assertEqual(resp.json(), {'created': 2})
        artur = TrialLesson.objects.get(crm_customer_id=1)
        self.assertEqual((artur.full_name, artur.age, artur.tutor, artur.created_by_id), ('Хван Артур', 13, self.tutor, self.manager.pk))
        self.assertContains(self.client.get(reverse('lessons:staff_trials')), 'Записано на пробные: 2')

        # Повторно на ту же дату записать нельзя
        resp = self.post('lessons:staff_trials_create', {'rows': rows[:1]})
        self.assertIn('уже записан', resp.json()['errors']['0'])
        self.assertEqual(self.post('lessons:staff_trial_lookup', {'link': self.link(1)}).json()['previous'][0]['label'], '28.11.2026')

    def test_manager_edits_deletes_and_sends(self):
        from .models import TrialLesson
        self.client.login(username='manager', password='Manager-pass-123')
        trial = self.make_trial()
        resp = self.post('lessons:staff_trial_update', {'date': '2026-11-22', 'tutor': self.other.pk}, trial.pk)
        self.assertEqual(resp.status_code, 200)
        trial.refresh_from_db()
        self.assertEqual((trial.lesson_date, trial.tutor), (datetime.date(2026, 11, 22), self.other))

        self.assertEqual(self.post('lessons:staff_trial_sent', {}, trial.pk).status_code, 400)  # ОС ещё нет
        TrialLesson.objects.filter(pk=trial.pk).update(feedback='Молодец', feedback_at=datetime.datetime.now(datetime.UTC))
        page = self.client.get(reverse('lessons:staff_trials'))
        self.assertContains(page, 'https://wa.me/77087643854?text=')
        self.assertEqual(self.post('lessons:staff_trial_sent', {}, trial.pk).json(), {'sent': True})
        trial.refresh_from_db()
        self.assertEqual(trial.sent_by_id, self.manager.pk)
        self.assertEqual(self.post('lessons:staff_trial_delete', {}, trial.pk).status_code, 400)
        self.post('lessons:staff_trial_sent', {'undo': True}, trial.pk)
        self.assertEqual(self.post('lessons:staff_trial_delete', {}, trial.pk).status_code, 200)
        self.assertFalse(TrialLesson.objects.exists())

    @patch('lessons.staff_views.timezone.localdate', return_value=datetime.date(2026, 11, 22))
    def test_tutor_writes_draft_then_submits(self, _):
        from .models import TrialLesson
        mine = self.make_trial(1)
        future = self.make_trial(2, day=datetime.date(2026, 11, 29))
        foreign = self.make_trial(3, tutor=self.other)
        self.client.login(username='tutor', password='Tutor-pass-123')
        page = self.client.get(reverse('lessons:staff_feedback'))
        self.assertContains(page, 'Ученик 1')
        self.assertContains(page, 'Ученик 2')  # в предстоящих
        self.assertNotContains(page, 'Ученик 3')
        self.assertEqual([t.pk for t in page.context['todo']], [mine.pk])
        self.assertEqual([t.pk for t in page.context['upcoming']], [future.pk])

        self.assertEqual(self.post('lessons:staff_feedback_save', {'feedback': 'x'}, foreign.pk).status_code, 404)

        resp = self.post('lessons:staff_feedback_save', {'topic': 'Roblox Studio'}, mine.pk)
        self.assertEqual(resp.json()['status'], 'тьютор пишет ОС')
        self.assertIn('Roblox Studio', resp.json()['feedback'])
        mine.refresh_from_db()
        self.assertIsNone(mine.feedback_at)
        # Черновик менеджер не видит в «готово к отправке»
        self.client.logout()
        self.client.login(username='manager', password='Manager-pass-123')
        self.assertEqual(self.client.get(reverse('lessons:staff_trials')).context['ready'], [])
        self.client.logout()
        self.client.login(username='tutor', password='Tutor-pass-123')

        # «Готово» без всех полей не принимается
        resp = self.post('lessons:staff_feedback_save', {'topic': 'Roblox Studio', 'done': True}, mine.pk)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['missing'], ['activities', 'recommended_group', 'makeup_hours'])
        self.assertIn('что делали', resp.json()['error'])

        fields = {'topic': 'Roblox Studio', 'activities': 'Собрали обби', 'recommended_group': 'старшая, сб 14:20', 'makeup_hours': '2'}
        resp = self.post('lessons:staff_feedback_save', {**fields, 'done': True}, mine.pk)
        self.assertTrue(resp.json()['done'])
        mine.refresh_from_db()
        self.assertEqual((mine.topic, mine.makeup_hours), ('Roblox Studio', '2'))
        self.assertIn('Нужно отработать 2 часа', mine.feedback)
        self.assertIsNotNone(mine.feedback_at)
        self.assertEqual([t.pk for t in self.client.get(reverse('lessons:staff_feedback')).context['done']], [mine.pk])
        self.assertEqual(self.post('lessons:staff_feedback_save', {**fields, 'topic': 'x' * 300}, mine.pk).json()['field'], 'topic')

        TrialLesson.objects.filter(pk=mine.pk).update(sent_at=datetime.datetime.now(datetime.UTC))
        self.assertEqual(self.post('lessons:staff_feedback_save', {'topic': 'Новое'}, mine.pk).status_code, 409)

    def test_legacy_free_text_feedback_shown_in_activities(self):
        trial = self.make_trial(1, feedback='Старый текст ОС')
        self.client.login(username='tutor', password='Tutor-pass-123')
        page = self.client.get(reverse('lessons:staff_feedback'))
        self.assertContains(page, '>Старый текст ОС</textarea>')

    def test_compose_feedback(self):
        from .models import TrialLesson, makeup_label
        trial = TrialLesson(full_name='Хван Артур', lesson_date=datetime.date(2026, 11, 22))
        self.assertEqual(trial.compose_feedback(), '')
        trial.topic, trial.activities = 'Roblox Studio', 'собрали обби'
        trial.recommended_group, trial.makeup_hours = 'старшая, сб 14:20', '2'
        self.assertEqual(trial.compose_feedback(), '\n'.join([
            'Здравствуйте! Обратная связь по пробному занятию 22 ноября — Артур',
            '', '📌 *Тема*', 'Roblox Studio',
            '', '🛠 *Что делали*', 'Собрали обби',
            '', '🎯 *Рекомендуем группу*', 'Старшая, сб 14:20',
            '', '⏱ *Отработки*', 'Нужно отработать 2 часа',
        ]))
        self.assertEqual(
            [makeup_label(v) for v in ('0', '1', '4', '5', '21', '1,5', 'по договорённости')],
            ['не нужны', 'нужно отработать 1 час', 'нужно отработать 4 часа', 'нужно отработать 5 часов',
             'нужно отработать 21 час', 'нужно отработать 1,5 часа', 'по договорённости'],
        )
