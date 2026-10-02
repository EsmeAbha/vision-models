/* Local Vision: the chat front end from the design canvas.
 *
 * The sidebar is not hard-coded: models come from /api/models (app.py's
 * MODELS) and skills from /api/skills (ocr_skills/*.json). So the page shows
 * exactly what the machine can actually run, and gains a row when app.py
 * gains a model rather than when this file is edited.
 */

const ICON = {
  mark: '<circle cx="12" cy="12" r="9"/><path d="M14.3 3.3L9 12"/><path d="M20.7 9.5L10.4 9.5"/><path d="M16.9 19.2L12 10.5"/><path d="M9.7 20.7L15 12"/><path d="M3.3 14.5L13.6 14.5"/><path d="M7.1 4.8L12 13.5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h4"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M3 15h18"/><path d="M10 10v10"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  send: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
  camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  clip: '<rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 4H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  down: '<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 21h16"/>',
  x: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  fields: '<rect x="3" y="4" width="5" height="5" rx="1"/><rect x="3" y="15" width="5" height="5" rx="1"/><path d="M11 6.5h10"/><path d="M11 17.5h10"/>',
};

function svg(name, size) {
  const s = size || 16;
  return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[name] || ''}</svg>`;
}

/* ------------------------------------------------------------------ state */

const S = {
  sidebar: window.innerWidth >= 900,
  models: [],
  skills: [],
  model: null,
  skill: null,
  file: null,
  prompt: '',
  pages: '',
  menu: null,
  msgs: [],
  busy: false,
  docTypes: [],
  docType: null,   // id of the chosen type, or null for a plain read
  chosen: [],      // field names ticked in the picker
  extra: '',       // extra field names typed in by hand
  templates: [],   // spreadsheets the found fields can be dropped into
  history: [],     // documents read in earlier sessions
  expanded: [],    // rows opened to show their full detail
  deals: [],       // deal workbooks on disk
  deal: null,      // the deal documents are being read into, if any
  newDeal: false,  // the new-deal form is open
  dealName: '',    // what is typed into the new-deal form
  addField: '',    // a mapping row being added by hand
  addCell: '',
  dealBusy: false,
  dealError: null,
};

const el = (id) => document.getElementById(id);
const modelById = (id) => S.models.find((m) => m.id === id) || S.models[0] || null;

function toast(text) {
  const t = el('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 3600);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* keep status */ }
    throw new Error(detail);
  }
  return r.status === 204 ? null : r.json();
}

/* ---------------------------------------------------------------- actions */

/* A model that runs a fixed pipeline has a stand-in string where its prompt
 * would be ("(no prompt needed ...)"). That belongs in the placeholder, not
 * in the box, so it never looks like something you could edit. */
function promptFor(m) {
  return m && m.takes_prompt ? m.prompt : '';
}

function pickModel(id) {
  S.model = id;
  S.skill = null;
  // Only overwrite a prompt the user has not touched.
  if (!S.promptDirty) S.prompt = promptFor(modelById(id));
  S.menu = null;
  render();
}

function pickSkill(id) {
  const k = S.skills.find((s) => s.id === id);
  if (!k) return;
  // This is all a skill does: set the model and fill in its prompt.
  S.skill = id;
  S.model = k.model;
  S.prompt = promptFor(modelById(k.model));
  S.promptDirty = false;
  S.menu = null;
  render();
  toast(`${k.name}: model set to ${k.model_short}`);
}

const docTypeById = (id) => S.docTypes.find((d) => d.id === id) || null;

/* Picking a document type picks its reader too, because each type names the
 * model that suits it. The field list is only ever searched for afterwards,
 * never put to the model: app.py's compose_prompt carries the measurements
 * that settled that, and handing DeepSeek-OCR a field list made it loop. */
function pickDocType(id) {
  const d = docTypeById(id);
  if (!d) return;
  S.docType = id;
  S.chosen = d.fields.slice();
  S.model = d.reader;
  S.prompt = promptFor(modelById(d.reader));
  S.promptDirty = false;
  S.skill = null;
  S.menu = null;
  render();
  toast(`${d.name}: reading with ${d.reader_short}`);
}

function clearDocType() {
  S.docType = null;
  S.chosen = [];
  S.extra = '';
  S.menu = null;
  render();
}

function toggleField(name) {
  S.chosen = S.chosen.includes(name)
    ? S.chosen.filter((f) => f !== name)
    : S.chosen.concat([name]);
  render();
}

function wantsFields() {
  return S.chosen.length > 0 || S.extra.trim().length > 0
         || dealFields().length > 0;
}

/* Everything to search this transcript for: the ticked boxes, anything typed
 * in, and whatever the open deal's workbook has room for. Without the last
 * of those, a row added to the mapping by hand would never be filled,
 * because nothing would have looked for it. */
function fieldsWanted() {
  const out = [];
  S.chosen.concat(dealFields()).forEach((f) => {
    if (f && !out.includes(f)) out.push(f);
  });
  return out;
}

/* ------------------------------------------------------------------- deals */

async function loadHistory() {
  try {
    S.history = await api('/api/history');
  } catch (e) { /* the sidebar still works without it */ }
}

/* When a run happened is more useful than its exact timestamp. */
function dayOf(at) {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return 'Earlier';
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const days = Math.floor((midnight - when) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days + 1} days ago`;
  return when.toLocaleDateString(undefined,
                                 { day: 'numeric', month: 'short' });
}

/* Reopen a document read in an earlier session. The transcript is kept, so
 * this costs nothing: re-running it would mean another model load. */
async function reopen(id) {
  try {
    const entry = await api(`/api/history/${id}`);
    const model = modelById(entry.model);
    S.msgs.push({
      role: 'you', text: '', file: { name: entry.file, pdf: false },
    });
    S.msgs.push({
      role: 'run', runId: entry.id, model: entry.model,
      modelName: entry.model_name || (model ? model.name : entry.model),
      file: entry.file, status: 'done', steps: [], text: entry.text,
      annotated: entry.annotated, error: null, elapsed: entry.elapsed,
      saved: [], docType: null, docTypeName: '',
      want: (entry.fields || []).length ? { fields: [], extra: '' } : null,
      fieldRows: (entry.fields || []).length ? entry.fields : null,
      fieldSummary: entry.field_summary || '',
      fieldsBusy: false, fieldError: null, reopened: true,
      tab: (entry.fields || []).length ? 'fields' : null,
    });
    render();
  } catch (e) {
    toast(`Could not reopen that: ${e.message}`);
  }
}

async function forget(id, event) {
  event.stopPropagation();
  try {
    await api(`/api/history/${id}`, { method: 'DELETE' });
    S.history = S.history.filter((h) => h.id !== id);
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function loadDeals() {
  try {
    S.deals = await api('/api/deals');
  } catch (e) { /* the list is a convenience; the rest of the page still works */ }
}

async function openDeal(id) {
  S.menu = null;
  S.dealError = null;
  try {
    S.deal = await api(`/api/deals/${id}`);
  } catch (e) {
    S.dealError = e.message;
  }
  render();
}

function closeDeal() {
  S.deal = null;
  S.menu = null;
  render();
}

/* Create a deal from a workbook you supply. Nothing is written to it: the
 * reply is a proposed mapping to check before anything lands. */
async function startDeal(file) {
  const name = S.dealName.trim();
  if (!name) { toast('Give the deal a name first.'); return; }
  if (!file) return;
  S.dealBusy = true;
  S.dealError = null;
  render();
  try {
    const body = new FormData();
    body.append('name', name);
    body.append('file', file, file.name);
    S.deal = await api('/api/deals', { method: 'POST', body });
    S.dealName = '';
    S.newDeal = false;
    S.menu = null;
    await loadDeals();
    await loadHistory();
    toast(`${S.deal.name}: ${S.deal.mapping.length} field(s) located`);
  } catch (e) {
    S.dealError = e.message;
  }
  S.dealBusy = false;
  render();
}

function chooseWorkbook() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.xlsx,.xlsm';
  input.onchange = () => startDeal(input.files[0]);
  input.click();
}

function setCell(field, cell) {
  const row = S.deal.mapping.find((r) => r.field === field);
  if (row) row.cell = cell.toUpperCase();
}

function dropFromMapping(field) {
  S.deal.mapping = S.deal.mapping.filter((r) => r.field !== field);
  render();
}

async function confirmMapping() {
  S.dealBusy = true;
  S.dealError = null;
  render();
  try {
    S.deal = await api(`/api/deals/${S.deal.id}/mapping`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapping: S.deal.mapping }),
    });
    toast('Mapping saved. Later documents will reuse it.');
  } catch (e) {
    S.dealError = e.message;
  }
  S.dealBusy = false;
  render();
}

