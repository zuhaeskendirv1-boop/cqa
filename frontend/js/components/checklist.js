// View: checklist editor — add/edit/remove criteria, weights, types, scales, N/A, rules; save as new version
CQA.views.checklist = async function (root, { id } = {}) {
  const { esc, el, toast } = CQA.util;
  const all = await CQA.api.checklists();
  let chk = JSON.parse(JSON.stringify(all.find(c => c.id === (id || 'default')) || all[0]));

  root.innerHTML = '';
  root.appendChild(el(`
    <section class="panel row gap wrap">
      <div class="field"><label>Чек-лист</label><select id="chk-pick">${all.map(c => `<option value="${esc(c.id)}" ${c.id === chk.id ? 'selected' : ''}>${esc(c.name)} (v${c.version})</option>`).join('')}</select></div>
      <div class="field end"><button id="new-chk">Новый (копия текущего)</button></div>
      <div class="field end"><button id="del-chk" class="danger">Удалить</button></div>
      <div class="grow"></div>
      <div class="field end"><button id="save-chk" class="primary">Сохранить конфигурацию</button></div>
    </section>`));

  const form = el(`<section class="panel">
    <div class="row gap wrap">
      <div class="field grow"><label>Название</label><input id="chk-name" value="${esc(chk.name)}"></div>
      <div class="field"><label>Шкала итога</label><input id="r-scale" type="number" value="${chk.rules.total_scale ?? 100}" style="width:80px"></div>
      <div class="field"><label>Округление (знаков)</label><input id="r-round" type="number" value="${chk.rules.rounding ?? 0}" style="width:80px"></div>
      <div class="field"><label>Грубая ошибка</label><select id="r-gross">
        <option value="flag" ${chk.rules.gross_error_policy === 'flag' ? 'selected' : ''}>только отметить</option>
        <option value="cap" ${chk.rules.gross_error_policy === 'cap' ? 'selected' : ''}>ограничить итог</option>
        <option value="zero" ${chk.rules.gross_error_policy === 'zero' ? 'selected' : ''}>итог = 0</option></select></div>
      <div class="field"><label>Потолок при cap</label><input id="r-cap" type="number" value="${chk.rules.gross_error_cap ?? 0}" style="width:80px"></div>
    </div>
    <p class="muted small">N/A: вес неприменимых критериев перераспределяется пропорционально между применимыми; если применимых нет — итог не считается. Тип «грубая ошибка» — отдельный критерий без баллов («Да» = ошибка есть). Шкала со ступенями: ответ модели притягивается к ближайшей ступени.</p>
    <div class="field top"><label>Знания предметной области (попадают в промпт как «истина» для проверки корректности и стандартов: сроки расчётов, что оператору запрещено, как идентифицировать клиента)</label>
      <textarea id="chk-domain" rows="5">${esc(chk.domain_notes || '')}</textarea></div>
    <div id="crit-rows"></div>
    <div class="row gap top"><button id="add-crit">+ Добавить критерий</button><span class="muted" id="wsum"></span></div>
  </section>`);
  root.appendChild(form);
  const rows = form.querySelector('#crit-rows');

  function critRow(c, i) {
    const isScale = c.type === 'scale', isGE = c.type === 'gross_error';
    return el(`<div class="crit-row" data-i="${i}">
      <div class="row gap wrap">
        <input class="f-id" placeholder="id" value="${esc(c.id || '')}" style="width:140px" title="идентификатор (латиница)">
        <input class="f-section" placeholder="секция" value="${esc(c.section || '')}" style="width:150px">
        <input class="f-title grow" placeholder="название критерия" value="${esc(c.title || '')}">
        <select class="f-type"><option value="yes_no" ${c.type === 'yes_no' || !c.type ? 'selected' : ''}>да/нет</option><option value="scale" ${isScale ? 'selected' : ''}>шкала</option><option value="gross_error" ${isGE ? 'selected' : ''}>грубая ошибка (без баллов)</option></select>
        <label class="inline" title="ступени шкалы через запятую, напр. 0,5,10,15,20">ступени <input class="f-steps" value="${esc((c.scale?.steps || []).join(','))}" placeholder="0,5,10,15,20" style="width:120px" ${isScale ? '' : 'disabled'}></label>
        <label class="inline">min <input class="f-min" type="number" value="${c.scale?.min ?? 0}" style="width:55px" ${isScale ? '' : 'disabled'}></label>
        <label class="inline">max <input class="f-max" type="number" value="${c.scale?.max ?? 2}" style="width:55px" ${isScale ? '' : 'disabled'}></label>
        <label class="inline">вес <input class="f-weight" type="number" value="${isGE ? 0 : (c.weight ?? 10)}" style="width:65px" ${isGE ? 'disabled' : ''}></label>
        <label class="inline"><input class="f-na" type="checkbox" ${c.na_allowed ? 'checked' : ''} ${isGE ? 'disabled' : ''}> N/A</label>
        <button class="small up">↑</button><button class="small down">↓</button><button class="small danger rm">✕</button>
      </div>
      <textarea class="f-desc" placeholder="Формулировка: что именно должен сделать оператор (это читает модель)">${esc(c.description || '')}</textarea>
      <div class="row gap wrap">
        <input class="f-labels grow" placeholder="подписи шкалы, напр.: 0=не сделал; 1=частично; 2=полностью" value="${esc(Object.entries(c.scale?.labels || {}).map(([k, v]) => `${k}=${v}`).join('; '))}" ${isScale ? '' : 'disabled'}>
        <input class="f-nahint grow" placeholder="правило N/A: когда критерий не применяется" value="${esc(c.na_hint || '')}" ${c.na_allowed ? '' : 'disabled'}>
      </div>
    </div>`);
  }
  function render() {
    rows.innerHTML = '';
    chk.criteria.forEach((c, i) => rows.appendChild(critRow(c, i)));
    form.querySelector('#wsum').textContent = `сумма весов: ${chk.criteria.filter(c => c.type !== 'gross_error').reduce((s, c) => s + (+c.weight || 0), 0)}`;
  }
  function collect() {
    chk.name = form.querySelector('#chk-name').value.trim();
    chk.domain_notes = form.querySelector('#chk-domain').value.trim();
    chk.rules = { total_scale: +form.querySelector('#r-scale').value || 100, rounding: +form.querySelector('#r-round').value || 0, gross_error_policy: form.querySelector('#r-gross').value, gross_error_cap: +form.querySelector('#r-cap').value || 0 };
    chk.criteria = [...rows.querySelectorAll('.crit-row')].map(r => {
      const type = r.querySelector('.f-type').value;
      const labels = {};
      r.querySelector('.f-labels').value.split(';').map(s => s.trim()).filter(Boolean).forEach(p => { const [k, ...v] = p.split('='); if (v.length) labels[k.trim()] = v.join('=').trim(); });
      return {
        id: r.querySelector('.f-id').value.trim() || undefined,
        section: r.querySelector('.f-section').value.trim(),
        title: r.querySelector('.f-title').value.trim(),
        description: r.querySelector('.f-desc').value.trim(),
        type,
        scale: type === 'scale' ? { min: +r.querySelector('.f-min').value, max: +r.querySelector('.f-max').value, labels,
          steps: r.querySelector('.f-steps').value.split(',').map(x => x.trim()).filter(x => x !== '').map(Number).filter(x => !isNaN(x)) } : undefined,
        weight: type === 'gross_error' ? 0 : +r.querySelector('.f-weight').value,
        na_allowed: type === 'gross_error' ? false : r.querySelector('.f-na').checked,
        na_hint: r.querySelector('.f-nahint').value.trim(),
      };
    });
  }
  render();

  rows.addEventListener('change', e => {
    const r = e.target.closest('.crit-row'); if (!r) return;
    if (e.target.classList.contains('f-type')) {
      const s = e.target.value === 'scale', g = e.target.value === 'gross_error';
      ['.f-min', '.f-max', '.f-labels', '.f-steps'].forEach(q => r.querySelector(q).disabled = !s);
      r.querySelector('.f-weight').disabled = g; r.querySelector('.f-na').disabled = g;
      if (g) { r.querySelector('.f-weight').value = 0; r.querySelector('.f-na').checked = false; r.querySelector('.f-nahint').disabled = true; }
    }
    if (e.target.classList.contains('f-na')) r.querySelector('.f-nahint').disabled = !e.target.checked;
    if (e.target.classList.contains('f-weight')) { collect(); form.querySelector('#wsum').textContent = `сумма весов: ${chk.criteria.filter(c => c.type !== 'gross_error').reduce((s, c) => s + (+c.weight || 0), 0)}`; }
  });
  rows.addEventListener('click', e => {
    const r = e.target.closest('.crit-row'); if (!r) return;
    const i = +r.dataset.i; collect();
    if (e.target.classList.contains('rm')) chk.criteria.splice(i, 1);
    else if (e.target.classList.contains('up') && i > 0) [chk.criteria[i - 1], chk.criteria[i]] = [chk.criteria[i], chk.criteria[i - 1]];
    else if (e.target.classList.contains('down') && i < chk.criteria.length - 1) [chk.criteria[i + 1], chk.criteria[i]] = [chk.criteria[i], chk.criteria[i + 1]];
    else return;
    render();
  });
  form.querySelector('#add-crit').onclick = () => { collect(); chk.criteria.push({ id: '', title: '', type: 'yes_no', weight: 10, na_allowed: false }); render(); rows.lastElementChild.querySelector('.f-title').focus(); };

  root.querySelector('#chk-pick').onchange = e => CQA.go('checklist', { id: e.target.value });
  root.querySelector('#new-chk').onclick = () => { collect(); chk = { ...chk, id: undefined, name: chk.name + ' (копия)', version: 0 }; form.querySelector('#chk-name').value = chk.name; toast('Создана копия — сохраните её'); };
  root.querySelector('#del-chk').onclick = async () => { if (!confirm('Удалить чек-лист?')) return; try { await CQA.api.deleteChecklist(chk.id); CQA.go('checklist'); } catch (err) { toast(err.message, 'err'); } };
  root.querySelector('#save-chk').onclick = async () => {
    collect();
    if (chk.criteria.some(c => !c.title)) return toast('У каждого критерия нужно название', 'err');
    try { const saved = await CQA.api.saveChecklist(chk); toast(`Сохранено: ${saved.name} v${saved.version}`); CQA.go('checklist', { id: saved.id }); }
    catch (err) { toast(err.message, 'err'); }
  };
};
