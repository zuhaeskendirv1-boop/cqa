CQA.util = {
  esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); },
  el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; },
  toast(msg, kind = 'ok') {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = `toast ${kind}`;
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), 3200);
  },
  answerLabel(crit, ans) {
    if (ans === 'N/A' || ans === null || ans === undefined) return 'N/A';
    if (crit.type === 'gross_error') return ans === 'yes' ? 'Да — есть' : 'Нет';
    if (crit.type === 'yes_no') return ans === 'yes' ? 'Да' : 'Нет';
    const lab = crit.scale && crit.scale.labels && crit.scale.labels[String(ans)];
    return lab ? `${ans} — ${lab}` : `${ans} / ${crit.scale ? crit.scale.max : ''}`;
  },
  scaleValues(crit) {
    if (crit.scale && crit.scale.steps && crit.scale.steps.length) return crit.scale.steps;
    const v = []; for (let i = crit.scale.min; i <= crit.scale.max; i++) v.push(i); return v;
  },
  answerOptions(crit) {
    const o = [];
    if (crit.type === 'yes_no' || crit.type === 'gross_error') o.push(['yes', CQA.util.answerLabel(crit, 'yes')], ['no', CQA.util.answerLabel(crit, 'no')]);
    else CQA.util.scaleValues(crit).forEach(v => o.push([String(v), CQA.util.answerLabel(crit, v)]));
    if (crit.na_allowed) o.push(['N/A', 'N/A']);
    return o;
  },
  bestAnswer(crit) { return crit.type === 'gross_error' ? 'no' : crit.type === 'yes_no' ? 'yes' : String(Math.max(...CQA.util.scaleValues(crit))); },
  scoreClass(total) { return total == null ? '' : total >= 80 ? 'good' : total >= 60 ? 'mid' : 'bad'; },
  fmtTotal(t) { return t == null ? '—' : Number.isInteger(t) ? String(t) : t.toFixed(2); },
  fmtTs(ts) {
    if (!ts) return '';
    if (/^\d{2}:\d{2}$/.test(ts)) return ts;
    const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  },
  // seconds offset of a turn inside a voice call (for the audio player)
  tsOffset(transcript, i) {
    const t0 = new Date(transcript[0].timestamp), t = new Date(transcript[i].timestamp);
    return isNaN(t0) || isNaN(t) ? 0 : Math.max(0, (t - t0) / 1000);
  },
};