/* Put this document's fields into the deal's workbook. */
async function addToDeal(msg) {
  msg.dealBusy = true;
  msg.dealError = null;
  render();
  try {
    msg.dealResult = await api(`/api/deals/${S.deal.id}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run: msg.runId }),
    });
    S.deal = await api(`/api/deals/${S.deal.id}`);
    const n = msg.dealResult.written.length;
    toast(n ? `Wrote ${n} cell(s) into ${S.deal.name}`
            : 'Nothing new for this deal.');
  } catch (e) {
    msg.dealError = e.message;
  }
  msg.dealBusy = false;
  render();
}

/* Every field name the scan can recognise, for the suggestion list. The
 * vocabulary is small, which is why a template with its own labels needs
 * rows added by hand. */
function knownFields() {
  const seen = [];
  S.docTypes.forEach((d) => d.fields.forEach((f) => {
    if (!seen.includes(f)) seen.push(f);
  }));
  return seen;
}

const dealFields = () =>
  (S.deal && S.deal.mapping) ? S.deal.mapping.map((r) => r.field) : [];

function addMappingRow() {
  const field = S.addField.trim();
  const cell = S.addCell.trim().toUpperCase();
  if (!field) { toast('Name the field first.'); return; }
  if (!/^[A-Z]{1,3}[1-9][0-9]*$/.test(cell)) {
    toast(`${cell || 'That'} is not a cell reference, for example B7.`);
    return;
  }
  if (S.deal.mapping.some((r) => r.field.toLowerCase() === field.toLowerCase())) {
    toast(`${field} is already mapped.`);
    return;
  }
  S.deal.mapping.push({ field, cell, sheet: '', label_cell: '',
                        label_text: '', matched: '', how: 'set by hand' });
  S.addField = '';
  S.addCell = '';
  render();
}

const isOpen = (key) => S.expanded.includes(key);

function toggleExpanded(key, event) {
  if (event) event.stopPropagation();
  S.expanded = isOpen(key) ? S.expanded.filter((k) => k !== key)
                           : S.expanded.concat([key]);
  render();
}

/* A row plus its disclosure. The row itself is a button, so the toggle
 * cannot live inside it: a button inside a button is not valid markup and
 * browsers disagree about which one a click reaches. */
function expandable(button, key, detail) {
  const wrap = document.createElement('div');
  wrap.className = 'pick-wrap';
  const row = document.createElement('div');
  row.className = 'pick-row';
  row.append(button);

  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'pick-more';
  more.setAttribute('aria-expanded', String(isOpen(key)));
  more.setAttribute('aria-label', isOpen(key) ? 'Show less' : 'Show more');
  more.innerHTML = svg('chev', 15);
  more.onclick = (e) => toggleExpanded(key, e);
  row.append(more);
  wrap.append(row);

  if (isOpen(key)) {
    button.classList.add('open');
    const box = document.createElement('div');
    box.className = 'pick-detail';
    detail.filter(([, v]) => v).forEach(([label, value]) => {
      const line = document.createElement('div');
      line.className = 'detail-line';
      const k = document.createElement('span');
      k.className = 'detail-key';
      k.textContent = label;
      const v = document.createElement('span');
      v.textContent = value;
      line.append(k, v);
      box.append(line);
    });
    wrap.append(box);
  }
  return wrap;
}

const templatesFor = (docTypeName) =>
  S.templates.filter((t) => t.doc_type === docTypeName);

/* Copy the template and write this run's fields into the copy. */
async function fillTemplate(msg, templateId) {
  msg.fillBusy = templateId;
  msg.fillError = null;
  render();
  try {
    msg.filled = await api(`/api/runs/${msg.runId}/template`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: templateId }),
    });
    toast(`Wrote ${msg.filled.written.length} cell(s) into ${msg.filled.file}`);
  } catch (e) {
    msg.fillError = e.message;
  }
  msg.fillBusy = null;
  render();
}

/* Pull fields out of a run that has already finished, using whatever is
 * selected in the picker right now. Also records the type on the message, so
 * the matching spreadsheet template appears once the rows are in. */
async function extractNow(msg) {
  const type = docTypeById(S.docType);
  msg.docTypeName = type ? type.name : '';
  msg.want = { fields: fieldsWanted(), extra: S.extra };
  await extractFields(msg, msg.want.fields, msg.want.extra);
}

/* Search a finished run's transcript for the ticked fields. No model runs. */
async function extractFields(msg, wanted, extra) {
  if (!wanted.length && !extra.trim()) return;
  msg.fieldsBusy = true;
  msg.fieldError = null;
  render();
  try {
    const r = await api(`/api/runs/${msg.runId}/fields`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields: wanted, extra }),
    });
    msg.fieldRows = r.rows;
    msg.fieldSummary = r.summary;
    msg.tab = 'fields';
  } catch (e) {
    msg.fieldError = e.message;
  }
  msg.fieldsBusy = false;
  render();

  // A deal is open, so this document was read into it. Confirming a mapping
  // used to do nothing by itself: you then had to find a button on the
  // Fields tab, and skipping it left the workbook empty with no sign why.
  if (S.deal && msg.fieldRows && msg.fieldRows.some((r) => r.value)) {
    await addToDeal(msg);
  }
}

async function attach(file) {
  if (!file) return;
  try {
    const body = new FormData();
    body.append('file', file, file.name || 'pasted.png');
    S.file = await api('/api/upload', { method: 'POST', body });
    S.menu = null;
    render();
  } catch (e) {
    toast(`Could not attach that: ${e.message}`);
  }
}

function detach() {
  S.file = null;
  render();
}

function newRun() {
  S.msgs = [];
  S.file = null;
  S.prompt = promptFor(modelById(S.model));
  S.promptDirty = false;
  S.skill = null;
  render();
}

async function run() {
  if (S.busy) return;
  if (!S.file) { toast('Add an image or a PDF first.'); return; }
  const m = modelById(S.model);
  if (!m) { toast('No model is available.'); return; }

  S.msgs.push({ role: 'you', text: m.takes_prompt ? S.prompt : '', file: S.file });
  const msg = {
    role: 'run', model: m.id, modelName: m.name, file: S.file.name,
    status: 'running', steps: [], tab: null, text: '', annotated: null,
    error: null, elapsed: null, saved: [],
    // Snapshot the picker now, so editing it mid-run cannot change what this
    // run was asked for.
    docType: S.docType,
    docTypeName: docTypeById(S.docType) ? docTypeById(S.docType).name : '',
    want: wantsFields() ? { fields: fieldsWanted(), extra: S.extra } : null,
    fieldRows: null, fieldSummary: '', fieldsBusy: false, fieldError: null,
  };
  S.msgs.push(msg);
  S.busy = true;
  render();

  let started;
  try {
    started = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: S.file.id, model: m.id,
        prompt: m.takes_prompt ? S.prompt : '', pages: S.pages, dpi: 300,
      }),
    });
  } catch (e) {
    msg.status = 'error';
    msg.error = e.message;
    S.busy = false;
    render();
    return;
  }

  msg.runId = started.id;
  msg.steps = started.steps.map((name, i) => ({ i, name, state: 'wait', note: '' }));
  render();

  const es = new EventSource(`/api/runs/${started.id}/events`);
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === 'step') {
      const s = msg.steps.find((x) => x.i === d.i);
      if (s) { s.state = d.state; s.note = d.note || ''; }
      render();
    } else if (d.type === 'error') {
      msg.status = 'error';
      msg.error = d.error;
      es.close();
      finish(msg);
    } else if (d.type === 'done') {
      es.close();
      loadResult(msg);
    }
  };
  es.onerror = () => {
    es.close();
    // The stream can drop before the run ends; the result endpoint is the
    // authority either way, so ask it rather than reporting a failure here.
    loadResult(msg);
  };
}

async function loadResult(msg) {
  // Both the `done` event and a dropped stream land here; whichever arrives
  // first is the one that counts.
  if (msg._settled) return;
  msg._settled = true;
  try {
    const r = await api(`/api/runs/${msg.runId}`);
    msg.status = r.status;
    msg.text = r.text || '';
    msg.annotated = r.annotated;
    msg.error = r.error;
    msg.elapsed = r.elapsed;
    msg.steps = r.steps.length ? r.steps : msg.steps;
    const m = modelById(msg.model);
    if (!msg.tab && m) msg.tab = m.tabs[0][0];
  } catch (e) {
    msg.status = 'error';
    msg.error = e.message;
  }
  finish(msg);
  // Fields were asked for, so search the transcript now the read is done.
  if (msg.status === 'done' && msg.want) {
    extractFields(msg, msg.want.fields, msg.want.extra);
  }
}

function finish(msg) {
  S.busy = false;
  render();
}

async function copyOut(msg) {
  try {
    await navigator.clipboard.writeText(msg.text);
    toast('Copied the output.');
  } catch (e) {
    toast('The browser would not give access to the clipboard.');
  }
}

async function saveOut(msg) {
  try {
    const r = await api(`/api/runs/${msg.runId}/save`, { method: 'POST' });
    msg.saved = r.saved;
    render();
    toast(`Saved ${r.saved.length} file(s) into outputs/.`);
  } catch (e) {
    toast(`Save failed: ${e.message}`);
  }
}

/* ------------------------------------------------------- getting an image */

function chooseFile() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.png,.jpg,.jpeg,.webp,.bmp,.pdf';
  inp.onchange = () => attach(inp.files[0]);
  inp.click();
  S.menu = null;
  render();
}

async function fromClipboard() {
  S.menu = null;
  render();
  try {
    const items = await navigator.clipboard.read();
    for (const it of items) {
      const type = it.types.find((t) => t.startsWith('image/'));
      if (type) {
        const blob = await it.getType(type);
        return attach(new File([blob], 'pasted.png', { type }));
      }
    }
    toast('No image on the clipboard.');
  } catch (e) {
    toast('Clipboard read was blocked. Press Ctrl+V over the page instead.');
  }
}

async function fromWebcam() {
  S.menu = null;
  render();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280 } });
  } catch (e) {
    toast('No camera available, or permission was refused.');
    return;
  }

  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:fixed;inset:0;z-index:80;display:grid;place-items:center;'
    + 'background:rgba(10,30,45,.72);padding:20px';
  const video = document.createElement('video');
  video.autoplay = true; video.playsInline = true; video.srcObject = stream;
  video.style.cssText = 'max-width:100%;max-height:70vh;border-radius:12px';
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:10px;justify-content:center;margin-top:14px';
  const shoot = document.createElement('button');
  shoot.className = 'btn primary'; shoot.textContent = 'Take the picture';
  const cancel = document.createElement('button');
  cancel.className = 'btn'; cancel.textContent = 'Cancel';
  const box = document.createElement('div');
  box.style.cssText = 'text-align:center';
  box.append(video, bar);
  bar.append(shoot, cancel);
  wrap.append(box);
  document.body.append(wrap);

  const close = () => {
    stream.getTracks().forEach((t) => t.stop());
    wrap.remove();
  };
  cancel.onclick = close;
  shoot.onclick = () => {
    const c = document.createElement('canvas');
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0);
    c.toBlob((blob) => {
      close();
      attach(new File([blob], 'webcam.png', { type: 'image/png' }));
    }, 'image/png');
  };
}

/* ------------------------------------------------- rendering the sidebar */

function sidebar() {
  const node = document.createElement('aside');
  node.className = 'side';

  const brand = document.createElement('div');
  brand.className = 'brand';
  brand.innerHTML = `<div class="mark">${svg('mark', 16)}</div>`
    + '<span class="brand-name">Local Vision</span>';
  const collapse = document.createElement('button');
  collapse.className = 'icon-btn';
  collapse.type = 'button';
  collapse.setAttribute('aria-label', 'Collapse sidebar');
  collapse.innerHTML = svg('panel', 18);
  collapse.onclick = () => { S.sidebar = false; render(); };
  brand.append(collapse);
  node.append(brand);

  const nr = document.createElement('button');
  nr.className = 'new-run';
  nr.type = 'button';
  nr.innerHTML = `${svg('plus', 16)}<span>New run</span>`;
  nr.onclick = newRun;
  node.append(nr);

  // models
  const mg = document.createElement('div');
  mg.className = 'group';
  mg.innerHTML = '<div class="group-label">Models</div>';
  S.models.forEach((m) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'pick model';
    b.setAttribute('aria-pressed', String(m.id === S.model));
    // Everything about the model sits inside the row. Hanging a label off
    // the right edge is what wrapped these names onto two lines.
    b.innerHTML = `<span class="pick-icon">${svg(m.icon, 16)}</span>`
      + '<span class="pick-text">'
      + '<span class="pick-head"><span class="pick-name"></span>'
      + (m.tag ? '<span class="pick-tag"></span>' : '') + '</span>'
      + '<span class="pick-sub"></span></span>';
    b.querySelector('.pick-name').textContent = m.name;
    if (m.tag) b.querySelector('.pick-tag').textContent = m.tag;
    b.querySelector('.pick-sub').textContent = m.sub;
    b.onclick = () => pickModel(m.id);
    mg.append(expandable(b, `model:${m.id}`, [
      ['Does', m.sub],
      ['Gives', m.tabs.map(([, label]) => label).join(', ')],
      ['Prompt', m.takes_prompt
        ? 'Takes one. The skill or document type fills it in.'
        : 'None. It runs a fixed layout pipeline.'],
      ['Called', m.label],
    ]));
  });
  node.append(mg);

  // skills
  if (S.skills.length) {
    const kg = document.createElement('div');
    kg.className = 'group';
    kg.innerHTML = `<div class="group-label"><span class="spark">${svg('spark', 12)}</span>Skills</div>`;
    S.skills.forEach((k) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'pick skill';
      b.setAttribute('aria-pressed', String(k.id === S.skill));
      // The reader and the speed are the two things worth knowing before
      // picking one, so they read on their own line underneath rather than
      // only in a tooltip nobody hovers.
      b.innerHTML = `<span class="pick-icon">${svg(k.icon, 15)}</span>`
        + '<span class="pick-text">'
        + '<span class="pick-head"><span class="pick-name"></span>'
        + '<span class="pick-tag"></span></span>'
        + '<span class="pick-sub"></span></span>';
      b.querySelector('.pick-name').textContent = k.name;
      b.querySelector('.pick-tag').textContent = k.model_short;
      b.querySelector('.pick-sub').textContent = k.gives || k.takes || '';
      b.onclick = () => pickSkill(k.id);
      kg.append(expandable(b, `skill:${k.id}`, [
        ['Reader', k.model_short],
        ['Takes', k.takes],
        ['Gives', k.gives],
        ['Speed', k.speed],
        ['Note', k.note],
      ]));
    });
    node.append(kg);
  }

  // Runs from this page, then everything read in earlier sessions. The
  // first list is what you are working on; the second is why you do not
  // have to read a document twice.
  const runs = S.msgs.filter((m) => m.role === 'run' && !m.reopened);
  if (runs.length) {
    const hg = document.createElement('div');
    hg.className = 'group';
    hg.innerHTML = '<div class="group-label">This session</div>';
    const list = document.createElement('div');
    list.className = 'history';
    runs.slice().reverse().forEach((r) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'history-item';
      b.innerHTML = '<span class="dot"></span><span></span>';
      b.querySelector('span:last-child').textContent =
        `${r.file} to ${r.modelName}`;
      b.title = b.querySelector('span:last-child').textContent;
      b.onclick = () => {
        const card = document.querySelector(`[data-run="${r.runId}"]`);
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      };
      list.append(b);
    });
    hg.append(list);
    node.append(hg);
  }

  const open = new Set(S.msgs.filter((m) => m.role === 'run')
                             .map((m) => m.runId));
  const earlier = S.history.filter((h) => !open.has(h.id));
  if (earlier.length) {
    const eg = document.createElement('div');
    eg.className = 'group';
    eg.innerHTML = '<div class="group-label">Earlier</div>';
    let day = null;
    earlier.forEach((h) => {
      const label = dayOf(h.at);
      if (label !== day) {
        day = label;
        const head = document.createElement('div');
        head.className = 'history-day';
        head.innerHTML = '<span class="dot"></span><span></span>';
        head.querySelector('span:last-child').textContent = label;
        eg.append(head);
      }
      const row = document.createElement('div');
      row.className = 'past-row';
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'history-item past';
      b.innerHTML = '<span class="past-text"><span class="past-file"></span>'
        + '<span class="past-sub"></span></span>';
      b.querySelector('.past-file').textContent = h.file;
      b.querySelector('.past-sub').textContent =
        `${h.model_name}${h.fields ? `, ${h.fields} fields` : ''}`;
      b.title = `${h.file}, read with ${h.model_name}`
        + (h.chars ? `, ${h.chars} characters` : '');
      b.onclick = () => reopen(h.id);
      row.append(b);
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'past-forget';
      x.setAttribute('aria-label', `Forget ${h.file}`);
      x.innerHTML = svg('x', 13);
      x.onclick = (e) => forget(h.id, e);
      row.append(x);
      eg.append(row);
    });
    node.append(eg);
  }

  const foot = document.createElement('div');
  foot.className = 'side-foot';
  foot.innerHTML = '<div><b>Everything runs here.</b> No image and no text '
    + 'leaves this machine.</div>';
  node.append(foot);
  return node;
}

/* --------------------------------------------------- rendering the thread */

function hello() {
  const d = document.createElement('div');
  d.className = 'hello';
  d.innerHTML = '<h1>Read a document, locally</h1>'
    + '<p>Add an image or a PDF, pick a skill or a model, and run it. '
    + 'The models sit on your own GPU, and the first switch to one takes '
    + 'about 10 to 20 seconds while its weights load.</p>';
  const row = document.createElement('div');
  row.className = 'starters';
  S.skills.slice(0, 4).forEach((k) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'starter';
    b.innerHTML = `<span class="pick-icon" style="width:28px;height:28px;border-radius:7px">${svg(k.icon, 15)}</span>`
      + '<span><b></b><small></small></span>';
    b.querySelector('b').textContent = k.name;
    b.querySelector('small').textContent = k.model_short;
    b.onclick = () => pickSkill(k.id);
    row.append(b);
  });
  if (row.children.length) d.append(row);
  return d;
}

function youMsg(m) {
  const d = document.createElement('div');
  d.className = 'msg you';
  const b = document.createElement('div');
  b.className = 'bubble';
  if (m.file) {
    const row = document.createElement('div');
    row.className = 'chip-row';
    const chip = document.createElement('span');
    chip.className = 'chip';
    if (m.file.preview) {
      const img = document.createElement('img');
      img.src = m.file.preview;
      img.alt = '';
      chip.append(img);
    }
    const name = document.createElement('span');
    name.textContent = m.file.pdf
      ? `${m.file.name} · ${m.file.pages} page${m.file.pages === 1 ? '' : 's'}`
      : m.file.name;
    chip.append(name);
    row.append(chip);
    b.append(row);
  }
  if (m.text && m.text.trim()) {
    const p = document.createElement('div');
    p.textContent = m.text;
    b.append(p);
  }
  d.append(b);
  return d;
}

/* The paddle "rendered table" tab is model-produced HTML. Keep the table
 * markup and drop anything that could execute. */
function safeHtml(raw) {
  const host = document.createElement('div');
  host.innerHTML = raw;
  host.querySelectorAll('script,style,iframe,object,embed,link').forEach((n) => n.remove());
  host.querySelectorAll('*').forEach((n) => {
    [...n.attributes].forEach((a) => {
      if (/^on/i.test(a.name) || /javascript:/i.test(a.value)) n.removeAttribute(a.name);
    });
  });
  return host;
}

function runMsg(m) {
  const d = document.createElement('div');
  d.className = 'msg';
  const card = document.createElement('div');
  card.className = 'card';
  if (m.runId) card.dataset.run = m.runId;

  const model = modelById(m.model);
  const head = document.createElement('div');
  head.className = 'card-head';
  head.innerHTML = `<span class="pick-icon">${svg(model ? model.icon : 'eye', 15)}</span>`
    + '<span class="card-title"><strong></strong><small></small></span>';
  head.querySelector('strong').textContent = m.modelName;
  head.querySelector('small').textContent = m.status === 'running'
    ? `reading ${m.file}…`
    : `${m.file}${m.elapsed ? ` · ${m.elapsed.toFixed(1)}s` : ''}`;
  card.append(head);

  // the live timeline
  if (m.steps.length) {
    const steps = document.createElement('div');
    steps.className = 'steps';
    m.steps.forEach((s) => {
      const row = document.createElement('div');
      row.className = `step ${s.state}`;
      row.innerHTML = '<i></i><span></span>';
      row.querySelector('span').textContent = s.name;
      if (s.note) {
        const em = document.createElement('em');
        em.textContent = s.note;
        row.append(em);
      }
      steps.append(row);
    });
    card.append(steps);
  }

  if (m.status === 'error') {
    const body = document.createElement('div');
    body.className = 'card-body';
    const fail = document.createElement('div');
    fail.className = 'fail';
    fail.textContent = m.error || 'The run failed.';
    body.append(fail);
    card.append(body);
    d.append(card);
    return d;
  }

  if (m.status !== 'running' && (m.text || m.annotated)) {
    const tabs = (model ? model.tabs : [['raw', 'Text']])
      .filter(([id]) => id !== 'annot' || m.annotated);
    // Offered on any finished read, not only when a type was chosen before
    // pressing Run. Picking the type afterwards is the common case, and
    // gating on m.want meant the only way to reach fields, and so the
    // template button, was to run the document a second time.
    if (m.text) tabs.unshift(['fields', 'Fields']);
    if (tabs.length > 1) {
      const bar = document.createElement('div');
      bar.className = 'tabs';
      bar.setAttribute('role', 'tablist');
      tabs.forEach(([id, label]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.setAttribute('role', 'tab');
        b.setAttribute('aria-selected', String((m.tab || tabs[0][0]) === id));
        b.textContent = label;
        b.onclick = () => { m.tab = id; render(); };
        bar.append(b);
      });
      card.append(bar);
    }

    const body = document.createElement('div');
    body.className = 'card-body';
    const tab = m.tab || tabs[0][0];
    if (tab === 'fields') {
      body.append(fieldsTable(m));
    } else if (tab === 'annot' && m.annotated) {
      const img = document.createElement('img');
      img.src = m.annotated;
      img.alt = `${m.file} with boxes around the text that was read`;
      body.append(img);
    } else if (tab === 'table') {
      const wrap = document.createElement('div');
      wrap.className = 'tablewrap';
      wrap.append(safeHtml(m.text));
      body.append(wrap);
    } else {
      const pre = document.createElement('pre');
      pre.className = 'out';
      pre.textContent = m.text;
      body.append(pre);
    }
    card.append(body);

    const foot = document.createElement('div');
    foot.className = 'card-foot';
    const note = document.createElement('small');
    note.textContent = m.saved.length
      ? `Saved: ${m.saved.map((f) => f.name).join(', ')}`
      : `${m.text.length} characters`;
    foot.append(note);

    const copy = document.createElement('button');
    copy.className = 'btn';
    copy.type = 'button';
    copy.innerHTML = `${svg('copy', 14)}<span>Copy</span>`;
    copy.onclick = () => copyOut(m);
    foot.append(copy);

    if (m.saved.length) {
      m.saved.forEach((f) => {
        const a = document.createElement('a');
        a.className = 'btn';
        a.href = `/api/runs/${m.runId}/download/${f.i}`;
        a.textContent = f.name.split('.').pop().toUpperCase();
        foot.append(a);
      });
    } else {
      const save = document.createElement('button');
      save.className = 'btn primary';
      save.type = 'button';
      save.innerHTML = `${svg('down', 14)}<span>Save</span>`;
      save.onclick = () => saveOut(m);
      foot.append(save);
    }
    card.append(foot);
  }

  d.append(card);
  return d;
}

/* ------------------------------------------------- rendering the composer */

function composer() {
  const wrap = document.createElement('div');
  wrap.className = 'composer-wrap';
  const box = document.createElement('div');
  box.className = 'composer';

  // drag and drop straight onto the box
  box.ondragover = (e) => { e.preventDefault(); box.classList.add('drop'); };
  box.ondragleave = () => box.classList.remove('drop');
  box.ondrop = (e) => {
    e.preventDefault();
    box.classList.remove('drop');
    if (e.dataTransfer.files[0]) attach(e.dataTransfer.files[0]);
  };

  if (S.file) {
    const row = document.createElement('div');
    row.className = 'attached';
    const chip = document.createElement('span');
    chip.className = 'chip';
    if (S.file.preview) {
      const img = document.createElement('img');
      img.src = S.file.preview;
      img.alt = '';
      chip.append(img);
    }
    const name = document.createElement('span');
    name.textContent = S.file.pdf
      ? `${S.file.name} · ${S.file.pages} page${S.file.pages === 1 ? '' : 's'}`
      : S.file.name;
    chip.append(name);
    const x = document.createElement('button');
    x.type = 'button';
    x.setAttribute('aria-label', `Remove ${S.file.name}`);
    x.innerHTML = svg('x', 13);
    x.onclick = detach;
    chip.append(x);
    row.append(chip);
    box.append(row);
  }

  const deal = dealPanel();
  if (deal) box.append(deal);

  const picker = fieldPicker();
  if (picker) box.append(picker);

  const model = modelById(S.model);
  const ta = document.createElement('textarea');
  ta.id = 'prompt-box';
  ta.rows = 2;
  ta.value = S.prompt;
  ta.placeholder = model && !model.takes_prompt
    ? `${model.name} takes no prompt. It runs a fixed layout pipeline.`
    : 'Tell the model what to read, or leave its default prompt as it is…';
  ta.disabled = !!(model && !model.takes_prompt);
  ta.oninput = () => { S.prompt = ta.value; S.promptDirty = true; };
  ta.onkeydown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); }
  };
  box.append(ta);

  const row = document.createElement('div');
  row.className = 'composer-row';

  const plus = document.createElement('button');
  plus.type = 'button';
  plus.className = 'round';
  plus.setAttribute('aria-label', 'Add an image or a PDF');
  plus.setAttribute('aria-expanded', String(S.menu === 'attach'));
  plus.innerHTML = svg('plus', 17);
  plus.onclick = (e) => {
    e.stopPropagation();
    S.menu = S.menu === 'attach' ? null : 'attach';
    render();
  };
  row.append(plus);

  if (model) {
    const mb = document.createElement('button');
    mb.type = 'button';
    mb.className = 'model-btn';
    mb.setAttribute('aria-expanded', String(S.menu === 'model'));
    mb.innerHTML = `${svg(model.icon, 14)}<span></span>`
      + (model.tag ? '<span class="tag"></span>' : '');
    mb.querySelector('span').textContent = model.name;
    if (model.tag) mb.querySelector('.tag').textContent = model.tag;
    mb.onclick = (e) => {
      e.stopPropagation();
      S.menu = S.menu === 'model' ? null : 'model';
      render();
    };
    row.append(mb);
  }

  const chosenType = docTypeById(S.docType);
  const dt = document.createElement('button');
  dt.type = 'button';
  dt.className = 'model-btn';
  dt.setAttribute('aria-expanded', String(S.menu === 'doctype'));
  const showCount = chosenType && chosenType.fields.length > 0;
  dt.innerHTML = `${svg('fields', 14)}<span></span>`
    + (showCount ? '<span class="tag"></span>' : '');
  dt.querySelector('span').textContent = chosenType ? chosenType.name : 'Fields';
  if (showCount) {
    dt.querySelector('.tag').textContent =
      `${S.chosen.length}/${chosenType.fields.length}`;
  }
  dt.onclick = (e) => {
    e.stopPropagation();
    S.menu = S.menu === 'doctype' ? null : 'doctype';
    render();
  };
  row.append(dt);

  const dealBtn = document.createElement('button');
  dealBtn.type = 'button';
  dealBtn.className = 'model-btn';
  dealBtn.setAttribute('aria-expanded', String(S.menu === 'deal'));
  dealBtn.innerHTML = `${svg('table', 14)}<span></span>`
    + (S.deal ? '<span class="tag"></span>' : '');
  dealBtn.querySelector('span').textContent = S.deal ? S.deal.name : 'Deal';
  if (S.deal) {
    dealBtn.querySelector('.tag').textContent =
      `${Object.keys(S.deal.filled || {}).length}/${S.deal.mapping.length}`;
  }
  dealBtn.onclick = (e) => {
    e.stopPropagation();
    S.menu = S.menu === 'deal' ? null : 'deal';
    render();
  };
  row.append(dealBtn);

  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'run';
  go.setAttribute('aria-label', 'Run');
  go.disabled = S.busy || !S.file;
  go.innerHTML = svg('send', 18);
  go.onclick = run;
  row.append(go);
  box.append(row);

  if (S.menu === 'attach') box.append(attachMenu());
  if (S.menu === 'model') box.append(modelMenu());
  if (S.menu === 'doctype') box.append(docTypeMenu());
  if (S.menu === 'deal') box.append(dealMenu());

  wrap.append(box);
  const hint = document.createElement('div');
  hint.className = 'composer-hint';
  hint.textContent = S.busy
    ? 'Running. The Run button comes back when it finishes.'
    : 'Ctrl+Enter runs · Ctrl+V pastes an image · drop a file anywhere on the box';
  wrap.append(hint);
  return wrap;
}

function menuItem(icon, label, sub, fn, pressed) {
  const b = document.createElement('button');
  b.type = 'button';
  if (pressed !== undefined) b.setAttribute('aria-pressed', String(pressed));
  b.innerHTML = `<span class="mi">${svg(icon, 15)}</span><span><b></b>`
    + (sub ? '<small></small>' : '') + '</span>';
  b.querySelector('b').textContent = label;
  if (sub) b.querySelector('small').textContent = sub;
  b.onclick = fn;
  return b;
}

function attachMenu() {
  const m = document.createElement('div');
  m.className = 'menu';
  m.onclick = (e) => e.stopPropagation();
  m.innerHTML = '<div class="menu-label">Add</div>';
  m.append(menuItem('image', 'Upload image or PDF', 'PNG, JPG, WEBP or PDF', chooseFile));
  m.append(menuItem('camera', 'Use webcam', 'Take a picture now', fromWebcam));
  m.append(menuItem('clip', 'Paste from clipboard', 'Whatever you last copied', fromClipboard));
  return m;
}

function modelMenu() {
  const m = document.createElement('div');
  m.className = 'menu';
  m.onclick = (e) => e.stopPropagation();
  m.innerHTML = '<div class="menu-label">Model</div>';
  S.models.forEach((x) => {
    m.append(menuItem(x.icon, x.name, x.sub, () => pickModel(x.id), x.id === S.model));
  });
  return m;
}

function docTypeMenu() {
  const m = document.createElement('div');
  m.className = 'menu right';
  m.onclick = (e) => e.stopPropagation();
  m.innerHTML = '<div class="menu-label">Document type</div>';
  S.docTypes.forEach((d) => {
    const b = menuItem('fields', d.name,
                       `${d.fields.length} fields, ${d.reader_short}`,
                       () => pickDocType(d.id), d.id === S.docType);
    b.title = d.why;
    m.append(b);
  });
  if (S.docType) {
    m.append(menuItem('x', 'No field extraction',
                      'Just read the document', clearDocType, false));
  }
  return m;
}

function dealMenu() {
  const m = document.createElement('div');
  m.className = 'menu right';
  m.onclick = (e) => e.stopPropagation();
  m.innerHTML = '<div class="menu-label">Deal workbook</div>';
  S.deals.forEach((d) => {
    const b = menuItem('table', d.name,
                       `${d.filled} of ${d.mapped} filled, `
                       + `${d.documents} document(s)`,
                       () => openDeal(d.id), S.deal && S.deal.id === d.id);
    m.append(b);
  });
  m.append(menuItem('plus', 'New deal', 'From a workbook you supply', () => {
    // Not S.menu: the global click handler closes a menu on the next click
    // anywhere, which killed this form the moment the name box was clicked.
    S.newDeal = true;
    S.menu = null;
    render();
  }));
  if (S.deal) {
    m.append(menuItem('x', 'Work without a deal',
                      'Read documents on their own', closeDeal, false));
  }
  return m;
}

/* The new-deal form and the mapping review, both shown in the composer. */
function dealPanel() {
  if (S.newDeal) {
    const panel = document.createElement('div');
    panel.className = 'picker';
    const head = document.createElement('div');
    head.className = 'picker-head';
    const title = document.createElement('div');
    title.innerHTML = '<strong>New deal</strong><small></small>';
    title.querySelector('small').textContent =
      'Supply the workbook for this deal. It is copied, never written to.';
    head.append(title);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'link';
    cancel.textContent = 'Cancel';
    cancel.onclick = () => {
      S.newDeal = false;
      S.dealError = null;
      render();
    };
    head.append(cancel);
    panel.append(head);

    const row = document.createElement('div');
    row.className = 'picker-extra';
    const lab = document.createElement('label');
    lab.htmlFor = 'deal-name';
    lab.textContent = 'Deal name';
    const input = document.createElement('input');
    input.id = 'deal-name';
    input.type = 'text';
    input.placeholder = 'for example Maple Avenue acquisition';
    input.value = S.dealName;
    // Re-render so the button below follows what is typed. The caret is
    // restored by id, so this does not interrupt typing.
    input.oninput = () => { S.dealName = input.value; render(); };
    row.append(lab, input);
    panel.append(row);

    const bar = document.createElement('div');
    bar.className = 'template-bar';
    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'btn primary';
    const named = S.dealName.trim().length > 0;
    pick.disabled = S.dealBusy || !named;
    pick.innerHTML = `${svg('table', 14)}<span></span>`;
    pick.querySelector('span').textContent =
      S.dealBusy ? 'Reading the workbook...' : 'Choose workbook';
    pick.title = named ? 'Pick the .xlsx for this deal'
                       : 'Name the deal first';
    pick.onclick = chooseWorkbook;
    bar.append(pick);
    if (!named) {
      const hint = document.createElement('span');
      hint.className = 'muted-line';
      hint.textContent = 'Name the deal, then choose its workbook.';
      bar.append(hint);
    }
    panel.append(bar);
    if (S.dealError) {
      const f = document.createElement('div');
      f.className = 'fail';
      f.textContent = S.dealError;
      panel.append(f);
    }
    return panel;
  }

  if (!S.deal) return null;

  const panel = document.createElement('div');
  panel.className = 'picker';
  const head = document.createElement('div');
  head.className = 'picker-head';
  const title = document.createElement('div');
  title.innerHTML = '<strong></strong><small></small>';
  title.querySelector('strong').textContent = S.deal.name;
  const done = Object.keys(S.deal.filled || {}).length;
  const seen = (S.deal.history || []).length;
  title.querySelector('small').textContent = S.deal.confirmed
    ? (seen
        ? `${done} of ${S.deal.mapping.length} cells filled, from `
          + `${seen} document(s)`
        : `Nothing read into it yet. Add a document below and run it: its `
          + `fields land in these ${S.deal.mapping.length} cells.`)
    : 'Check where each field will go, correct anything wrong, then confirm.';
  head.append(title);

  const grab = document.createElement('a');
  grab.className = 'link';
  grab.href = `/api/deals/${S.deal.id}/workbook`;
  grab.textContent = 'Download';
  head.append(grab);
  const shut = document.createElement('button');
  shut.type = 'button';
  shut.className = 'link';
  shut.textContent = 'Close';
  shut.onclick = closeDeal;
  head.append(shut);
  panel.append(head);

  if (!S.deal.mapping.length) {
    const none = document.createElement('div');
    none.className = 'muted-line';
    none.textContent = 'No field names were recognised in that workbook. '
      + 'Label the cells with field names and start the deal again.';
    panel.append(none);
    return panel;
  }

  // Only the proposal is editable. Once confirmed it reads as a summary.
  const list = document.createElement('div');
  list.className = 'map-list';
  S.deal.mapping.forEach((row) => {
    const line = document.createElement('div');
    line.className = 'map-row';

    const name = document.createElement('span');
    name.className = 'map-field';
    name.textContent = row.field;
    line.append(name);

    const found = document.createElement('span');
    found.className = 'map-how';
    found.textContent = row.label_text
      ? `"${row.label_text}" at ${row.sheet ? row.sheet + '!' : ''}${row.label_cell}`
      : (row.how || '');
    line.append(found);

    if (S.deal.confirmed) {
      const at = document.createElement('span');
      at.className = 'verdict ok';
      at.textContent = row.cell;
      line.append(at);
    } else {
      const cell = document.createElement('input');
      cell.className = 'map-cell';
      cell.id = `map-${row.field.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
      cell.type = 'text';
      cell.value = row.cell;
      cell.oninput = () => setCell(row.field, cell.value);
      line.append(cell);
      const drop = document.createElement('button');
      drop.type = 'button';
      drop.className = 'link';
      drop.textContent = 'Remove';
      drop.onclick = () => dropFromMapping(row.field);
      line.append(drop);
    }
    list.append(line);
  });
  panel.append(list);

  if (!S.deal.confirmed) {
    // The scan only knows the names the document types list, so a template
    // with its own labels needs rows added here.
    const add = document.createElement('div');
    add.className = 'map-row map-add';

    const list = document.createElement('datalist');
    list.id = 'known-fields';
    knownFields().forEach((f) => {
      const option = document.createElement('option');
      option.value = f;
      list.append(option);
    });
    add.append(list);

    const field = document.createElement('input');
    field.id = 'add-field';
    field.className = 'map-field-input';
    field.type = 'text';
    field.setAttribute('list', 'known-fields');
    field.placeholder = 'Field name, for example Meter number';
    field.value = S.addField;
    field.oninput = () => { S.addField = field.value; };
    add.append(field);

    const cell = document.createElement('input');
    cell.id = 'add-cell';
    cell.className = 'map-cell';
    cell.type = 'text';
    cell.placeholder = 'B7';
    cell.value = S.addCell;
    cell.oninput = () => { S.addCell = cell.value; };
    cell.onkeydown = (e) => { if (e.key === 'Enter') addMappingRow(); };
    add.append(cell);

    const plus = document.createElement('button');
    plus.type = 'button';
    plus.className = 'link';
    plus.textContent = 'Add';
    plus.onclick = addMappingRow;
    add.append(plus);
    panel.append(add);

    const note = document.createElement('div');
    note.className = 'muted-line';
    note.textContent = 'Any name works, not only the suggested ones. '
      + 'Whatever is mapped here is what gets looked for in each document.';
    panel.append(note);

    const bar = document.createElement('div');
    bar.className = 'template-bar';
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'btn primary';
    ok.disabled = S.dealBusy;
    ok.innerHTML = `${svg('fields', 14)}<span></span>`;
    ok.querySelector('span').textContent =
      S.dealBusy ? 'Saving...' : 'Confirm mapping';
    ok.onclick = confirmMapping;
    bar.append(ok);
    panel.append(bar);
  }
  else {
    const bar = document.createElement('div');
    bar.className = 'template-bar';
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'btn';
    edit.innerHTML = `${svg('fields', 14)}<span>Edit mapping</span>`;
    edit.title = 'Add more fields, or move one to another cell';
    edit.onclick = () => { S.deal.confirmed = false; render(); };
    bar.append(edit);
    panel.append(bar);
  }
  if (S.dealError) {
    const f = document.createElement('div');
    f.className = 'fail';
    f.textContent = S.dealError;
    panel.append(f);
  }
  return panel;
}

