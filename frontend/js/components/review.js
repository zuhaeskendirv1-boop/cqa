// View: reviewer queue — sees disputed score, comment, dialog fragment; confirms or changes with a reason
CQA.views.review = async function (root) {
  const { esc, el, toast, answerLabel, answerOptions } = CQA.util;
  const appeals = await CQA.api.appeals();
  root.innerHTML = '';
  root.appendChild(el(`<section class="panel"><h2>Апелляции</h2><p class="muted small">Проверяющий видит спорную оценку, комментарий оператора и фрагмент диалога. Решение пересчитывает итог.</p></section>`));
  if (!appeals.length) { root.appendChild(el(`<section class="panel muted">Апелляций нет.</section>`)); return; }

  for (const a of appeals) {
    const [ev, conv] = await Promise.all([CQA.api.evaluation(a.evaluation_id), CQA.api.conversation(a.conversation_id)]);
    const chk = ev.checklist_snapshot, c = chk.criteria.find(x => x.id === a.criterion_id), r = ev.ai[a.criterion_id];
    const frag = (r.evidence_turns.length ? r.evidence_turns : [r.checked_range[0], r.checked_range[1]]);
    const lo = Math.max(0, Math.min(...frag) - 1), hi = Math.min(conv.transcript.length - 1, Math.max(...frag) + 1);
    const node = el(`<section class="panel appeal ${a.status}">
      <div class="row gap wrap">
        <b>${esc(a.conversation_id)}</b> · <span>${esc(c.title)}</span>
        <span class="status ${a.status}">${a.status === 'open' ? 'открыта' : 'решена'}</span>
        <span class="grow"></span>
        <button class="small open-card">открыть карточку</button>
      </div>
      <div class="grid2 top">
        <div>
          <div class="muted small">Оценка AI</div>
          <div><b>${esc(answerLabel(c, a.disputed_answer))}</b> — ${esc(r.explanation)}</div>
          <div class="muted small top">Комментарий (${esc(a.author)}):</div>
          <blockquote>${esc(a.comment)}</blockquote>
          ${a.decision ? `<div class="decision">Решение: <b>${a.decision.decision === 'confirm' ? 'подтверждено' : `изменено → ${esc(answerLabel(c, a.decision.final_answer))}`}</b> — ${esc(a.decision.reason)} <i class="muted">(${esc(a.decision.reviewer)})</i></div>` : ''}
        </div>
        <div class="fragment">
          <div class="muted small">Фрагмент диалога (#${lo}–#${hi})</div>
          ${conv.transcript.slice(lo, hi + 1).map((t, k) => `<div class="turn ${esc(t.speaker)} ${r.evidence_turns.includes(lo + k) ? 'hl' : ''}"><span class="idx">#${lo + k}</span> <b>${esc(t.speaker)}</b>: ${esc(t.text)}</div>`).join('')}
        </div>
      </div>
      ${a.status === 'open' ? `
      <form class="decide row gap wrap top">
        <select class="d-kind"><option value="confirm">Подтвердить оценку</option><option value="change">Изменить оценку</option></select>
        <select class="d-ans hidden">${answerOptions(c).map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select>
        <input class="d-reason grow" placeholder="Причина решения (обязательно)" required>
        <button class="primary">Сохранить решение</button>
      </form>` : ''}
    </section>`);
    node.querySelector('.open-card').onclick = () => CQA.go('card', { id: a.conversation_id, evalId: a.evaluation_id });
    const f = node.querySelector('form.decide');
    if (f) {
      f.querySelector('.d-kind').onchange = e => f.querySelector('.d-ans').classList.toggle('hidden', e.target.value !== 'change');
      f.onsubmit = async e => {
        e.preventDefault();
        const kind = f.querySelector('.d-kind').value;
        let ans = f.querySelector('.d-ans').value; if (c.type === 'scale' && ans !== 'N/A') ans = +ans;
        try { const res = await CQA.api.decide(a.id, { decision: kind, new_answer: kind === 'change' ? ans : null, reason: f.querySelector('.d-reason').value }); toast(`Решение сохранено. Новый итог: ${res.evaluation.final_totals.total}`); CQA.refreshBadge(); CQA.go('review'); }
        catch (err) { toast(err.message, 'err'); }
      };
    }
    root.appendChild(node);
  }
};
