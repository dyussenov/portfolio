// Страница тьютора: ОС по пробным из нескольких полей, автосохранение и предпросмотр текста для родителя
(function () {
  const { toast, api, urlFor, renderWhatsApp } = window.Staff;
  const cfg = window.STAFF;
  const AUTOSAVE_DELAY = 800;
  const FIELDS = ['topic', 'activities', 'recommended_group', 'makeup_hours'];
  const todoCount = document.getElementById('todo-count');
  const doneCount = document.getElementById('done-count');
  const doneList = document.getElementById('done-list');

  function bump(counter, delta) { counter.textContent = String(Math.max(0, Number(counter.textContent) + delta)); }

  document.querySelectorAll('.fb-card').forEach((card) => {
    const id = card.dataset.id;
    const body = card.querySelector('.fb-body');
    const expand = card.querySelector('.js-expand');
    const preview = card.querySelector('.fb-preview');
    if (expand) {
      expand.addEventListener('click', () => {
        body.hidden = false;
        expand.hidden = true;
        if (preview) preview.hidden = true;
        const first = card.querySelector('[name=topic]');
        if (first) first.focus();
      });
    }
    const inputs = Object.fromEntries(FIELDS.map((name) => [name, card.querySelector(`[name=${name}]`)]));
    if (!inputs.topic) return; // уже отправлено родителю — только просмотр

    const state = card.querySelector('.js-save-state');
    const status = card.querySelector('.js-status');
    const doneBtn = card.querySelector('.js-done');
    const result = card.querySelector('.js-preview');
    const resultEmpty = card.querySelector('.js-preview-empty');
    let timer = null;
    let lastSaved = JSON.stringify(values());
    let isDone = card.classList.contains('collapsed');

    function values() {
      return Object.fromEntries(FIELDS.map((name) => [name, inputs[name].value]));
    }

    function setState(message, kind) {
      state.textContent = message;
      state.classList.toggle('error', kind === 'error');
    }

    function markMissing(missing) {
      FIELDS.forEach((name) => inputs[name].classList.toggle('invalid', (missing || []).includes(name)));
    }

    async function save(done) {
      clearTimeout(timer);
      const current = values();
      const snapshot = JSON.stringify(current);
      if (!done && snapshot === lastSaved) return true;
      setState('Сохраняем…');
      try {
        const data = await api(urlFor(cfg.saveUrl, id), { ...current, done: Boolean(done) });
        lastSaved = snapshot;
        markMissing([]);
        status.textContent = data.status;
        status.className = 'status-chip js-status' + (data.done ? ' done' : data.feedback ? ' draft' : '');
        renderWhatsApp(result, data.feedback);
        resultEmpty.hidden = Boolean(data.feedback);
        setState(data.saved_at ? `Сохранено в ${data.saved_at}` : (data.feedback ? 'Черновик сохранён' : ''));
        return data;
      } catch (e) {
        setState(e.message, 'error');
        markMissing(e.data.missing || (e.data.field ? [e.data.field] : []));
        return false;
      }
    }

    FIELDS.forEach((name) => {
      const input = inputs[name];
      input.addEventListener('input', () => {
        input.classList.remove('invalid');
        setState('Есть несохранённые изменения');
        clearTimeout(timer);
        timer = setTimeout(() => save(false), AUTOSAVE_DELAY);
      });
      input.addEventListener('blur', () => save(false));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(); }
      });
    });

    async function finish() {
      doneBtn.disabled = true;
      const data = await save(true);
      doneBtn.disabled = false;
      if (!data) {
        const firstMissing = card.querySelector('.invalid');
        if (firstMissing) firstMissing.focus();
        return;
      }
      if (!isDone) {
        isDone = true;
        bump(todoCount, -1);
        bump(doneCount, 1);
        doneBtn.textContent = 'Сохранить';
        toast(`Обратная связь по ${card.dataset.name} передана менеджеру`);
        card.classList.add('just-done');
        doneList.prepend(card);
        document.getElementById('todo-empty').hidden = Number(todoCount.textContent) > 0;
        const next = document.querySelector('.fb-card:not(.just-done):not(.collapsed) [name=topic]');
        if (next) next.focus();
      } else {
        toast('Изменения сохранены — менеджер увидит новый текст');
      }
    }

    doneBtn.addEventListener('click', finish);
  });

  // Не теряем текст, если вкладку закрывают до автосохранения
  window.addEventListener('beforeunload', (e) => {
    const dirty = [...document.querySelectorAll('.js-save-state')].some((s) => s.textContent === 'Есть несохранённые изменения');
    if (dirty) { e.preventDefault(); e.returnValue = ''; }
  });

  const firstTodo = document.querySelector('.fb-card:not(.collapsed) [name=topic]');
  if (firstTodo && !firstTodo.value) firstTodo.focus();
})();
