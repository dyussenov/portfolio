from import_export import fields, resources, widgets

from .models import Branch, Student


class StudentResource(resources.ModelResource):
    """Импорт учеников в выбранный в админке филиал. Колонки как в students_<филиал>.csv: ID, ФИО."""

    external_id = fields.Field(attribute='external_id', column_name='ID', widget=widgets.IntegerWidget())
    full_name = fields.Field(attribute='full_name', column_name='ФИО')
    # Филиал из файла не читается — его выбирают в форме импорта
    branch = fields.Field(attribute='branch', column_name='Филиал', widget=widgets.ForeignKeyWidget(Branch, 'name'), readonly=True)

    class Meta:
        model = Student
        fields = ('external_id', 'full_name', 'branch')
        import_id_fields = ('external_id',)
        skip_unchanged = True
        report_skipped = True

    def __init__(self, branch=None, **kwargs):
        super().__init__(**kwargs)
        self.branch = branch

    def before_import(self, dataset, **kwargs):
        if self.branch is None:
            raise ValueError('Не выбран филиал для импорта')

    def before_import_row(self, row, **kwargs):
        row['ФИО'] = ' '.join(str(row.get('ФИО') or '').split())

    def skip_row(self, instance, original, row, import_validation_errors=None):
        if not row['ФИО']:
            return True
        # Ученик уже в этом филиале и активен — менять нечего
        if original.pk and (original.full_name, original.branch_id, original.is_active) == (
            row['ФИО'], self.branch.pk, True,
        ):
            return True
        return False

    def before_save_instance(self, instance, row, **kwargs):
        instance.branch = self.branch
        instance.is_active = True
