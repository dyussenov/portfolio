// Общие помощники для кабинета сотрудника
(function () {
  const toasts = document.getElementById('toasts');

  function toast(text, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || 'ok');
    el.textContent = text;
    toasts.appendChild(el);
    setTimeout(() => el.classList.add('hide'), 3500);
    setTimeout(() => el.remove(), 4000);
  }

  async function api(url, body, options) {
    let response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.STAFF.csrf },
        body: JSON.stringify(body || {}),
        credentials: 'same-origin',
        keepalive: Boolean(options && options.keepalive),
      });
    } catch (e) {
      throw Object.assign(new Error('Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.'), { data: {} });
    }
    if (response.status === 403 || response.redirected) {
      throw Object.assign(new Error('Сессия закончилась — обновите страницу и войдите снова.'), { data: {} });
    }
    let data = {};
    try { data = await response.json(); } catch (e) { /* не JSON — ошибка сервера */ }
    if (!response.ok) {
      throw Object.assign(new Error(data.error || 'Что-то пошло не так. Попробуйте ещё раз.'), { data });
    }
    return data;
  }

  function urlFor(template, id) {
    return template.replace('/0/', '/' + id + '/');
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const area = document.createElement('textarea');
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    toast('Текст скопирован');
  }

  // Текст ОС размечен как в WhatsApp: *жирный*. Показываем так же, как увидит родитель
  function renderWhatsApp(node, raw) {
    node.dataset.raw = raw;
    node.textContent = '';
    raw.split(/(\*[^*\n]+\*)/).forEach((part) => {
      if (/^\*[^*\n]+\*$/.test(part)) {
        const bold = document.createElement('b');
        bold.textContent = part.slice(1, -1);
        node.appendChild(bold);
      } else if (part) {
        node.appendChild(document.createTextNode(part));
      }
    });
  }
  document.querySelectorAll('.wa-text').forEach((node) => renderWhatsApp(node, node.dataset.raw || ''));

  const server = document.getElementById('server-messages');
  if (server) setTimeout(() => server.remove(), 4000);

  window.Staff = { toast, api, urlFor, copyText, renderWhatsApp };
})();
