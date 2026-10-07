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

/* Sidebar state belongs to the person, not to the page load: a section
 * folded away should still be folded tomorrow. Wrapped because storage
 * throws outright in a private window or with site data blocked, and a
 * sidebar preference is not worth taking the page down for. */
function recall(key, fallback) {
  try {
    const raw = localStorage.getItem(`lv-${key}`);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (e) {
    return fallback;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(`lv-${key}`, JSON.stringify(value));
  } catch (e) { /* nothing to do about it, and nothing worth breaking for */ }
}

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
  extras: [],      // extra field names, added one at a time
  typing: '',      // what is half-typed in the add box
  templates: [],   // spreadsheets the found fields can be dropped into
  history: [],     // documents read in earlier sessions
  expanded: recall('expanded', []),   // rows opened to their detail
  collapsed: recall('collapsed', []), // groups folded shut
  openRun: null,   // the run the thread is currently showing
  search: '',      // filter over the run list
  chatModel: null, // the local model that answers typed questions
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
  S.extras = [];
  S.typing = '';
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
  return S.chosen.length > 0 || S.extras.length > 0;
}

/* Everything to search this transcript for: the ticked boxes and anything
 * typed in. */
function fieldsWanted() {
  const out = [];
  S.chosen.forEach((f) => {
    if (f && !out.includes(f)) out.push(f);
  });
  return out;
}


async function loadChatModel() {
  try {
    S.chatModel = await api('/api/chat_model');
  } catch (e) { /* the footer falls back to the general claim */ }
}

async function loadHistory() {
  try {
    S.history = await api('/api/history');
  } catch (e) { /* the sidebar still works without it */ }
}

/* When a run happened, in the buckets a chat sidebar uses. */
function dayOf(at) {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return 'Earlier';
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const days = Math.floor((midnight - when) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'Previous 7 days';
  if (days < 30) return 'Previous 30 days';
  return when.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

/* Reopen a document read in an earlier session. The transcript is kept, so
 * this costs nothing: re-running it would mean another model load. */
async function reopen(id) {
  if (S.openRun === id) return;
  try {
    const entry = await api(`/api/history/${id}`);
    const model = modelById(entry.model);
    // Replaces the thread rather than adding to it: picking one from the
    // list is opening it, not stacking it on whatever was already there.
    S.msgs = [
      { role: 'you', text: '', file: { name: entry.file, pdf: false } },
      {
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
      },
    ];
    S.openRun = id;
    S.file = null;
    render();
  } catch (e) {
    toast(`Could not open that: ${e.message}`);
  }
}

async function forget(id, event) {
  event.stopPropagation();
  try {
    await api(`/api/history/${id}`, { method: 'DELETE' });
    S.history = S.history.filter((h) => h.id !== id);
    if (S.openRun === id) { S.msgs = []; S.openRun = null; }
    render();
  } catch (e) {
    toast(e.message);
  }
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


const isOpen = (key) => S.expanded.includes(key);
const isFolded = (key) => S.collapsed.includes(key);

function toggleGroup(key) {
  S.collapsed = isFolded(key) ? S.collapsed.filter((k) => k !== key)
                              : S.collapsed.concat([key]);
  remember('collapsed', S.collapsed);
  render();
}

/* A group heading that folds its list away. The count stays visible when
 * shut, so folding one does not hide that anything is there. */
function groupHeader(key, label, count, icon) {
  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'group-label';
  head.setAttribute('aria-expanded', String(!isFolded(key)));
  head.innerHTML = `<span class="fold">${svg('chev', 11)}</span>`
    + (icon ? `<span class="spark">${svg(icon, 12)}</span>` : '')
    + '<span class="group-name"></span><span class="group-count"></span>';
  head.querySelector('.group-name').textContent = label;
  head.querySelector('.group-count').textContent = count;
  head.onclick = () => toggleGroup(key);
  return head;
}

function toggleExpanded(key, event) {
  if (event) event.stopPropagation();
  S.expanded = isOpen(key) ? S.expanded.filter((k) => k !== key)
                           : S.expanded.concat([key]);
  remember('expanded', S.expanded);
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
  msg.want = { fields: fieldsWanted(), extra: S.extras.join(', ') };
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
  S.openRun = null;
  S.file = null;
  S.prompt = promptFor(modelById(S.model));
  S.promptDirty = false;
  S.skill = null;
  render();
}

/* Whether there is anything to send: a document, or a typed question. */
function canSend() {
  if (S.busy) return false;
  if (S.file) return true;
  const ta = el('prompt-box');
  const typed = ta ? ta.value : (S.prompt || '');
  return typed.trim().length > 0;
}

/* Keep the Run button in step with the box without redrawing the composer. */
function syncSend() {
  const btn = document.querySelector('.composer .run');
  if (btn) btn.disabled = !canSend();
  const hint = document.querySelector('.composer-hint');
  if (hint) hint.classList.toggle('needs-file', !S.file && canSend());
}

/* A typed question, answered by the local text model. Kept apart from run()
 * on purpose: that one reads a document and reports what it found on the
 * page, and the two must never be mistaken for each other in the thread. */
async function ask() {
  const text = (S.prompt || '').trim();
  if (!text) return;

  S.msgs.push({ role: 'you', text, file: null });
  const msg = { role: 'chat', question: text, answer: '', model: '',
                busy: true, error: null, startedAt: Date.now() };
  S.msgs.push(msg);
  S.busy = true;
  S.prompt = '';
  S.promptDirty = false;
  render();

  // Only the plain exchanges go back as history: a document run's transcript
  // belongs to that document, not to this conversation.
  const history = [];
  S.msgs.forEach((m) => {
    if (m.role === 'chat' && m.answer) {
      history.push({ role: 'user', content: m.question });
      history.push({ role: 'assistant', content: m.answer });
    }
  });

  try {
    const out = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: text, history: history.slice(0, -2) }),
    });
    msg.answer = out.answer;
    msg.model = out.model;
  } catch (e) {
    msg.error = e.message;
  }
  msg.busy = false;
  S.busy = false;
  render();
}

