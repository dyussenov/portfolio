import datetime
import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Branch, Screenshot, Student

MEDIA_ROOT = tempfile.mkdtemp()


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
