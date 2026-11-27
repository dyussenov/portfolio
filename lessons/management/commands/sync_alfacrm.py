import datetime
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from lessons.alfacrm import AlfaCRMClient, AlfaCRMError
from lessons.models import Branch, Student, StudyGroup

PLACEHOLDER_HOST = 'results.example.kz'


def find_local_branch(crm_name, local_branches):
    crm_name = crm_name.casefold()
    matches = [b for b in local_branches if b.name.casefold() in crm_name or b.slug.casefold() in crm_name]
    return matches[0] if len(matches) == 1 else None


def parse_crm_date(value):
    return datetime.datetime.strptime(value.strip(), '%d.%m.%Y').date()


def is_current_member(record, today):
    # Ученик, выведенный из группы, остаётся в CRM с датой окончания в прошлом
    e_date = (record.get('e_date') or '').strip()
    if not e_date:
        return True
    try:
        return parse_crm_date(e_date) >= today
    except ValueError:
        return True


class Command(BaseCommand):
    help = (
        'Получает учеников и группы из AlfaCRM, добавляет новых, обновляет ФИО, даты рождения и группы, '
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
        students = {
            s.external_id: s for s in Student.objects.filter(external_id__isnull=False).prefetch_related('groups')
        }
        stats = dict.fromkeys([
            'created', 'renamed', 'birthdays', 'linked', 'unchanged',
            'groups_created', 'groups_updated', 'groups_closed', 'regrouped',
        ], 0)
        problems = []
        seen = set()
        matched_branches = []

        for crm_branch in crm_branches:
            branch = find_local_branch(crm_branch['name'], local_branches)
            if branch is None:
                problems.append(f'филиал CRM «{crm_branch["name"]}» не сопоставлен с филиалом сайта — пропущен')
                continue
            matched_branches.append((crm_branch['id'], branch))

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
                        birth_date = parse_crm_date(customer['dob'])
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

        self.sync_groups(client, matched_branches, students, apply, stats)

        for problem in problems:
            self.stderr.write(problem)
        self.stdout.write(
            ('Применено' if apply else 'Пробный запуск, ничего не изменено')
            + f': новых учеников {stats["created"]}, ссылок записать {stats["linked"]}, '
            f'ФИО обновить {stats["renamed"]}, дат рождения обновить {stats["birthdays"]}, '
            f'уже верных ссылок {stats["unchanged"]}, групп новых {stats["groups_created"]}, '
            f'групп обновить {stats["groups_updated"]}, групп закрыть {stats["groups_closed"]}, '
            f'учеников со сменой групп {stats["regrouped"]}, проблем {len(problems)}'
            + ('' if write_links else '. Ссылки в CRM в этом окружении не записываются')
        )

    def sync_groups(self, client, matched_branches, students, apply, stats):
        today = timezone.localdate()
        crm_groups = {}
        for crm_branch_id, branch in matched_branches:
            for group in client.groups(crm_branch_id):
                name = ' '.join((group.get('name') or '').split()) or f'Группа {group["id"]}'
                crm_groups.setdefault(group['id'], (crm_branch_id, name, branch))

        membership = defaultdict(set)
        for group_id, (crm_branch_id, _, _) in crm_groups.items():
            for record in client.group_members(crm_branch_id, group_id):
                if record.get('customer_id') and is_current_member(record, today):
                    membership[record['customer_id']].add(group_id)

        local = {g.external_id: g for g in StudyGroup.objects.all()}
        for group_id, (_, name, branch) in crm_groups.items():
            group = local.get(group_id)
            if group is None:
                stats['groups_created'] += 1
                if apply:
                    local[group_id] = StudyGroup.objects.create(external_id=group_id, name=name, branch=branch)
            elif (group.name, group.branch_id, group.is_active) != (name, branch.pk, True):
                stats['groups_updated'] += 1
                if apply:
                    group.name, group.branch, group.is_active = name, branch, True
                    group.save()
        for group_id, group in local.items():
            if group_id not in crm_groups and group.is_active:
                stats['groups_closed'] += 1
                if apply:
                    group.is_active = False
                    group.save(update_fields=['is_active'])

        for customer_id, student in students.items():
            target = membership.get(customer_id, set())
            if {g.external_id for g in student.groups.all()} == target:
                continue
            stats['regrouped'] += 1
            if apply:
                student.groups.set([local[group_id] for group_id in target])
