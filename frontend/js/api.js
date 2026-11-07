// Thin API client. Change window.CQA_API_BASE to point at a remote backend (e.g. when embedded in Dynamics 365).
window.CQA = window.CQA || {};
CQA.role = { name: localStorage.getItem('cqa_role') || 'operator', token: localStorage.getItem('cqa_token') || '' };
CQA.api = (() => {
  const BASE = (window.CQA_API_BASE || '') + '/api';
  async function req(method, path, body) {
    const headers = { 'X-Role': CQA.role.name, 'X-Token': CQA.role.token };
    if (body) headers['Content-Type'] = 'application/json';
    const r = await fetch(BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }
  return {
    checklists: () => req('GET', '/checklists'),
    checklist: (id) => req('GET', `/checklists/${id}`),
    saveChecklist: (c) => req('POST', '/checklists', c),
    deleteChecklist: (id) => req('DELETE', `/checklists/${id}`),
    conversations: () => req('GET', '/conversations'),
    conversation: (id) => req('GET', `/conversations/${id}`),
    importUrl: (url) => req('POST', '/conversations/import', { url }),
    importPayload: (payload) => req('POST', '/conversations/import', { payload }),
    evaluate: (b) => req('POST', '/evaluate', b),
    batch: (b) => req('POST', '/evaluate/batch', b),
    batchStatus: () => req('GET', '/evaluate/batch/status'),
    evaluations: (cid) => req('GET', `/evaluations${cid ? '?conversation_id=' + encodeURIComponent(cid) : ''}`),
    evaluation: (id) => req('GET', `/evaluations/${id}`),
    trace: (id) => req('GET', `/evaluations/${id}/trace`),
    appeal: (eid, b) => req('POST', `/evaluations/${eid}/appeals`, b),
    appeals: (status) => req('GET', `/appeals${status ? '?status=' + status : ''}`),
    decide: (aid, b) => req('POST', `/appeals/${aid}/decision`, b),
    groundTruth: () => req('GET', '/ground_truth'),
    saveGroundTruth: (b) => req('POST', '/ground_truth', b),
    deleteGroundTruth: (cid) => req('DELETE', `/ground_truth/${cid}`),
    calibrationReport: () => req('GET', '/calibration/report'),
    stats: () => req('GET', '/stats'),
    exportUrl: () => BASE + '/export.csv',
    groups: () => req('GET', '/groups'),
    resultsUrl: (group) => BASE + '/export/results.json' + (group ? '?group=' + encodeURIComponent(group) : ''),
    writeResults: (b) => req('POST', '/export/results/write', b),
  };
})();
