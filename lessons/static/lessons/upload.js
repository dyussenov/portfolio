(() => {
  const students = JSON.parse(document.getElementById('students-data').textContent);
  const $ = id => document.getElementById(id);

  const BRANCH_KEY = 'lessons.branch';
  let branch = '';
  let selected = null;
  let queue = []; // {file, url, hash}
  let sending = false;

  // Токен берём из cookie при каждой отправке: вход в админку в соседней вкладке
  // меняет CSRF-секрет, и токен, вписанный в страницу при загрузке, устаревает
  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : document.querySelector('[name=csrfmiddlewaretoken]').value;
  }

  // ---------- шаг 1: выбор ученика ----------
  function storageGet(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }
  function storageSet(key, value) { try { localStorage.setItem(key, value); } catch {} }

  function setBranch(id) {
    branch = id;
    storageSet(BRANCH_KEY, id);
    document.querySelectorAll('#branches .tab').forEach(t => t.classList.toggle('active', t.dataset.branch === id));
    renderStudents();
  }

  function renderStudents() {
    const q = $('search').value.trim().toLowerCase().replace(/ё/g, 'е');
    const list = $('students');
    list.innerHTML = '';
    const matches = students.filter(s =>
      (!branch || String(s.branch_id) === branch) &&
      (!q || s.full_name.toLowerCase().replace(/ё/g, 'е').includes(q)));
    for (const s of matches) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = s.full_name;
      btn.addEventListener('click', () => selectStudent(s));
      li.append(btn);
      list.append(li);
    }
    $('no-results').hidden = matches.length > 0;
  }

  function selectStudent(s) {
    selected = s;
    $('selected-name').textContent = s.full_name;
    $('step-student').hidden = true;
    $('step-images').hidden = false;
    $('dropzone').focus();
  }

  function resetStudent() {
    selected = null;
    $('search').value = '';
    $('pin').value = '';
    renderStudents();
    $('step-images').hidden = true;
    $('step-done').hidden = true;
    $('step-student').hidden = false;
    $('search').focus();
  }

  // ---------- шаг 2: скриншоты ----------
  function showError(msg) {
    $('error').textContent = msg;
    $('error').hidden = !msg;
  }

  async function fileHash(file) {
    if (!crypto.subtle) return `${file.size}:${file.type}`;
    const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  }

  async function addFiles(files) {
    showError('');
    for (let file of files) {
      if (!file.type.startsWith('image/')) continue;
      if (queue.length >= window.MAX_FILES) {
        showError(`Не больше ${window.MAX_FILES} скриншотов за раз.`);
        break;
      }
      const hash = await fileHash(file);
      if (queue.some(item => item.hash === hash)) {
        showError('Этот скриншот уже добавлен. Сделай новый и вставь ещё раз.');
        continue;
      }
      // У картинок из буфера обмена имя всегда "image.png" — даём осмысленное
      if (!file.name || file.name === 'image.png') {
        const ext = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
        file = new File([file], `screenshot-${Date.now()}.${ext}`, { type: file.type });
      }
      queue.push({ file, hash, url: URL.createObjectURL(file) });
    }
    renderQueue();
  }

  function removeItem(index) {
    URL.revokeObjectURL(queue[index].url);
    queue.splice(index, 1);
    renderQueue();
  }

  function renderQueue() {
    const box = $('thumbs');
    box.innerHTML = '';
    queue.forEach((item, i) => {
      const div = document.createElement('div');
      div.className = 'thumb';
      const img = document.createElement('img');
      img.src = item.url;
      const num = document.createElement('span');
      num.className = 'thumb-num';
      num.textContent = i + 1;
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'thumb-del';
      del.title = 'Убрать';
      del.textContent = '×';
      del.addEventListener('click', () => removeItem(i));
      div.append(img, num, del);
      box.append(div);
    });
    const n = queue.length;
    $('submit').disabled = n === 0 || sending || !pinReady();
    $('submit').textContent = n ? `Отправить ${n} ${plural(n, 'скриншот', 'скриншота', 'скриншотов')}` : 'Отправить';
  }

  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function pinReady() {
    // 4 цифры — пин ученика, 8 цифр — секретный пин, а пароль тьютора может быть любым
    return $('pin').value.length >= 4;
  }

  async function submit() {
    if (!selected || !queue.length || sending || !pinReady()) return;
    sending = true;
    $('submit').disabled = true;
    $('submit').textContent = 'Отправляю…';
    showError('');

    const data = new FormData();
    data.append('student', selected.id);
    data.append('pin', $('pin').value);
    queue.forEach(item => data.append('images', item.file));

    try {
      const resp = await fetch(window.UPLOAD_URL, {
        method: 'POST',
        body: data,
        headers: { 'X-CSRFToken': csrfToken() },
      });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        if (json.field === 'pin') { $('pin').value = ''; $('pin').focus(); }
        throw new Error(json.error || `Ошибка сервера (${resp.status})`);
      }
      queue.forEach(item => URL.revokeObjectURL(item.url));
      queue = [];
      $('pin').value = '';
      $('done-text').textContent = `${selected.full_name}, сохранено ${json.saved} ${plural(json.saved, 'скриншот', 'скриншота', 'скриншотов')}!`;
      $('step-images').hidden = true;
      $('step-done').hidden = false;
    } catch (err) {
      showError(err.message === 'Failed to fetch' ? 'Нет связи с сервером. Попробуй ещё раз.' : err.message);
    } finally {
      sending = false;
      renderQueue();
    }
  }

  // ---------- события ----------
  document.querySelectorAll('#branches .tab').forEach(t => t.addEventListener('click', () => setBranch(t.dataset.branch)));
  $('search').addEventListener('input', renderStudents);
  $('pin').addEventListener('input', renderQueue);
  $('pin').addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  $('change-student').addEventListener('click', resetStudent);
  $('again').addEventListener('click', resetStudent);
  $('submit').addEventListener('click', submit);
  // Копируем список сразу: addFiles асинхронный, а очистка input и конец события drop опустошают FileList
  $('file-input').addEventListener('change', e => { addFiles(Array.from(e.target.files)); e.target.value = ''; });

  // ---------- быстрые ссылки от преподавателя ----------
  const QUICK_LINKS_POLL_MS = 15000;

  function renderQuickLinks(links) {
    const box = $('quick-links');
    box.innerHTML = '';
    for (const link of links) {
      const a = document.createElement('a');
      a.className = 'quick-link';
      a.href = link.url;
      a.title = link.url;
      a.target = '_blank';
      a.rel = 'noopener';
      const icon = document.createElement('span');
      icon.className = 'quick-link-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '↗';
      const text = document.createElement('span');
      text.className = 'quick-link-text';
      const title = document.createElement('b');
      title.textContent = link.title || link.url;
      const meta = document.createElement('span');
      meta.textContent = link.title ? `${link.added} · ${link.url}` : link.added;
      text.append(title, meta);
      a.append(icon, text);
      box.append(a);
    }
    $('quick-links-panel').hidden = links.length === 0;
  }

  async function refreshQuickLinks() {
    try {
      const resp = await fetch(window.QUICK_LINKS_URL, { cache: 'no-store' });
      if (resp.ok) renderQuickLinks((await resp.json()).links);
    } catch {} // нет связи — оставляем то, что уже показано
  }

  setInterval(refreshQuickLinks, QUICK_LINKS_POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshQuickLinks(); });

  document.addEventListener('paste', e => {
    if ($('step-images').hidden || e.target === $('pin')) return;
    const files = Array.from(e.clipboardData?.items || [])
      .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
      .map(item => item.getAsFile());
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    } else {
      showError('В буфере обмена нет картинки. Сначала сделай скриншот (Win + Shift + S).');
    }
  });

  const dz = $('dropzone');
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); addFiles(Array.from(e.dataTransfer.files)); });

  // Кнопка «Вставить» работает только по HTTPS / localhost
  if (navigator.clipboard?.read) {
    $('paste-btn').hidden = false;
    $('paste-btn').addEventListener('click', async () => {
      try {
        const files = [];
        for (const item of await navigator.clipboard.read()) {
          const type = item.types.find(t => t.startsWith('image/'));
          if (type) files.push(new File([await item.getType(type)], 'image.png', { type }));
        }
        if (files.length) addFiles(files);
        else showError('В буфере обмена нет картинки. Сначала сделай скриншот (Win + Shift + S).');
      } catch {
        showError('Браузер не дал доступ к буферу обмена. Нажми Ctrl + V.');
      }
    });
  }

  window.addEventListener('beforeunload', e => {
    if (queue.length) e.preventDefault();
  });

  // Список учеников тянется до низа экрана: CSS берёт отступ сверху из --students-top
  // (нижние поля карточки и страницы 16 + 16 px плюс рамка карточки 1 px)
  function fitStudents() {
    const list = $('students');
    if (list.offsetParent === null) return;
    const top = list.getBoundingClientRect().top + window.scrollY;
    document.documentElement.style.setProperty('--students-top', `${Math.ceil(top) + 33}px`);
  }
  window.addEventListener('resize', fitStudents);
  new ResizeObserver(fitStudents).observe($('quick-links-panel'));
  new ResizeObserver(fitStudents).observe($('branches'));

  const savedBranch = storageGet(BRANCH_KEY);
  setBranch(document.querySelector(`#branches .tab[data-branch="${savedBranch}"]`) ? savedBranch : '');
})();
