import json
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_SIZE = 500
MIN_REQUEST_INTERVAL = 0.25  # лимит API — 5 запросов в секунду, держим 4
MAX_ATTEMPTS = 5
TIMEOUT = 30


class AlfaCRMError(Exception):
    pass


class AlfaCRMClient:
    def __init__(self, host, email, api_key):
        host = host.strip().removeprefix('https://').removeprefix('http://').strip('/')
        self.base_url = f'https://{host}/v2api'
        self.email = email
        self.api_key = api_key
        self.token = None
        self._last_request = 0.0

    def _send(self, path, payload, query=None):
        wait = self._last_request + MIN_REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

        url = f'{self.base_url}/{path}'
        if query:
            url += '?' + urllib.parse.urlencode(query)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.token:
            headers['X-ALFACRM-TOKEN'] = self.token
        request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method='POST')
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)

    def login(self):
        self.token = None
        data = self._send('auth/login', {'email': self.email, 'api_key': self.api_key})
        self.token = data['token']

    def post(self, path, payload, query=None):
        if self.token is None:
            self.login()
        relogged = False
        for attempt in range(MAX_ATTEMPTS):
            retryable = attempt < MAX_ATTEMPTS - 1
            try:
                return self._send(path, payload, query)
            except urllib.error.HTTPError as e:
                if e.code == 401 and not relogged:
                    relogged = True
                    self.login()
                    continue
                if not (e.code == 429 or e.code >= 500) or not retryable:
                    body = e.read(500).decode('utf-8', 'replace')
                    raise AlfaCRMError(f'{path}: HTTP {e.code}: {body}') from e
            except (urllib.error.URLError, TimeoutError) as e:
                if not retryable:
                    raise AlfaCRMError(f'{path}: {e}') from e
            time.sleep(2 ** attempt)
        raise AlfaCRMError(f'{path}: не удалось авторизоваться')

    def _paginate(self, path, filters=None):
        page = 0
        while True:
            data = self.post(path, {**(filters or {}), 'page': page, 'pageSize': PAGE_SIZE})
            items = data.get('items') or []
            yield from items
            if not items or (page + 1) * PAGE_SIZE >= data.get('total', 0):
                return
            page += 1

    def branches(self):
        return list(self._paginate('branch/index'))

    def students(self, branch_id):
        return self._paginate(f'{branch_id}/customer/index', {'is_study': 1})

    def update_customer(self, branch_id, customer_id, fields):
        data = self.post(f'{branch_id}/customer/update', fields, {'id': customer_id})
        if not data.get('success'):
            raise AlfaCRMError(f'ученик {customer_id}: {data.get("errors")}')