/* The field checklist, shown inside the composer once a type is chosen. */
function fieldPicker() {
  const d = docTypeById(S.docType);
  if (!d) return null;

  const panel = document.createElement('div');
  panel.className = 'picker';

  const head = document.createElement('div');
  head.className = 'picker-head';
  const title = document.createElement('div');
  title.innerHTML = '<strong></strong><small></small>';
  title.querySelector('strong').textContent = d.name;
  title.querySelector('small').textContent =
    `read with ${d.reader_short}, then found in the text`;
  head.append(title);

  if (d.fields.length) {
    const everything = S.chosen.length === d.fields.length;
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'link';
    all.textContent = everything ? 'Clear all' : 'Select all';
    all.onclick = () => {
      S.chosen = everything ? [] : d.fields.slice();
      render();
    };
    head.append(all);
  }
  const off = document.createElement('button');
  off.type = 'button';
  off.className = 'link';
  off.textContent = 'Remove';
  off.onclick = clearDocType;
  head.append(off);
  panel.append(head);

  if (d.fields.length) {
    const grid = document.createElement('div');
    grid.className = 'field-grid';
    d.fields.forEach((name) => {
      const id = 'f-' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      const label = document.createElement('label');
      label.className = 'field-chip';
      label.htmlFor = id;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.id = id;
      cb.checked = S.chosen.includes(name);
      cb.onchange = () => toggleField(name);
      const span = document.createElement('span');
      span.textContent = name;
      label.append(cb, span);
      grid.append(label);
    });
    panel.append(grid);
  } else {
    // Rent roll, Lease and Tax bill carry no field list on purpose: the whole
    // document is the answer, or formats vary too much to fix one. Say so and
    // let the reader name what they want instead of showing an empty grid.
    const note = document.createElement('div');
    note.className = 'muted-line no-fields';
    note.textContent = d.why
      || 'This type has no fixed field list, so name the fields you want.';
    panel.append(note);
  }

  const extra = document.createElement('div');
  extra.className = 'picker-extra';
  const lab = document.createElement('label');
  lab.htmlFor = 'extra-fields';
  lab.textContent = d.fields.length ? 'Other fields' : 'Fields to find';
  const inp = document.createElement('input');
  inp.id = 'extra-fields';
  inp.type = 'text';
  inp.placeholder = 'Comma separated, for example Meter number, Tariff';
  inp.value = S.extra;
  inp.oninput = () => { S.extra = inp.value; };
  extra.append(lab, inp);
  panel.append(extra);
  return panel;
}

