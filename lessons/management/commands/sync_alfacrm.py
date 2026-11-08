import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lessons.alfacrm import AlfaCRMClient, AlfaCRMError
from lessons.models import Branch, Student

PLACEHOLDER_HOST = 'results.example.kz'


def find_local_branch(crm_name, local_branches):
    crm_name = crm_name.casefold()
    matches = [b for b in local_branches if b.name.casefold() in crm_name or b.slug.casefold() in crm_name]
    return matches[0] if len(matches) == 1 else None


def parse_birth_date(value):
    return datetime.datetime.strptime(value.strip(), '%d.%m.%Y').date()


class Command(BaseCommand):
    help = (
        'Получает учеников из AlfaCRM, добавляет новых, обновляет ФИО и даты рождения '
        'и записывает в карточки CRM ссылки на портфолио. Без --apply только показывает план.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Применить изменения (иначе — пробный запуск)')
        parser.add_argument('--show-fields', action='store_true', help='Показать поля первого ученика из CRM и выйти')

    def handle(self, *args, **options):
        required = ['ALFACRM_HOST', 'ALFACRM_EMAIL', 'ALFACRM_API_KEY']
        missing = [name for name in required if not getattr(settings, name)]
        if missing:
            raise CommandError('Не заданы переменные окружения: ' + ', '.join(missing))

        client = AlfaCRMClient(settings.ALFACRM_HOST, settings.ALFACRM_EMAIL, settings.ALFACRM_API_KEY)
        try:
            self.run(client, options)
        except AlfaCRMError as e:
            raise CommandError(f'Ошибка AlfaCRM: {e}') from e

    def run(self, client, options):
        crm_branches = client.branches()
        if options['show_fields']:
            for crm_branch in crm_branches:
                for customer in client.students(crm_branch['id']):
                    for key in sorted(customer):
                        self.stdout.write(key)
                    return
            raise CommandError('В CRM не найдено учеников')

        apply = options['apply']
        write_links = settings.ALFACRM_WRITE_LINKS
        field = settings.ALFACRM_LINK_FIELD
        base_url = settings.PUBLIC_BASE_URL.rstrip('/')
        if write_links:
            if not field or not base_url:
                raise CommandError('Не заданы ALFACRM_LINK_FIELD и/или PUBLIC_BASE_URL')
            if apply and PLACEHOLDER_HOST in base_url:
                raise CommandError(f'PUBLIC_BASE_URL указывает на заглушку {PLACEHOLDER_HOST}: ссылки не будут работать')

        local_branches = list(Branch.objects.all())
        students = {s.external_id: s for s in Student.objects.filter(external_id__isnull=False)}
        stats = dict.fromkeys(['created', 'renamed', 'birthdays', 'linked', 'unchanged'], 0)
        problems = []
        seen = set()

        for crm_branch in crm_branches:
            branch = find_local_branch(crm_branch['name'], local_branches)
            if branch is None:
                problems.append(f'филиал CRM «{crm_branch["name"]}» не сопоставлен с филиалом сайта — пропущен')
                continue

            for customer in client.students(crm_branch['id']):
                customer_id = customer['id']
                if customer_id in seen:
                    continue
                seen.add(customer_id)
                full_name = ' '.join((customer.get('name') or '').split())
                if not full_name:
                    problems.append(f'ученик CRM {customer_id}: пустое имя — пропущен')
                    continue

                birth_date = None
                if customer.get('dob'):
                    try:
                        birth_date = parse_birth_date(customer['dob'])
                    except ValueError:
                        problems.append(f'ученик CRM {customer_id}: не удалось разобрать дату рождения')

                student = students.get(customer_id)
                if student is None:
                    stats['created'] += 1
                    if not apply:
                        stats['linked'] += write_links
                        continue
                    student, _ = Student.objects.get_or_create(
                        external_id=customer_id,
                        defaults={'full_name': full_name, 'branch': branch, 'birth_date': birth_date},
                    )
                    students[customer_id] = student
                else:
                    changes = {}
                    if student.full_name != full_name:
                        changes['full_name'] = full_name
                        stats['renamed'] += 1
                    if birth_date and student.birth_date != birth_date:
                        changes['birth_date'] = birth_date
                        stats['birthdays'] += 1
                    if changes and apply:
                        Student.objects.filter(pk=student.pk).update(**changes)

                if not write_links:
                    continue

                link = f'{base_url}{student.get_absolute_url()}'
                current = customer.get(field)
                if current == link:
                    stats['unchanged'] += 1
                elif current:
                    problems.append(f'ученик CRM {customer_id}: в поле {field} другое значение — не перезаписано')
                else:
                    if apply:
                        try:
                            client.update_customer(crm_branch['id'], customer_id, {field: link})
                        except AlfaCRMError as e:
                            problems.append(str(e))
                            continue
                    stats['linked'] += 1

        for problem in problems:
            self.stderr.write(problem)
        self.stdout.write(
            ('Применено' if apply else 'Пробный запуск, ничего не изменено')
            + f': новых учеников {stats["created"]}, ссылок записать {stats["linked"]}, '
            f'ФИО обновить {stats["renamed"]}, дат рождения обновить {stats["birthdays"]}, '
            f'уже верных ссылок {stats["unchanged"]}, проблем {len(problems)}'
            + ('' if write_links else '. Ссылки в CRM в этом окружении не записываются')
        )
