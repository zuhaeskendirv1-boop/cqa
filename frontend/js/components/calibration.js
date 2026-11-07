// View: hand-labeling (ground truth) + calibration report vs AI. This is the hackathon's main metric, measured inside the app.
CQA.views.calibration = async function (root) {
  const { esc, el, toast, answerOptions } = CQA.util;
  const [report, convs, gts, checklists] = await Promise.all([CQA.api.calibrationReport(), CQA.api.conversations(), CQA.api.groundTruth(), CQA.api.checklists()]);
  const chk = checklists.find(c => c.id === 'default') || checklists[0];
  const gtMap = Object.fromEntries(gts.map(g => [g.conversation_id, g]));

  root.innerHTML = '';
  // ---- report ----
  const rep = el(`<section class="panel"><h2>Калибровка: AI vs ручная разметка</h2></section>`);
  if (!report.n) rep.appendChild(el(`<p class="muted">${esc(report.message)} Размечено: ${report.labeled}.</p>`));
  else rep.appendChild(el(`<div>
    <div class="kpis">
      <div class="kpi"><div class="muted small">диалогов</div><b>${report.n}</b></div>
      <div class="kpi"><div class="muted small">MAE</div><b>${report.mae}</b></div>
      <div class="kpi"><div class="muted small">RMSE</div><b>${report.rmse}</b></div>
      <div class="kpi ${report.bias > 3 ? 'bad' : report.bias < -3 ? 'mid' : 'good'}"><div class="muted small">смещение (AI − GT)</div><b>${report.bias > 0 ? '+' : ''}${report.bias}</b></div>
      <div class="kpi"><div class="muted small">в пределах ±5 / ±10</div><b>${Math.round(report.within_5 * 100)}% / ${Math.round(report.within_10 * 100)}%</b></div>
      ${Object.entries(report.by_language).map(([l, v]) => `<div class="kpi"><div class="muted small">MAE ${esc(l)}</div><b>${v.mae}</b> <span class="muted small">(${v.n})</span></div>`).join('')}
    </div>
    <p class="advice">${esc(report.advice)}</p>
    ${Object.keys(report.criteria).length ? `<table class="tbl"><thead><tr><th>Критерий</th><th>n</th><th>Точное совпадение</th><th>Средн. |Δ|</th></tr></thead><tbody>
      ${Object.entries(report.criteria).sort((a, b) => a[1].exact - b[1].exact).map(([k, v]) => `<tr><td>${esc(chk.criteria.find(c => c.id === k)?.title || k)}</td><td>${v.n}</td><td class="${v.exact >= 0.8 ? 'good' : v.exact >= 0.6 ? 'mid' : 'bad'}">${Math.round(v.exact * 100)}%</td><td>${v.mean_abs_delta ?? '—'}</td></tr>`).join('')}</tbody></table>` : ''}
    <div class="muted small top">Худшие диалоги (читать → править формулировку критерия / добавить пример в calibration.json → переоценить):</div>
    <table class="tbl"><thead><tr><th>Диалог</th><th>Язык</th><th>GT</th><th>AI</th><th>Δ</th><th></th></tr></thead><tbody>
      ${report.worst.map(r => `<tr><td>${esc(r.conversation_id)}</td><td>${esc(r.language || '')}</td><td>${r.gt}</td><td>${r.ai}</td><td class="${Math.abs(r.delta) > 10 ? 'bad' : Math.abs(r.delta) > 5 ? 'mid' : 'good'}">${r.delta > 0 ? '+' : ''}${r.delta}</td><td><button class="small" onclick="CQA.go('card',{id:'${esc(r.conversation_id)}'})">открыть</button></td></tr>`).join('')}</tbody></table>
  </div>`));
  root.appendChild(rep);

  // ---- labeling ----
  const lab = el(`<section class="panel"><h2>Разметка (ground truth)</h2>
    <p class="muted small">Команда вручную оценивает 10–15 DEV-диалогов по чек-листу «${esc(chk.name)}». Итог считается по тем же правилам, что и у AI (или введите готовый итог). Нужна роль reviewer/admin.</p>
    <div class="row gap wrap"><select id="lab-conv"><option value="">— выберите диалог —</option>${convs.map(c => `<option value="${esc(c.conversation_id)}">${esc(c.conversation_id)} · ${esc(c.language)}${c.has_gt ? ' ✓' : ''}</option>`).join('')}</select><span class="muted small">размечено: ${gts.length}</span></div>
    <div id="lab-form" class="hidden">
      <div class="lab-grid">${chk.criteria.map(c => `<label class="lab-row"><span>${esc(c.title)}</span><select data-c="${esc(c.id)}">${answerOptions(c).map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></label>`).join('')}</div>
      <div class="row gap wrap top"><label class="inline">или готовый итог <input id="lab-total" type="number" min="0" max="100" style="width:80px" placeholder="авто"></label><input id="lab-note" class="grow" placeholder="заметка (необязательно)"><button id="lab-save" class="primary">Сохранить разметку</button><button id="lab-del" class="small danger">Удалить</button></div>
    </div></section>`);
  root.appendChild(lab);
  const sel = lab.querySelector('#lab-conv'), form = lab.querySelector('#lab-form');
  sel.onchange = () => {
    form.classList.toggle('hidden', !sel.value);
    const g = gtMap[sel.value];
    form.querySelectorAll('select[data-c]').forEach(s => {
      const c = chk.criteria.find(x => x.id === s.dataset.c), v = g?.criteria?.[s.dataset.c];
      s.value = v !== undefined ? String(v) : CQA.util.bestAnswer(c);  // default = best (most dialogs pass)
    });
    form.querySelector('#lab-total').value = g && !Object.keys(g.criteria || {}).length ? g.total : '';
    form.querySelector('#lab-note').value = g?.note || '';
  };
  lab.querySelector('#lab-save').onclick = async () => {
    const criteria = {};
    form.querySelectorAll('select[data-c]').forEach(s => { const c = chk.criteria.find(x => x.id === s.dataset.c); criteria[s.dataset.c] = s.value === 'N/A' ? 'N/A' : c.type === 'scale' ? +s.value : s.value; });
    const total = form.querySelector('#lab-total').value;
    try { await CQA.api.saveGroundTruth({ conversation_id: sel.value, total: total === '' ? null : +total, criteria: total === '' ? criteria : {}, note: form.querySelector('#lab-note').value }); toast('Разметка сохранена'); CQA.go('calibration'); }
    catch (err) { toast(err.message, 'err'); }
  };
  lab.querySelector('#lab-del').onclick = async () => { try { await CQA.api.deleteGroundTruth(sel.value); CQA.go('calibration'); } catch (err) { toast(err.message, 'err'); } };
};
