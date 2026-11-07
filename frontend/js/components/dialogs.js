// View: dialog list + import + batch scoring
CQA.views = CQA.views || {};
CQA.views.dialogs = async function (root) {
  const { esc, el, toast, scoreClass } = CQA.util;
  const [convs, checklists, groups, stats] = await Promise.all([CQA.api.conversations(), CQA.api.checklists(), CQA.api.groups(), CQA.api.stats()]);
  const groupOpts = Object.entries(groups).map(([g, n]) => `<option value="${esc(g)}">${esc(g)} (${n})</option>`).join('');
  const chkOpts = checklists.map(c => `<option value="${esc(c.id)}">${esc(c.name)} (v${c.version})</option>`).join('');

  root.innerHTML = '';
  root.appendChild(el(`
    <section class="panel">
      <div class="row wrap gap">
        <div class="field grow">
          <label>Импорт из API организаторов (GET с group_id) — запрос идёт через сервер, CORS не мешает</label>
          <div class="row gap"><input id="imp-url" placeholder="https://...invoke?...&group_id=DEV"><button id="imp-btn" class="primary">Загрузить</button></div>
        </div>
        <div class="field">
          <label>Или JSON-файл</label>
          <input type="file" id="imp-file" accept="application/json">
        </div>
      </div>
      <div class="row wrap gap top">
        <div class="field"><label>Чек-лист</label><select id="chk-sel">${chkOpts}</select></div>
        <div class="field"><label>Режим</label><select id="mode-sel"><option value="">по умолчанию (.env)</option><option value="per_criterion">per_criterion (точнее)</option><option value="batch">batch (дешевле)</option></select></div>
        <div class="field"><label>Самплов (голосование)</label><select id="samp-sel"><option value="">.env</option><option>1</option><option>3</option><option>5</option></select></div>
        <div class="field end"><button id="batch-btn" class="primary">Оценить все неоценённые</button></div>
        <div class="field end"><button id="batch-all-btn">Переоценить все</button></div>
        <div class="field end"><a class="btn" href="${CQA.api.exportUrl()}" download>CSV</a></div>
      </div>
      <div class="row wrap gap top submit-row">
        <div class="field"><label>Набор (group_id)</label><select id="grp-sel">${groupOpts}</select></div>
        <div class="field end"><a class="btn" id="res-dl" href="#" download>Скачать results.json</a></div>
        <div class="field end"><button id="res-dev">Записать results/dev/results.json</button></div>
        <div class="field end"><button id="res-prod" class="primary">Записать results/prod/results.json</button></div>
        <div class="muted small">команда: <b>${esc(stats.team_name)}</b> · model: ${esc(stats.submission_model.mode)} / ${esc(stats.submission_model.name)} (TEAM_NAME, MODEL_MODE в .env)</div>
      </div>
      <div id="batch-progress" class="progress hidden"><div class="bar"></div><span></span></div>
      <div id="batch-errors" class="muted small"></div>
    </section>`));

  const table = el(`<section class="panel">
    <div class="row gap"><input id="flt" placeholder="фильтр: id, RU/KZ/EN, voice..." class="grow"><span class="muted">${convs.length} диалогов</span></div>
    <table class="tbl"><thead><tr><th>ID</th><th>Канал</th><th>Язык</th><th>Реплик</th><th>Статус</th><th>Итог</th><th></th></tr></thead><tbody></tbody></table>
  </section>`);
  root.appendChild(table);
  const tbody = table.querySelector('tbody');

  function render(filter = '') {
    const f = filter.toLowerCase();
    tbody.innerHTML = convs.filter(c => !f || `${c.conversation_id} ${c.channel} ${c.language} ${c.status}`.toLowerCase().includes(f)).map(c => `
      <tr data-id="${esc(c.conversation_id)}">
        <td><b>${esc(c.conversation_id)}</b></td>
        <td>${c.channel === 'voice call' ? '📞 звонок' : '💬 чат'}</td>
        <td><span class="pill">${esc(c.language)}</span></td>
        <td>${c.turns}</td>
        <td><span class="status ${c.status}" title="${esc(c.error || '')}">${{ new: 'не оценён', ai_scored: 'оценён AI', appealed: 'апелляция', reviewed: 'проверено', error: '⚠ ошибка' }[c.status] || c.status}</span> <span class="muted small">${esc(c.group || '')}</span></td>
        <td class="score ${scoreClass(c.total)}">${CQA.util.fmtTotal(c.total)}${c.gross_error ? ' <span class="gross" title="грубая ошибка">⚠</span>' : ''}</td>
        <td><button class="small open">Открыть</button> <button class="small eval">${c.total == null ? 'Оценить' : 'Переоценить'}</button></td>
      </tr>`).join('');
  }
  render();
  root.querySelector('#flt').oninput = e => render(e.target.value);

  const opts = () => ({
    checklist_id: root.querySelector('#chk-sel').value,
    mode: root.querySelector('#mode-sel').value || null,
    samples: root.querySelector('#samp-sel').value ? +root.querySelector('#samp-sel').value : null,
  });

  tbody.onclick = async e => {
    const tr = e.target.closest('tr'); if (!tr) return;
    const id = tr.dataset.id;
    if (e.target.classList.contains('open')) return CQA.go('card', { id });
    if (e.target.classList.contains('eval')) {
      e.target.disabled = true; e.target.textContent = '…';
      try { await CQA.api.evaluate({ conversation_id: id, ...opts() }); toast('Оценка готова'); CQA.go('card', { id }); }
      catch (err) { toast(err.message, 'err'); e.target.disabled = false; e.target.textContent = 'Оценить'; }
    }
  };

  root.querySelector('#imp-btn').onclick = async () => {
    const url = root.querySelector('#imp-url').value.trim(); if (!url) return;
    try { const r = await CQA.api.importUrl(url); toast(`Импортировано: ${r.imported}`); CQA.go('dialogs'); } catch (err) { toast(err.message, 'err'); }
  };
  root.querySelector('#imp-file').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    try { const r = await CQA.api.importPayload(JSON.parse(await f.text())); toast(`Импортировано: ${r.imported}`); CQA.go('dialogs'); } catch (err) { toast(err.message, 'err'); }
  };

  async function runBatch(ids) {
    const prog = root.querySelector('#batch-progress'); prog.classList.remove('hidden');
    try { await CQA.api.batch({ conversation_ids: ids, ...opts() }); } catch (err) { return toast(err.message, 'err'); }
    const tick = async () => {
      const s = await CQA.api.batchStatus();
      prog.querySelector('.bar').style.width = (s.total ? s.done / s.total * 100 : 0) + '%';
      prog.querySelector('span').textContent = `${s.done}/${s.total}${s.errors.length ? ` · ошибок: ${s.errors.length}` : ''}`;
      if (s.errors.length) root.querySelector('#batch-errors').textContent = s.errors.map(x => `${x.conversation_id}: ${x.error}`).join(' · ');
      if (s.running) setTimeout(tick, 1500); else { toast(`Пакетная оценка завершена${s.errors.length ? `, ошибок: ${s.errors.length}` : ''}`); CQA.go('dialogs'); }
    };
    tick();
  }
  root.querySelector('#batch-btn').onclick = () => runBatch(convs.filter(c => c.total == null).map(c => c.conversation_id));
  const grp = root.querySelector('#grp-sel');
  const setDl = () => { root.querySelector('#res-dl').href = CQA.api.resultsUrl(grp.value); };
  grp.onchange = setDl; setDl();
  const writeRes = async target => {
    try {
      const r = await CQA.api.writeResults({ group: grp.value, target });
      toast(`${r.path}: ${r.records} записей (ok ${r.ok}, error ${r.error})${r.problems.length ? ` · ПРОБЛЕМЫ: ${r.problems.length}` : ''}`, r.problems.length ? 'err' : 'ok');
      if (r.problems.length) alert('Проблемы валидации:\n' + r.problems.join('\n'));
    } catch (err) { toast(err.message, 'err'); }
  };
  root.querySelector('#res-dev').onclick = () => writeRes('dev');
  root.querySelector('#res-prod').onclick = () => confirm(`Записать набор ${grp.value} как results/prod/results.json?`) && writeRes('prod');
  root.querySelector('#batch-all-btn').onclick = () => confirm('Переоценить все диалоги?') && runBatch(null);
};
