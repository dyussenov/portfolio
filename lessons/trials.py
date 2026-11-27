"""Данные ученика для пробного занятия из AlfaCRM."""
import datetime
import re
import urllib.parse

from django.conf import settings

from .alfacrm import AlfaCRMClient, AlfaCRMError


class TrialLookupError(Exception):
    pass


def normalize_host(host):
    return (host or '').strip().removeprefix('https://').removeprefix('http://').strip('/').lower()


def parse_crm_link(value):
    """Ссылка на карточку (…/company/1/customer/view?id=3412) или просто ID → (id филиала или None, id ученика)."""
    value = (value or '').strip()
    if value.isdigit():
        return None, int(value)
    parsed = urllib.parse.urlparse(value if '://' in value else f'https://{value}')
    if parsed.hostname and normalize_host(settings.ALFACRM_HOST) and parsed.hostname != normalize_host(settings.ALFACRM_HOST):
        raise TrialLookupError(f'Это ссылка не на нашу AlfaCRM ({normalize_host(settings.ALFACRM_HOST)}).')
    customer_id = (urllib.parse.parse_qs(parsed.query).get('id') or [None])[0]
    if not (customer_id and customer_id.isdigit()) or 'customer' not in parsed.path:
        raise TrialLookupError('Не похоже на ссылку на карточку ученика: в адресе должно быть «customer» и «id=…».')
    branch = re.search(r'/company/(\d+)', parsed.path)
    return (int(branch[1]) if branch else None), int(customer_id)


def first(values):
    if isinstance(values, list):
        return next((v for v in values if v), '')
    return values or ''


def customer_data(customer, branch_id):
    birth_date = None
    try:
        if customer.get('dob'):
            birth_date = datetime.datetime.strptime(customer['dob'].strip(), '%d.%m.%Y').date()
    except ValueError:
        pass
    return {
        'crm_customer_id': customer['id'],
        'crm_branch_id': branch_id,
        'full_name': ' '.join((customer.get('name') or '').split()),
        'birth_date': birth_date,
        'parent_name': ' '.join((customer.get('custom_parent_name') or customer.get('legal_name') or '').split()),
        'parent_phone': first(customer.get('custom_parent_phone')) or first(customer.get('phone')),
    }


def fetch_trial_student(link, client=None):
    branch_id, customer_id = parse_crm_link(link)
    missing = [n for n in ('ALFACRM_HOST', 'ALFACRM_EMAIL', 'ALFACRM_API_KEY') if not getattr(settings, n)]
    if missing:
        raise TrialLookupError('Не настроено подключение к AlfaCRM: ' + ', '.join(missing))
    client = client or AlfaCRMClient(settings.ALFACRM_HOST, settings.ALFACRM_EMAIL, settings.ALFACRM_API_KEY)
    try:
        branch_ids = [branch_id] if branch_id else [b['id'] for b in client.branches()]
        for candidate in branch_ids:
            customer = client.customer(candidate, customer_id)
            if customer:
                data = customer_data(customer, (customer.get('branch_ids') or [candidate])[0])
                if not data['full_name']:
                    raise TrialLookupError('В карточке AlfaCRM не указано имя ученика.')
                return data
    except AlfaCRMError as e:
        raise TrialLookupError(f'AlfaCRM не ответила: {e}') from e
    raise TrialLookupError(f'Ученик с ID {customer_id} в AlfaCRM не найден.')


def whatsapp_url(phone, text):
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    if len(digits) < 11:
        return None
    return f'https://wa.me/{digits}?' + urllib.parse.urlencode({'text': text})