/* The found-fields table, with the evidence line behind each value. */
function fieldsTable(m) {
  const wrap = document.createElement('div');
  if (m.fieldsBusy) {
    const p = document.createElement('div');
    p.className = 'muted-line';
    p.textContent = 'Searching the text for those fields...';
    wrap.append(p);
    return wrap;
  }
  if (m.fieldError) {
    const p = document.createElement('div');
    p.className = 'fail';
    p.textContent = m.fieldError;
    wrap.append(p);
    return wrap;
  }
  if (!m.fieldRows) {
    // Nothing pulled yet. The transcript is already here, so this costs a
    // text search and no model time: say what is currently selected and
    // offer to run it, rather than sending anyone back to read again.
    const type = docTypeById(S.docType);
    const p = document.createElement('div');
    p.className = 'muted-line';
    p.textContent = type
      ? `${type.name}: ${S.chosen.length} field(s) ticked`
        + (S.extra.trim() ? ` plus ${S.extra.trim()}` : '')
      : 'Choose a document type with the Fields button below, or type the '
        + 'field names you want, then pull them out of this transcript.';
    wrap.append(p);

    if (wantsFields()) {
      const go = document.createElement('button');
      go.type = 'button';
      go.className = 'btn primary';
      go.innerHTML = `${svg('fields', 14)}<span>Extract fields</span>`;
      go.onclick = () => extractNow(m);
      const bar = document.createElement('div');
      bar.className = 'template-bar';
      bar.append(go);
      wrap.append(bar);
    }
    return wrap;
  }

  const scroll = document.createElement('div');
  scroll.className = 'tablewrap';
  const t = document.createElement('table');
  t.className = 'fields';
  const head = document.createElement('tr');
  ['Field', 'Value', '', 'Found'].forEach((h) => {
    const th = document.createElement('th');
    th.textContent = h;
    head.append(th);
  });
  t.append(head);

  const VERDICT = {
    yes: ['ok', 'yes'],
    check: ['warn', 'CHECK'],
    guess: ['warn', 'guess'],
    none: ['none', 'not found'],
  };
  m.fieldRows.forEach((r) => {
    const tr = document.createElement('tr');
    const f = document.createElement('td');
    f.textContent = r.field;
    const v = document.createElement('td');
    v.className = 'val';
    v.textContent = r.value || '-';
    const s = document.createElement('td');
    const [cls, text] = VERDICT[r.verdict] || ['none', r.verdict];
    const tag = document.createElement('span');
    tag.className = 'verdict ' + cls;
    tag.textContent = text;
    s.append(tag);
    const w = document.createElement('td');
    w.className = 'where';
    w.textContent = r.where || '';
    if (r.evidence) tr.title = r.evidence;
    tr.append(f, v, s, w);
    t.append(tr);
  });
  scroll.append(t);
  wrap.append(scroll);

  if (m.fieldSummary) {
    const sum = document.createElement('div');
    sum.className = 'muted-line';
    sum.textContent = m.fieldSummary;
    wrap.append(sum);
  }

  const templates = templatesFor(m.docTypeName);
  const bar = document.createElement('div');
  bar.className = 'template-bar';

  // The picker may have moved on since this ran. Searching again is a text
  // search over a transcript already in hand, so it is cheap to offer.
  const again = document.createElement('button');
  again.type = 'button';
  again.className = 'btn';
  again.disabled = !wantsFields() || !!m.fieldsBusy;
  again.innerHTML = `${svg('fields', 14)}<span>Extract again</span>`;
  again.title = 'Search this transcript for whatever is ticked right now';
  again.onclick = () => extractNow(m);
  bar.append(again);

  if (templates.length) {
    const lab = document.createElement('span');
    lab.className = 'muted-line';
    lab.textContent = 'Drop these into:';
    bar.append(lab);
    templates.forEach((t) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn';
      b.disabled = !!m.fillBusy;
      b.innerHTML = `${svg('table', 14)}<span></span>`;
      b.querySelector('span').textContent =
        m.fillBusy === t.id ? 'Filling...' : t.name;
      b.title = `${t.workbook}, ${t.fields.length} cells`;
      b.onclick = () => fillTemplate(m, t.id);
      bar.append(b);
    });
  }
  if (S.deal) {
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'btn primary';
    add.disabled = !!m.dealBusy;
    add.innerHTML = `${svg('table', 14)}<span></span>`;
    add.querySelector('span').textContent = m.dealBusy
      ? 'Adding...' : `Add to ${S.deal.name}`;
    add.title = 'Write these fields into the deal workbook';
    add.onclick = () => addToDeal(m);
    bar.append(add);
  }
  // Appended either way: a type with no template still gets Extract again.
  wrap.append(bar);

  if (m.dealError) {
    const f = document.createElement('div');
    f.className = 'fail';
    f.textContent = m.dealError;
    wrap.append(f);
  }

  if (m.dealResult) {
    const done = document.createElement('div');
    done.className = 'filled';
    const line = document.createElement('div');
    line.className = 'muted-line';
    const bits = [`${m.dealResult.written.length} cell(s) written`];
    if (m.dealResult.clashed.length) {
      bits.push('kept the earlier answer for '
        + m.dealResult.clashed.map((c) => `${c.field} (${c.kept}, from `
            + `${c.from}; this document said ${c.offered})`).join('; '));
    }
    if (m.dealResult.skipped.length) {
      bits.push(`${m.dealResult.skipped.length} field(s) this document did `
        + 'not carry');
    }
    line.textContent = bits.join('. ') + '.';
    const a = document.createElement('a');
    a.className = 'btn primary';
    a.href = m.dealResult.download;
    a.innerHTML = `${svg('down', 14)}<span>Workbook</span>`;
    done.append(a, line);
    wrap.append(done);
  }

  if (m.fillError) {
    const f = document.createElement('div');
    f.className = 'fail';
    f.textContent = m.fillError;
    wrap.append(f);
  }

  if (m.filled) {
    const done = document.createElement('div');
    done.className = 'filled';
    const line = document.createElement('div');
    line.className = 'muted-line';
    const skipped = m.filled.skipped.length;
    line.textContent = `${m.filled.written.length} cell(s) written`
      + (skipped ? `, ${skipped} left blank: `
          + m.filled.skipped.map((s) => `${s.field} (${s.why})`).join(', ')
        : '.');
    const a = document.createElement('a');
    a.className = 'btn primary';
    a.href = m.filled.download;
    a.innerHTML = `${svg('down', 14)}<span></span>`;
    a.querySelector('span').textContent = m.filled.file;
    done.append(a, line);
    wrap.append(done);
  }
  return wrap;
}

