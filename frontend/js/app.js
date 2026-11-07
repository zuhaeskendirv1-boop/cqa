// Router + shell. Views are plain functions (root, params) => Promise — easy to mount inside any host page.
(() => {
  const view = document.getElementById('view');
  CQA.go = async (name, params = {}) => {
    document.querySelectorAll('.cqa-nav button').forEach(b => b.classList.toggle('active', b.dataset.view === (name === 'card' ? 'dialogs' : name)));
    view.innerHTML = '<div class="loading">Загрузка…</div>';
    try { await CQA.views[name](view, params); } catch (e) { view.innerHTML = `<section class="panel err">Ошибка: ${CQA.util.esc(e.message)}</section>`; }
    history.replaceState(null, '', '#' + name + (params.id ? '/' + encodeURIComponent(params.id) : ''));
    CQA.refreshStats();
  };
  CQA.refreshBadge = async () => {
    try {
      const open = await CQA.api.appeals('open');
      const b = document.getElementById('appeal-badge'); b.textContent = open.length; b.classList.toggle('hidden', !open.length);
    } catch (_) {}
  };
  CQA.refreshStats = async () => {
    try {
      const s = await CQA.api.stats();
      document.getElementById('stats').innerHTML = `${s.llm.provider}/${s.llm.model} · ${s.llm.calls} вызовов · ~$${s.llm.usd}${s.llm.provider === 'mock' ? ' · <b class="warn">MOCK — демо-режим</b>' : ''}`;
      const needTok = s.roles_protected[CQA.role.name];
      document.getElementById('role-token').classList.toggle('hidden', !needTok);
    } catch (_) {}
  };
  const roleSel = document.getElementById('role-sel'), roleTok = document.getElementById('role-token');
  roleSel.value = CQA.role.name; roleTok.value = CQA.role.token;
  roleSel.onchange = () => { CQA.role.name = roleSel.value; localStorage.setItem('cqa_role', roleSel.value); CQA.refreshStats(); CQA.util.toast(`Роль: ${roleSel.value}`); };
  roleTok.onchange = () => { CQA.role.token = roleTok.value; localStorage.setItem('cqa_token', roleTok.value); };
  document.querySelectorAll('.cqa-nav button').forEach(b => b.onclick = () => CQA.go(b.dataset.view));
  const fromHash = () => { const [name, id] = location.hash.slice(1).split('/'); return [CQA.views[name] ? name : 'dialogs', id ? { id: decodeURIComponent(id) } : {}]; };
  let current = '';
  const route = () => { const [name, params] = fromHash(); const key = name + '/' + (params.id || ''); if (key === current) return; current = key; CQA.go(name, params); };
  const origGo = CQA.go; CQA.go = (name, params = {}) => { current = name + '/' + (params.id || ''); return origGo(name, params); };
  window.addEventListener('hashchange', route);
  route();
  CQA.refreshBadge();
})();