async function run() {
  if (S.busy) return;
  if (!S.file) {
    // No document: this is a plain question. The OCR models cannot answer
    // one, so it goes to the local text model instead of being refused.
    if ((S.prompt || '').trim()) return ask();
    toast('Add an image or a PDF first.');
    return;
  }
  const m = modelById(S.model);
  if (!m) { toast('No model is available.'); return; }

  // A document read starts its own chat rather than stacking under the last
  // one, which is what the sidebar has always claimed: it lists runs, one row
  // each, and reopening a row replaces the thread. A thread that accumulated
  // three documents could not be any one of those rows, so the open entry and
  // what was on screen disagreed as soon as you read a second document.
  // Typed questions still append -- a chat is one document plus whatever was
  // asked about it.
  S.msgs = [];
  S.openRun = null;
  S.msgs.push({ role: 'you', text: m.takes_prompt ? S.prompt : '', file: S.file });
  const msg = {
    role: 'run', model: m.id, modelName: m.name, file: S.file.name,
    status: 'running', steps: [], tab: null, text: '', annotated: null,
    error: null, elapsed: null, saved: [],
    // Snapshot the picker now, so editing it mid-run cannot change what this
    // run was asked for.
    docType: S.docType,
    docTypeName: docTypeById(S.docType) ? docTypeById(S.docType).name : '',
    want: wantsFields() ? { fields: fieldsWanted(),
                            extra: S.extras.join(', ') } : null,
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
        doc_type: S.docType || '',
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
  S.openRun = started.id;
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
  // It is remembered now, so put it in the list rather than leaving the
  // sidebar a refresh behind what the thread is showing.
  if (msg.status === 'done') {
    await loadHistory();
    render();
  }
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
  mg.append(groupHeader('models', 'Models', S.models.length, null));
  if (!isFolded('models')) S.models.forEach((m) => {
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
    kg.append(groupHeader('skills', 'Skills', S.skills.length, 'spark'));
    if (!isFolded('skills')) S.skills.forEach((k) => {
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

  // One list of runs, the way a chat sidebar lists conversations: newest
  // first, bucketed by when, the open one marked, and a filter once there
  // are enough of them to need one. A run joins it the moment it finishes.
  const needle = S.search.trim().toLowerCase();
  const matching = S.history.filter(
    (h) => !needle || h.file.toLowerCase().includes(needle)
           || (h.model_name || '').toLowerCase().includes(needle));

  if (S.history.length > 6 || needle) {
    const find = document.createElement('div');
    find.className = 'find';
    const input = document.createElement('input');
    input.id = 'run-search';
    input.type = 'search';
    input.placeholder = 'Search runs';
    input.value = S.search;
    input.oninput = () => { S.search = input.value; render(); };
    find.append(input);
    node.append(find);
  }

  if (matching.length) {
    const list = document.createElement('div');
    list.className = 'group runs';
    let bucket = null;
    matching.forEach((h) => {
      const label = dayOf(h.at);
      if (label !== bucket) {
        bucket = label;
        const head = document.createElement('div');
        head.className = 'run-bucket';
        head.textContent = label;
        list.append(head);
      }
      const row = document.createElement('div');
      row.className = 'past-row';
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'history-item past';
      b.setAttribute('aria-current', String(S.openRun === h.id));
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
      x.setAttribute('aria-label', `Delete ${h.file}`);
      x.innerHTML = svg('x', 13);
      x.onclick = (e) => forget(h.id, e);
      row.append(x);
      list.append(row);
    });
    node.append(list);
  } else if (needle) {
    const none = document.createElement('div');
    none.className = 'muted-line no-runs';
    none.textContent = `Nothing matching "${S.search.trim()}".`;
    node.append(none);
  }

  const foot = document.createElement('div');
  foot.className = 'side-foot';
  // The claim was written when this page only read documents. It is still
  // true of the questions added since -- they go to a model on this machine
  // -- but a reader cannot tell that from "no image and no text leaves",
  // so the model answering them is named. If it is not running, say that
  // instead of promising a privacy property for something that will fail.
  const line = document.createElement('div');
  const b = document.createElement('b');
  b.textContent = 'Everything runs here.';
  line.append(b);
  const rest = document.createElement('span');
  const cm = S.chatModel;
  if (cm && cm.available) {
    rest.textContent = ` Documents are read on this GPU and questions are `
      + `answered by ${cm.model}, also on this machine. No image and no text `
      + `leaves it.`;
  } else if (cm) {
    rest.textContent = ' No image and no text leaves this machine. Typed '
      + `questions need ${cm.model}, which is not running.`;
  } else {
    rest.textContent = ' No image and no text leaves this machine.';
  }
  line.append(rest);
  foot.append(line);
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
  return d;
}

/* An answer from the local text model. Deliberately plainer than a run card:
 * nothing here was read off a page, so it carries no evidence, no tabs and
 * nothing to download. */
function chatMsg(m) {
  const d = document.createElement('div');
  d.className = 'msg';
  const b = document.createElement('div');
  b.className = 'bubble answer';
  if (m.busy) {
    // A 13GB model on a shared card takes 20-60 seconds, and a motionless
    // "Thinking..." for that long reads as a hang. The dots prove the page
    // is alive and the clock proves the model is, so nobody reloads at 40s
    // believing it died.
    const wait = document.createElement('div');
    wait.className = 'thinking';

    const dots = document.createElement('span');
    dots.className = 'thinking-dots';
    dots.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 3; i += 1) dots.append(document.createElement('i'));

    const word = document.createElement('span');
    word.className = 'thinking-word';
    word.textContent = 'Thinking';

    const clock = document.createElement('span');
    clock.className = 'thinking-clock';
    const since = m.startedAt || Date.now();
    const tick = () => {
      const secs = Math.round((Date.now() - since) / 1000);
      clock.textContent = secs >= 1 ? `${secs}s` : '';
      // The model is cold on the first question of a session and the wait is
      // long enough to look broken. Say why rather than let it be a mystery.
      if (secs === 12) word.textContent = 'Thinking — loading the model';
      if (secs === 45) word.textContent = 'Thinking — still going';
    };
    tick();
    const timer = setInterval(() => {
      if (!document.body.contains(clock)) { clearInterval(timer); return; }
      tick();
    }, 1000);

    wait.append(dots, word, clock);
    b.append(wait);
  } else if (m.error) {
    const p = document.createElement('div');
    p.className = 'fail';
    p.textContent = m.error;
    b.append(p);
  } else {
    const body = document.createElement('div');
    body.className = 'answer-body';
    body.textContent = m.answer;
    b.append(body);
  }
  d.append(b);
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

    // The two workbooks, side by side, because they answer different
    // questions: the fields one is the values another tool reads, the table
    // one is the document as it was rendered. Fields only appears once the
    // fields have actually been pulled -- an empty workbook is worse than no
    // button, since it looks like the read found nothing.
    if (m.fieldRows && m.fieldRows.length) {
      const fx = document.createElement('a');
      fx.className = 'btn';
      fx.href = `/api/runs/${m.runId}/export/fields`;
      fx.innerHTML = `${svg('down', 14)}<span>Fields (XLSX)</span>`;
      foot.append(fx);
    }
    if ((m.text || '').includes('<table')) {
      const tx = document.createElement('a');
      tx.className = 'btn';
      tx.href = `/api/runs/${m.runId}/export/tables`;
      tx.innerHTML = `${svg('down', 14)}<span>Table (XLSX)</span>`;
      foot.append(tx);
    }

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

  const picker = fieldPicker();
  if (picker) box.append(picker);

  const model = modelById(S.model);
  const ta = document.createElement('textarea');
  ta.id = 'prompt-box';
  ta.rows = 2;
  ta.value = S.prompt;
  // Whether the box is usable depends on whether a document is attached, not
  // on the reader. With a file, this is the instruction for reading it, and a
  // fixed-pipeline reader has no use for one. With no file it is a question
  // for the text model, which every reader is irrelevant to -- and disabling
  // it then left nowhere to type at all.
  const fixedReader = !!(model && !model.takes_prompt);
  ta.disabled = !!S.file && fixedReader;
  if (S.file) {
    ta.placeholder = fixedReader
      ? `${model.name} takes no prompt. It runs a fixed layout pipeline.`
      : 'Tell the model what to read, or leave its default prompt as it is…';
  } else {
    ta.placeholder = 'Ask a question, or attach a document to read…';
  }
  ta.oninput = () => {
    S.prompt = ta.value;
    S.promptDirty = true;
    // The Run button's disabled state depends on this text, and typing used
    // to change the state without redrawing anything -- so the button stayed
    // greyed out however much you typed, and the page looked broken. Updated
    // here rather than by a full render(), which would rebuild the textarea
    // under the cursor on every keystroke.
    syncSend();
  };
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


  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'run';
  go.setAttribute('aria-label', 'Run');
  // A question with nothing attached is a legitimate thing to send, so the
  // button is live for that too. Same rule the keystroke handler uses.
  go.disabled = !canSend();
  go.innerHTML = svg('send', 18);
  go.onclick = run;
  row.append(go);
  box.append(row);

  if (S.menu === 'attach') box.append(attachMenu());
  if (S.menu === 'model') box.append(modelMenu());
  if (S.menu === 'doctype') box.append(docTypeMenu());

  wrap.append(box);
  const hint = document.createElement('div');
  hint.className = 'composer-hint';
  // Typing with nothing attached used to do nothing at all: the Run button
  // greyed itself out and said why only in a toast that a disabled button
  // never fires. These models read documents -- they do not converse -- so
  // the box has to say that rather than leave someone waiting for a reply.
  const typedOnly = !S.busy && !S.file && (S.prompt || '').trim().length > 0;
  if (S.busy) {
    hint.textContent = 'Running. The Run button comes back when it finishes.';
  } else if (typedOnly) {
    hint.classList.add('needs-file');
    const m = modelById(S.model);
    hint.textContent = m && m.takes_prompt
      ? `${m.name} reads a document — it does not answer on its own. `
        + 'Attach an image or PDF and this becomes the instruction for '
        + 'reading it.'
      : `${m ? m.name : 'This model'} reads a document. Attach an image or a `
        + 'PDF to run it.';
  } else {
    hint.textContent =
      'Ctrl+Enter runs · Ctrl+V pastes an image · drop a file anywhere on the box';
  }
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
  extra.append(lab);

  // One field at a time, each its own chip. A comma-separated box asked the
  // reader to do the parsing: you could not see where one field ended and
  // the next began, could not remove the middle one without re-typing the
  // line, and a stray comma silently became two fields or none.
  if (S.extras.length) {
    const added = document.createElement('div');
    added.className = 'field-grid';
    S.extras.forEach((name) => {
      const chip = document.createElement('span');
      chip.className = 'field-chip added';
      const txt = document.createElement('span');
      txt.textContent = name;
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'chip-x';
      x.setAttribute('aria-label', `Remove ${name}`);
      x.textContent = '×';
      x.onclick = () => {
        S.extras = S.extras.filter((f) => f !== name);
        render();
      };
      chip.append(txt, x);
      added.append(chip);
    });
    extra.append(added);
  }

  const row = document.createElement('div');
  row.className = 'add-field-row';
  const inp = document.createElement('input');
  inp.id = 'extra-fields';
  inp.type = 'text';
  inp.placeholder = d.fields.length
    ? 'Add a field, for example Meter number'
    : 'Name a field to find, for example Tenant name';
  inp.value = S.typing;
  inp.oninput = () => { S.typing = inp.value; };

  const addNow = () => {
    // Accept a pasted list too: someone used to typing commas should not be
    // punished for it, they just get one chip per name.
    const names = inp.value.split(',').map((t) => t.trim()).filter(Boolean);
    let added = 0;
    names.forEach((n) => {
      if (!S.extras.includes(n) && !S.chosen.includes(n)) {
        S.extras.push(n);
        added += 1;
      }
    });
    S.typing = '';
    if (!added && names.length) toast('Already on the list.');
    render();
    const again = el('extra-fields');
    if (again) again.focus();
  };

  inp.onkeydown = (e) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addNow(); }
  };

  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'btn';
  add.textContent = 'Add';
  add.onclick = addNow;

  row.append(inp, add);
  extra.append(row);
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
        + (S.extras.length ? ` plus ${S.extras.join(', ')}` : '')
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
  // Appended either way: a type with no template still gets Extract again.
  wrap.append(bar);

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
  S.msgs.forEach((m) => {
    if (m.role === 'you') inner.append(youMsg(m));
    else if (m.role === 'chat') inner.append(chatMsg(m));
    else inner.append(runMsg(m));
  });
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
    await loadHistory();
    // Not in the Promise.all above: this one reaches out to the model
    // service, and the page should still come up if that is down.
    await loadChatModel();
    if (models.length) {
      S.model = models[0].id;
      S.prompt = promptFor(models[0]);
    }
    render();
  } catch (e) {
    toast(`Could not reach the server: ${e.message}`);
  }
})();
