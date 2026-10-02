import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lessons.models import Branch, Student

BRANCH_NAMES = {
    'baitursyn': 'Байтурсын',
    'orbita': 'Орбита',
    'komfort': 'Комфорт',
    'alma': 'Алма',
}


class Command(BaseCommand):
    help = 'Импортирует учеников из CSV-файлов students_<филиал>.csv (колонки ID, ФИО)'

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=str(settings.STUDENTS_CSV_DIR), help='Папка с CSV-файлами')
        parser.add_argument(
            '--deactivate-missing',
            action='store_true',
            help='Сделать неактивными учеников импортированного филиала, которых нет в его CSV',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        csv_dir = Path(options['dir'])
        files = sorted(csv_dir.glob('students_*.csv'))
        if not files:
            raise CommandError(f'В {csv_dir} нет файлов students_*.csv')

        for path in files:
            slug = path.stem.removeprefix('students_')
            branch, _ = Branch.objects.get_or_create(slug=slug, defaults={'name': BRANCH_NAMES.get(slug, slug.title())})

            created = updated = 0
            seen_ids = set()
            with path.open(encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    external_id = int(row['ID'])
                    full_name = ' '.join(row['ФИО'].split())
                    if not full_name:
                        continue
                    seen_ids.add(external_id)
                    student, is_new = Student.objects.get_or_create(
                        external_id=external_id,
                        defaults={'full_name': full_name, 'branch': branch},
                    )
                    if is_new:
                        created += 1
                    elif (student.full_name, student.branch_id, student.is_active) != (full_name, branch.id, True):
                        student.full_name, student.branch, student.is_active = full_name, branch, True
                        student.save(update_fields=['full_name', 'branch', 'is_active'])
                        updated += 1

            deactivated = 0
            if options['deactivate_missing']:
                deactivated = (
                    Student.objects.filter(branch=branch, is_active=True, external_id__isnull=False)
                    .exclude(external_id__in=seen_ids)
                    .update(is_active=False)
                )

            self.stdout.write(
                f'{branch.name}: добавлено {created}, обновлено {updated}'
                + (f', деактивировано {deactivated}' if options['deactivate_missing'] else '')
            )