/* ----------------------------------------------------------------- render */

function render() {
  const app = el('app');

  // A run streams step events, and every one of them rebuilds the page. If
  // the caret was in the composer it has to come back, or typing while a run
  // is going loses a character each time a step lands.
  const old = document.activeElement;
  const typing = old && (old.tagName === 'TEXTAREA'
                         || (old.tagName === 'INPUT' && old.type === 'text'));
  const hadCaret = typing
    ? { id: old.id, start: old.selectionStart, end: old.selectionEnd }
    : null;

  app.className = `shell${S.sidebar ? '' : ' collapsed'}`;
  app.textContent = '';
  if (S.sidebar) app.append(sidebar());

  const main = document.createElement('div');
  main.className = 'main';

  const top = document.createElement('div');
  top.className = 'topbar';
  if (!S.sidebar) {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'icon-btn';
    open.setAttribute('aria-label', 'Open sidebar');
    open.innerHTML = svg('panel', 18);
    open.onclick = () => { S.sidebar = true; render(); };
    top.append(open);
  }
  const crumb = document.createElement('div');
  crumb.className = 'crumb';
  const model = modelById(S.model);
  crumb.innerHTML = '<b></b><span></span>';
  crumb.querySelector('b').textContent = model ? model.name : 'No model';
  crumb.querySelector('span').textContent = model ? ` · ${model.sub}` : '';
  top.append(crumb);
  const badge = document.createElement('div');
  badge.className = 'local-badge';
  badge.innerHTML = '<span class="dot"></span><span>Local GPU</span>';
  top.append(badge);
  main.append(top);

  const thread = document.createElement('div');
  thread.className = 'thread';
  const inner = document.createElement('div');
  inner.className = 'thread-inner';
  if (!S.msgs.length) inner.append(hello());
  S.msgs.forEach((m) => inner.append(m.role === 'you' ? youMsg(m) : runMsg(m)));
  thread.append(inner);
  main.append(thread);

  main.append(composer());
  app.append(main);

  if (hadCaret) {
    const node = hadCaret.id ? app.querySelector('#' + hadCaret.id)
                             : app.querySelector('.composer textarea');
    if (node && !node.disabled) {
      node.focus();
      try {
        node.setSelectionRange(hadCaret.start, hadCaret.end);
      } catch (e) { /* some inputs refuse a selection range */ }
    }
  }

  // keep the newest message in view while a run streams
  if (S.msgs.length) thread.scrollTop = thread.scrollHeight;
}

/* ------------------------------------------------------------------- boot */

document.addEventListener('click', () => {
  if (S.menu) { S.menu = null; render(); }
});

document.addEventListener('paste', (e) => {
  const item = [...(e.clipboardData?.items || [])]
    .find((i) => i.type.startsWith('image/'));
  if (!item) return;
  e.preventDefault();
  const blob = item.getAsFile();
  if (blob) attach(new File([blob], 'pasted.png', { type: blob.type }));
});

(async function boot() {
  render();
  try {
    const [models, skills, docTypes, templates] = await Promise.all([
      api('/api/models'), api('/api/skills'), api('/api/doc_types'),
      api('/api/templates'),
    ]);
    S.models = models;
    S.skills = skills;
    S.docTypes = docTypes;
    S.templates = templates;
    await loadDeals();
    if (models.length) {
      S.model = models[0].id;
      S.prompt = promptFor(models[0]);
    }
    render();
  } catch (e) {
    toast(`Could not reach the server: ${e.message}`);
  }
})();
