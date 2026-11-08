// View: dialog card — transcript + evaluation + evidence links + auto-checks + appeal + LLM trace
CQA.views.card = async function (root, { id, evalId }) {
  const { esc, el, toast, answerLabel, scoreClass, fmtTs, tsOffset } = CQA.util;
  const [conv, evals] = await Promise.all([CQA.api.conversation(id), CQA.api.evaluations(id)]);
  // default = newest SUCCESSFUL evaluation; a failed run (status "error") is shown only if picked or if nothing else exists
  const ev = evalId ? evals.find(e => e.id === evalId) : (evals.find(e => e.status !== 'error') || evals[0]);
  const chk = ev ? ev.checklist_snapshot : null;
  const isCall = conv.channel === 'voice call';
  const pre = (ev && ev.prechecks) || conv.prechecks;

  root.innerHTML = '';
  root.appendChild(el(`
    <section class="row gap top wrap">
      <button id="back" class="small">← к списку</button>
      <h2 class="grow">${esc(conv.conversation_id)} <span class="pill">${esc(conv.language)}</span> <span class="muted">${isCall ? '📞 звонок' : '💬 чат'}</span> <span class="muted small">${esc(conv.dataset_group_id || '')}</span></h2>
      ${evals.length > 1 ? `<select id="ev-sel">${evals.map(e => `<option value="${e.id}" ${ev && e.id === ev.id ? 'selected' : ''}>${new Date(e.created_at).toLocaleString('ru-RU')} · ${e.checklist_snapshot ? `${esc(e.checklist_snapshot.name)} v${e.checklist_snapshot.version} · ${CQA.util.fmtTotal(e.final_totals.total)}` : '⚠ ошибка оценки'}</option>`).join('')}</select>` : ''}
      ${ev && ev.status !== 'error' ? `<button id="trace-btn" class="small" title="Промпты и сырые ответы модели — доказательство живого анализа">Показать трассу LLM</button>` : ''}
      <button id="reeval" class="primary">${ev ? 'Переоценить' : 'Оценить'}</button>
    </section>`));

  const layout = el(`<section class="card-layout"><div class="transcript panel"></div><div class="results panel"></div></section>`);
  root.appendChild(layout);
  const tPane = layout.querySelector('.transcript'), rPane = layout.querySelector('.results');

  // ---------- transcript ----------
  let audio = null;
  if (isCall && conv.audio_url) {
    tPane.appendChild(el(`<div class="audio"><audio id="player" controls preload="none" src="${esc(conv.audio_url)}"></audio><a href="${esc(conv.audio_url)}" target="_blank" rel="noopener" class="muted small">открыть запись ↗</a></div>`));
    audio = tPane.querySelector('#player');
  }
  const s = pre.stats, flagsByTurn = {};
  pre.flags.forEach(f => (flagsByTurn[f.turn] = flagsByTurn[f.turn] || []).push(f.flag));
  tPane.appendChild(el(`<div class="callstats muted small">
    ${s.duration_sec != null ? `⏱ ${Math.round(s.duration_sec)} с · ` : ''}${s.avg_operator_latency_sec != null ? `ответ оператора в среднем ${s.avg_operator_latency_sec} с (макс ${s.max_operator_latency_sec}) · ` : ''}${s.operator_talk_share != null ? `доля слов оператора ${Math.round(s.operator_talk_share * 100)}% · ` : ''}завершил: ${esc(s.ended_by || '—')}
  </div>`));
  tPane.appendChild(el(`<div class="turns">${conv.transcript.map((t, i0) => { const i = i0 + 1; return `
    <div class="turn ${esc(t.speaker)}" id="turn-${i}" data-i="${i0}">
      <div class="meta"><span class="idx">#${i}</span> <span class="spk">${esc(t.speaker)}</span> <span class="ts">${esc(fmtTs(t.timestamp))}</span>${isCall && audio ? ` <button class="seek small" title="к фрагменту записи">▶</button>` : ''}${(flagsByTurn[i] || []).map(f => `<span class="flag" title="автопроверка">⚠ ${esc(f)}</span>`).join('')}</div>
      <div class="text">${esc(t.text)}</div>
    </div>`; }).join('')}</div>`));
  tPane.onclick = e => {
    const b = e.target.closest('.seek'); if (!b || !audio) return;
    audio.currentTime = tsOffset(conv.transcript, +b.closest('.turn').dataset.i); audio.play().catch(() => {});
  };

  function highlight(turns, range) {
    tPane.querySelectorAll('.turn').forEach(t => t.classList.remove('hl', 'range'));
    if (range) for (let i = range[0]; i <= range[1]; i++) tPane.querySelector(`#turn-${i}`)?.classList.add('range');
    turns.forEach(i => tPane.querySelector(`#turn-${i}`)?.classList.add('hl'));
    const first = turns[0] ?? (range ? range[0] : null);
    if (first != null) tPane.querySelector(`#turn-${first}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // ---------- results ----------
  if (ev && ev.status === 'error') {
    rPane.innerHTML = `<div class="panel err"><b>Ошибка оценки:</b> ${esc(ev.error || '')}<br><span class="muted small">${evals.some(e => e.status !== 'error') ? 'Это неудачный запуск — в списке и в results.json используется последняя успешная оценка (выберите её выше).' : 'Успешной оценки нет — в results.json этот диалог уйдёт со status="error".'} Нажмите «Переоценить».</span></div>`;
  } else if (!ev) {
    rPane.innerHTML = `<p class="muted">Диалог ещё не оценён. Нажмите «Оценить».</p>`;
    if (pre.flags.length) rPane.appendChild(el(`<div class="precheck"><b>Автопроверки:</b> ${pre.flags.map(f => `<span class="flag">⚠ ${esc(f.flag)} #${f.turn}</span>`).join(' ')}</div>`));
  } else {
    const ft = ev.final_totals, at = ev.ai_totals;
    const changed = Object.keys(ev.decisions).length > 0;
    rPane.appendChild(el(`
      <div class="total-box ${scoreClass(ft.total)}">
        <div><div class="muted">Итоговый балл</div><div class="big">${CQA.util.fmtTotal(ft.total)}<span class="muted">/${ft.total_scale}</span></div>
          ${ft.total == null ? '<div class="muted small">нет применимых критериев — итог не считается</div>' : ''}
          ${changed ? `<div class="muted">AI: ${CQA.util.fmtTotal(at.total)} → после проверки: <b>${CQA.util.fmtTotal(ft.total)}</b></div>` : ''}</div>
        <div class="right">
          ${ft.gross_error ? `<div class="gross-flag">⚠ Грубая ошибка: ${ft.gross_error_criteria.map(c => esc(chk.criteria.find(x => x.id === c)?.title || c)).join(', ')}</div>` : ''}
          <div class="muted small">${esc(chk.name)} v${chk.version} · ${esc(ev.meta.provider)}/${esc(ev.meta.model)} · ${ev.meta.mode}${ev.meta.samples > 1 ? ` ×${ev.meta.samples}` : ''} · ${ev.meta.usage.calls} вызовов, ${ev.meta.usage.prompt_tokens + ev.meta.usage.completion_tokens} ток.${ev.meta.calibration_examples ? ` · ${ev.meta.calibration_examples} калибр. примеров` : ''}</div>
          <div class="sections">${Object.entries(ft.sections).map(([n, sc]) => `<span class="sec"><b>${esc(n)}</b> ${sc.score == null ? 'N/A' : sc.score}${sc.na ? ` <i class="muted">(N/A: ${sc.na})</i>` : ''}</span>`).join('')}</div>
        </div>
      </div>`));
    if (pre.flags.length) rPane.appendChild(el(`<div class="precheck"><b>Автопроверки (regex, подсказки для модели):</b> ${pre.flags.map(f => `<button class="flag link" data-t="${f.turn}">⚠ ${esc(f.flag)} #${f.turn}</button>`).join(' ')}</div>`));
    rPane.querySelectorAll('.flag.link').forEach(b => b.onclick = () => highlight([+b.dataset.t]));

    const list = el(`<div class="crit-list"></div>`);
    for (const c of chk.criteria) {
      const r = ev.ai[c.id], fin = ev.final_answers[c.id], d = ev.decisions[c.id], tot = ft.criteria[c.id];
      const isNA = fin === 'N/A', isGE = c.type === 'gross_error', geHit = isGE && fin === 'yes';
      const node = el(`
        <div class="crit ${isNA ? 'na' : ''} ${geHit ? 'gross' : ''}">
          <div class="crit-head">
            <div class="grow"><div class="muted small">${esc(c.section || '')}${isGE ? ' · без баллов' : ''}</div><b>${esc(c.title)}</b></div>
            <div class="ans ${isNA ? '' : isGE ? (geHit ? 'bad' : 'good') : tot.score >= 1 ? 'good' : tot.score > 0 ? 'mid' : 'bad'}">${geHit ? '⚠ ' : ''}${esc(answerLabel(c, fin))}</div>
            <div class="pts">${isGE ? '<div class="muted small">флаг</div>' : `<div>${isNA ? '—' : `${tot.points} б.`}</div><div class="muted small">вес ${c.weight}${!isNA && tot.effective_weight !== c.weight ? ` → ${tot.effective_weight}%` : ''}</div>`}</div>
          </div>
          ${d ? `<div class="decision">Проверяющий: <b>${d.decision === 'confirm' ? 'подтвердил' : `изменил ${esc(answerLabel(c, d.original_answer))} → ${esc(answerLabel(c, d.final_answer))}`}</b> — ${esc(d.reason)}</div>` : ''}
          <div class="crit-body">
            <p>${esc(r.explanation)}</p>
            ${isNA ? `<p class="na-reason"><b>Почему N/A:</b> ${esc(r.na_reason || c.na_hint || '')}</p>` : ''}
            <div class="row gap wrap">
              ${r.evidence_turns.length
                ? r.evidence_turns.map(i => `<button class="small ev" data-i="${i}">реплика #${i}</button>`).join('')
                : `<button class="small range-btn">действие не найдено · проверен участок #${r.checked_range[0]}–#${r.checked_range[1]}</button>`}
              <span class="muted small">уверенность ${Math.round(r.confidence * 100)}%${r.votes ? ` · голоса: ${r.votes.join(', ')}` : ''}</span>
              <span class="grow"></span>
              <button class="small appeal-btn">Оспорить</button>
            </div>
            <form class="appeal-form hidden">
              <textarea placeholder="Комментарий к апелляции: почему оценка неверна?" required></textarea>
              <div class="row gap"><button class="primary small">Отправить апелляцию</button><button type="button" class="small cancel">Отмена</button></div>
            </form>
          </div>
        </div>`);
      node.querySelector('.crit-head').onclick = () => highlight(r.evidence_turns, r.evidence_turns.length ? null : r.checked_range);
      node.querySelectorAll('.ev').forEach(b => b.onclick = e => { e.stopPropagation(); highlight([+b.dataset.i]); });
      node.querySelector('.range-btn')?.addEventListener('click', e => { e.stopPropagation(); highlight([], r.checked_range); });
      const form = node.querySelector('.appeal-form');
      node.querySelector('.appeal-btn').onclick = () => form.classList.toggle('hidden');
      node.querySelector('.cancel').onclick = () => form.classList.add('hidden');
      form.onsubmit = async e => {
        e.preventDefault();
        try { await CQA.api.appeal(ev.id, { criterion_id: c.id, comment: form.querySelector('textarea').value }); toast('Апелляция сохранена — видна проверяющему'); CQA.refreshBadge(); CQA.go('card', { id }); }
        catch (err) { toast(err.message, 'err'); }
      };
      list.appendChild(node);
    }
    rPane.appendChild(list);

    root.querySelector('#trace-btn').onclick = async e => {
      let box = root.querySelector('#trace-box');
      if (box) { box.remove(); e.target.textContent = 'Показать трассу LLM'; return; }
      const t = await CQA.api.trace(ev.id);
      box = el(`<section id="trace-box" class="panel"><h2>Трасса LLM — ${t.trace.length} вызовов (${esc(t.meta.provider)}/${esc(t.meta.model)})</h2>
        <p class="muted small">Полные промпты и сырые ответы модели для этой оценки. Это доказательство, что система анализирует каждый диалог вживую, а не воспроизводит заранее подготовленные результаты.</p>
        ${t.trace.map((x, i) => `<details><summary><b>${esc(x.label)}</b> <span class="muted small">промпт ${x.prompt.length} симв. · ответ ${x.raw_response.length} симв.</span></summary>
          ${i === 0 ? `<div class="muted small">SYSTEM PROMPT</div><pre>${esc(x.system_prompt)}</pre>` : ''}<div class="muted small">PROMPT</div><pre>${esc(x.prompt)}</pre><div class="muted small">RAW RESPONSE</div><pre>${esc(x.raw_response)}</pre></details>`).join('')}
      </section>`);
      root.appendChild(box); e.target.textContent = 'Скрыть трассу'; box.scrollIntoView({ behavior: 'smooth' });
    };
  }

  root.querySelector('#back').onclick = () => CQA.go('dialogs');
  root.querySelector('#ev-sel')?.addEventListener('change', e => CQA.go('card', { id, evalId: e.target.value }));
  root.querySelector('#reeval').onclick = async e => {
    const chks = await CQA.api.checklists();
    const chkId = chks.length > 1 ? prompt('ID чек-листа:\n' + chks.map(c => `${c.id} — ${c.name} v${c.version}`).join('\n'), chk ? chk.id : chks[0].id) : chks[0].id;
    if (!chkId) return;
    e.target.disabled = true; e.target.textContent = 'Оцениваю…';
    try { await CQA.api.evaluate({ conversation_id: id, checklist_id: chkId }); toast('Готово'); CQA.go('card', { id }); }
    catch (err) { toast(err.message, 'err'); e.target.disabled = false; e.target.textContent = 'Оценить'; }
  };
};
