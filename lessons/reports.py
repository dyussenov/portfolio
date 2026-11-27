from django.db.models import Count

from .models import Branch, Screenshot, StudyGroup

NO_GROUP = 'без группы'


def counts(queryset, field):
    rows = queryset.values(field).annotate(n=Count('id', distinct=True))
    return {row[field]: row['n'] for row in rows}


def upload_report(date_from, date_to):
    """Текст отчёта: сколько скриншотов загружено за даты уроков [date_from; date_to] по филиалам и группам.

    Ученик, который состоит в нескольких группах, учитывается в каждой из них,
    поэтому итог филиала считается по самим скриншотам, а не суммой групп.
    """
    shots = Screenshot.objects.filter(lesson_date__range=(date_from, date_to))
    by_group = counts(shots.filter(student__groups__isnull=False), 'student__groups')
    no_group = counts(shots.filter(student__groups__isnull=True), 'student__branch')
    by_branch = counts(shots, 'student__branch')

    groups_by_branch = {}
    for group in StudyGroup.objects.all():
        # Закрытые в CRM группы показываем, только если за период по ним что-то загружено
        if group.is_active or by_group.get(group.pk):
            groups_by_branch.setdefault(group.branch_id, []).append(group)

    lines = [
        'Отчёт по загруженным картинкам',
        f'Период: {date_from:%d.%m.%Y} – {date_to:%d.%m.%Y}',
    ]
    for branch in Branch.objects.order_by('name'):
        lines += ['', f'{branch.name} — {by_branch.get(branch.pk, 0)}']
        for group in sorted(groups_by_branch.get(branch.pk, []), key=lambda g: g.name):
            lines.append(f'• {group.name} — {by_group.get(group.pk, 0)}')
        if no_group.get(branch.pk):
            lines.append(f'• {NO_GROUP} — {no_group[branch.pk]}')
    lines += ['', f'Итого: {shots.count()}']
    return '\n'.join(lines)
