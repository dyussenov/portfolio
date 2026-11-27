// Страница менеджера: массовая запись на пробные, отправка ОС родителям
(function () {
  const { toast, api, urlFor, copyText } = window.Staff;
  const cfg = window.STAFF;
  const tutors = JSON.parse(document.getElementById('tutors-data').textContent);
  const TUTOR_KEY = 'staff:default-tutor';

  const linksInput = document.getElementById('links-input');
  const rowsBox = document.getElementById('trial-rows');
  const createBtn = document.getElementById('create-btn');
  const createError = document.getElementById('create-error');
  const defaultDate = document.getElementById('default-date');
  const defaultTutor = document.getElementById('default-tutor');
  const chips = document.querySelectorAll('.date-chip');

  let rows = [];
  let nextId = 1;

  // ---------- значения по умолчанию ----------

  try {
    const saved = localStorage.getItem(TUTOR_KEY);
    if (saved && tutors.some((t) => String(t.id) === saved)) defaultTutor.value = saved;
  } catch (e) { /* хранилище недоступно — не страшно */ }

  function syncChips() {
    chips.forEach((c) => c.classList.toggle('active', c.dataset.date === defaultDate.value));
  }

  function setDefaultDate(value) {
    defaultDate.value = value;
    syncChips();
    // Меняем дату во всех строках, где её не меняли вручную
    rows.forEach((row) => { if (!row.dateTouched) row.date = value; });
    render();
  }

  chips.forEach((chip) => chip.addEventListener('click', () => setDefaultDate(chip.dataset.date)));
  defaultDate.addEventListener('change', () => setDefaultDate(defaultDate.value));
  defaultTutor.addEventListener('change', () => {
    try { localStorage.setItem(TUTOR_KEY, defaultTutor.value); } catch (e) { /* ignore */ }
    rows.forEach((row) => { if (!row.tutorTouched) row.tutor = defaultTutor.value; });
    render();
  });
  syncChips();

  // ---------- ввод ссылок ----------

  function extractLinks(text) {
    return text.split(/[\s,;]+/).map((s) => s.trim()).filter((s) => /^\d{1,9}$/.test(s) || /customer/i.test(s));
  }

  function addLinks(text) {
    const links = extractLinks(text);
    const unknown = text.trim() && !links.length;
    if (unknown) {
      toast('Не нашли ссылок на карточки. Скопируйте адрес карточки ученика из AlfaCRM.', 'error');
      return false;
    }
    let skipped = 0;
    links.forEach((link) => {
      if (rows.some((r) => r.link === link)) { skipped += 1; return; }
      const row = {
        id: nextId++, link, date: defaultDate.value, tutor: defaultTutor.value,
        state: 'queued', info: null, error: '',
      };
      rows.push(row);
      queue.push(row);
    });
    if (skipped) toast(`Уже в списке: ${skipped}`, 'error');
    render();
    runQueue();
    return true;
  }

  linksInput.addEventListener('paste', () => {
    setTimeout(() => { if (addLinks(linksInput.value)) linksInput.value = ''; }, 0);
  });
  linksInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (addLinks(linksInput.value)) linksInput.value = '';
    }
  });
  linksInput.addEventListener('blur', () => {
    if (linksInput.value.trim() && addLinks(linksInput.value)) linksInput.value = '';
  });

  // Карточки проверяем по одной — у AlfaCRM ограничение на число запросов
  const queue = [];
  let busy = false;
  async function runQueue() {
    if (busy) return;
    busy = true;
    while (queue.length) {
      const row = queue.shift();
      if (!rows.includes(row)) continue;
      row.state = 'loading';
      render();
      try {
        row.info = await api(cfg.lookupUrl, { link: row.link });
        row.state = 'ok';
        row.error = '';
        const twin = rows.find((r) => r !== row && r.info && r.info.customer_id === row.info.customer_id);
        if (twin) { row.state = 'error'; row.error = `${row.info.full_name} уже есть в списке выше.`; }
      } catch (e) {
        row.state = 'error';
        row.error = e.message;
      }
      render();
    }
    busy = false;
  }

  // ---------- строки ----------

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === 'text') node.textContent = v;
      else if (k === 'class') node.className = v;
      else node.setAttribute(k, v);
    });
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  function studentCell(row) {
    if (row.state === 'queued' || row.state === 'loading') {
      return el('div', { class: 'row-student loading' }, [
        el('span', { class: 'spinner' }), el('span', { class: 'muted', text: 'Ищем в AlfaCRM…' }),
      ]);
    }
    if (row.state === 'error') {
      return el('div', { class: 'row-student' }, [
        el('div', { class: 'error', text: row.error }),
        el('div', { class: 'muted small link-text', text: row.link }),
      ]);
    }
    const info = row.info;
    const meta = [info.age, info.parent_phone ? `родитель ${info.parent_phone}` : 'нет телефона родителя'].filter(Boolean);
    const cell = el('div', { class: 'row-student' }, [
      el('b', { text: info.full_name }),
      el('div', { class: 'muted small', text: meta.join(' · ') }),
    ]);
    if (!info.age) {
      cell.appendChild(el('div', { class: 'warn small', text: 'В CRM нет даты рождения — проверьте, что это карточка ребёнка, а не родителя' }));
    }
    if (info.previous.length) {
      cell.appendChild(el('div', { class: 'warn small', text: 'Уже был на пробном: ' + info.previous.map((p) => p.label).join(', ') }));
    }
    return cell;
  }

  function render() {
    rowsBox.hidden = rows.length === 0;
    rowsBox.querySelectorAll('.trial-row:not(.head)').forEach((n) => n.remove());
    rows.forEach((row, index) => {
      const date = el('input', { type: 'date', value: row.date, 'aria-label': 'Дата пробного' });
      date.addEventListener('change', () => { row.date = date.value; row.dateTouched = true; updateButton(); });

      const tutor = el('select', { 'aria-label': 'Тьютор' }, [el('option', { value: '', text: '— тьютор —' })]);
      tutors.forEach((t) => tutor.appendChild(el('option', { value: String(t.id), text: t.name })));
      tutor.value = row.tutor || '';
      tutor.classList.toggle('invalid', !row.tutor);
      tutor.addEventListener('change', () => {
        row.tutor = tutor.value; row.tutorTouched = true;
        tutor.classList.toggle('invalid', !row.tutor);
        updateButton();
      });

      const remove = el('button', { type: 'button', class: 'icon-btn', title: 'Убрать из списка', 'aria-label': 'Убрать', text: '×' });
      remove.addEventListener('click', () => { rows = rows.filter((r) => r !== row); render(); });

      const actions = [remove];
      if (row.state === 'error' && !/уже есть в списке/.test(row.error)) {
        const retry = el('button', { type: 'button', class: 'btn-link small', text: 'Повторить' });
        retry.addEventListener('click', () => { queue.push(row); row.state = 'queued'; render(); runQueue(); });
        actions.unshift(retry);
      }

      rowsBox.appendChild(el('div', { class: 'trial-row ' + row.state }, [
        el('span', { class: 'row-num', text: String(index + 1) }),
        studentCell(row), date, tutor,
        el('div', { class: 'row-actions' }, actions),
      ]));
    });
    updateButton();
  }

  function updateButton() {
    const ready = rows.filter((r) => r.state === 'ok');
    const pending = rows.some((r) => r.state === 'queued' || r.state === 'loading');
    const invalid = rows.some((r) => r.state === 'error' || !r.tutor || !r.date);
    createBtn.disabled = !rows.length || pending || invalid;
    if (!rows.length) createBtn.textContent = 'Записать';
    else if (pending) createBtn.textContent = 'Проверяем ссылки…';
    else if (rows.some((r) => r.state === 'error')) createBtn.textContent = 'Уберите или исправьте строки с ошибками';
    else if (invalid) createBtn.textContent = 'Выберите тьютора для всех учеников';
    else createBtn.textContent = `Записать ${ready.length} ${plural(ready.length, 'ученика', 'учеников', 'учеников')}`;
  }

  function plural(n, one, few, many) {
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return few;
    return many;
  }

  createBtn.addEventListener('click', async () => {
    createError.hidden = true;
    createBtn.disabled = true;
    createBtn.textContent = 'Записываем…';
    try {
      await api(cfg.createUrl, {
        rows: rows.map((r) => ({ link: r.link, date: r.date, tutor: Number(r.tutor) })),
      });
      rows = [];
      window.location.reload();
    } catch (e) {
      createError.textContent = e.message;
      createError.hidden = false;
      Object.entries(e.data.errors || {}).forEach(([index, message]) => {
        const row = rows[Number(index)];
        if (row) { row.state = 'error'; row.error = message; }
      });
      render();
    }
  });

  window.addEventListener('beforeunload', (e) => {
    if (rows.length) { e.preventDefault(); e.returnValue = ''; }
  });

  // ---------- готовые ОС ----------

  const readyCount = document.getElementById('ready-count');
  function bumpReady(delta) { readyCount.textContent = String(Number(readyCount.textContent) + delta); }

  document.querySelectorAll('.ready-card').forEach((card) => {
    const id = card.dataset.id;
    const note = card.querySelector('.sent-note');
    const actions = card.querySelector('.ready-actions');

    async function markSent(undo) {
      try {
        await api(urlFor(cfg.sentUrl, id), { undo: Boolean(undo) }, { keepalive: true });
        card.classList.toggle('is-sent', !undo);
        note.hidden = Boolean(undo);
        actions.hidden = !undo;
        bumpReady(undo ? 1 : -1);
      } catch (e) {
        toast(e.message, 'error');
      }
    }

    const send = card.querySelector('.js-send');
    if (send) send.addEventListener('click', () => markSent(false)); // ссылка сама откроет WhatsApp
    const mark = card.querySelector('.js-mark-sent');
    if (mark) mark.addEventListener('click', () => markSent(false));
    card.querySelector('.js-undo').addEventListener('click', () => markSent(true));
    card.querySelector('.js-copy').addEventListener('click', () => copyText(card.querySelector('.feedback-text').dataset.raw));
  });

  // ---------- ждём ОС: правка даты, тьютора, удаление ----------

  document.querySelectorAll('.waiting-row').forEach((rowEl) => {
    const id = rowEl.dataset.id;
    async function save(body, input) {
      try {
        await api(urlFor(cfg.updateUrl, id), body);
        input.classList.add('saved');
        setTimeout(() => input.classList.remove('saved'), 1200);
        toast('Сохранено');
      } catch (e) {
        toast(e.message, 'error');
      }
    }
    const date = rowEl.querySelector('.js-date');
    date.addEventListener('change', () => save({ date: date.value }, date));
    const tutor = rowEl.querySelector('.js-tutor');
    tutor.addEventListener('change', () => save({ tutor: Number(tutor.value) }, tutor));
    rowEl.querySelector('.js-delete').addEventListener('click', async () => {
      const name = rowEl.querySelector('b').textContent;
      if (!window.confirm(`Удалить запись «${name}» на пробное?`)) return;
      try {
        await api(urlFor(cfg.deleteUrl, id));
        rowEl.remove();
        toast('Запись удалена');
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  });
})();
