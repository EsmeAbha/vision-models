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
  pop: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  fields: '<rect x="3" y="4" width="5" height="5" rx="1"/><rect x="3" y="15" width="5" height="5" rx="1"/><path d="M11 6.5h10"/><path d="M11 17.5h10"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  mark2: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
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
  fieldQuery: '',  // what is typed in the field dropdown's search box
  history: [],     // documents read in earlier sessions
  expanded: recall('expanded', []),   // rows opened to their detail
  // Models and Skills start folded on every load: the run history is what
  // the sidebar is for, and the composer already shows the chosen reader.
  collapsed: ['models', 'skills'],
  openRun: null,   // the run the thread is currently showing
  search: '',      // filter over the run list
  chatModel: null, // the local model that answers typed questions
  // What the side panel is showing, and how wide it is. The width is
  // remembered because a panel that resets to a default every time is one
  // nobody drags to a useful size twice.
  // The thread's own id. A conversation with no document has no run to be
  // identified by, and without this it could not be written down -- which is
  // how a page reload used to throw the whole conversation away.
  threadId: null,
  view: null,      // { src, name, download }
  viewWidth: Number(localStorage.getItem('lv-view-w')) || 520,
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
  S.fieldQuery = '';
  S.menu = null;
  render();
}

/* Field names are PEXL's own snake_case keys -- kept as-is everywhere they
 * are matched or exported, since that is what another tool's API reads. This
 * is only for what gets printed on screen: "total_gas_bill" -> "Total gas
 * bill". */
function fieldLabel(name) {
  const s = String(name).replace(/_/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
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


/* A thread id, in the shape the server stores records under. */
function newThreadId() {
  const b = new Uint8Array(16);
  (window.crypto || {}).getRandomValues
    ? window.crypto.getRandomValues(b)
    : b.forEach((_, i) => { b[i] = Math.floor(Math.random() * 256); });
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

/* Write the conversation down. Called after every answer rather than on the
 * way out: there is no "on the way out" for a browser tab, and a thread that
 * is only in memory is one refresh from being gone. */
async function saveThread() {
  if (!S.threadId) return;
  const turns = S.msgs
    .filter((m) => m.role === 'chat' && (m.answer || m.error))
    .map((m) => ({ question: m.question, answer: m.answer || '',
                   model: m.model || '' }));
  if (!turns.length) return;
  const first = turns[0].question.replace(/\s+/g, ' ').trim();
  try {
    await api('/api/history/thread', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: S.threadId,
        title: first.slice(0, 90),
        turns,
      }),
    });
    await loadHistory();
    render();
  } catch (e) {
    // Losing the conversation silently is the thing this exists to prevent.
    toast(`This conversation was not saved: ${e.message}`);
  }
}

/* ------------------------------------------------------- the side panel */

/* Open a document beside the thread. The point is checking: a value pulled
 * off a page is worth nothing to whoever signs it off unless they can put it
 * next to the page it came from. */
function openView(src, name, download) {
  S.view = { src, name, download: download || '' };
  render();
}

function closeView() {
  S.view = null;
  render();
}

function viewPanel() {
  const wrap = document.createElement('div');
  wrap.className = 'viewer';
  wrap.style.width = `${viewWidthNow()}px`;

  const head = document.createElement('div');
  head.className = 'viewer-head';
  const title = document.createElement('div');
  title.className = 'viewer-name';
  title.textContent = S.view.name;
  title.title = S.view.name;
  head.append(title);

  const isWorkbook = S.view.src.startsWith('export:');
  // A workbook previews freely, but copies and downloads only once every row
  // in it has been approved against the page.
  const [, runId, what] = S.view.src.split(':');
  const owner = isWorkbook ? msgByRun(runId) : null;
  const locked = isWorkbook && lockOf(owner, what);

  if (locked) {
    const go = document.createElement('button');
    go.className = 'btn primary';
    go.type = 'button';
    go.innerHTML = `${svg('lock', 14)}<span>Approve to unlock</span>`;
    go.title = 'Copy and download open once every row is checked against the page';
    go.onclick = () => owner && openReview(owner);
    head.append(go);
  } else if (S.view.download) {
    const dl = document.createElement('a');
    dl.className = 'icon-btn';
    dl.href = S.view.download;
    dl.setAttribute('aria-label', `Download ${S.view.name}`);
    dl.innerHTML = svg('down', 16);
    head.append(dl);
  }
  let frame = null;
  if (isWorkbook && !locked) {
    const all = document.createElement('button');
    all.className = 'btn';
    all.type = 'button';
    all.innerHTML = `${svg('copy', 14)}<span>Copy all</span>`;
    all.onclick = () => copyTables(
      [...(frame.contentDocument?.querySelectorAll('table') || [])], 'every sheet');
    head.append(all);
  }

  const pop = document.createElement('a');
  pop.className = 'icon-btn';
  pop.href = `/api/view?src=${encodeURIComponent(S.view.src)}`;
  pop.target = '_blank';
  pop.rel = 'noopener';
  pop.setAttribute('aria-label', 'Open in a new tab');
  pop.innerHTML = svg('pop', 16);
  head.append(pop);

  const x = document.createElement('button');
  x.className = 'icon-btn';
  x.type = 'button';
  x.setAttribute('aria-label', 'Close the preview');
  x.innerHTML = svg('x', 16);
  x.onclick = closeView;
  head.append(x);
  wrap.append(head);

  // One iframe for every kind of file. A PDF and an image are served as
  // themselves and the browser draws them; a workbook, a document or a text
  // file is rendered to HTML first. The page does not have to know which.
  frame = document.createElement('iframe');
  frame.className = 'viewer-body';
  frame.src = `/api/view?src=${encodeURIComponent(S.view.src)}`;
  frame.title = S.view.name;
  if (isWorkbook && !locked) {
    frame.onload = () => addSheetCopyButtons(frame.contentDocument);
  }
  wrap.append(frame);
  return wrap;
}

/* A Copy button beside every sheet heading in a workbook preview, so one
 * table can be taken without the rest. The preview is same-origin, so its
 * DOM is ours to add to. */
function addSheetCopyButtons(doc) {
  if (!doc) return;
  const style = doc.createElement('style');
  style.textContent = `
    .sheet-head { display: flex; align-items: center; gap: 10px; }
    .sheet-copy { font: 600 11px "Segoe UI", system-ui, sans-serif;
      color: #1F4E6B; background: #fff; border: 1px solid #DEDFD4;
      border-radius: 8px; padding: 3px 10px; cursor: pointer; }
    .sheet-copy:hover { background: #E8EEF4; }`;
  doc.head.append(style);
  doc.querySelectorAll('h2').forEach((h) => {
    const table = h.nextElementSibling?.querySelector('table');
    if (!table) return;
    const row = doc.createElement('div');
    row.className = 'sheet-head';
    h.replaceWith(row);
    const b = doc.createElement('button');
    b.className = 'sheet-copy';
    b.type = 'button';
    b.textContent = 'Copy';
    b.onclick = () => copyTables([table], h.textContent);
    row.append(h, b);
  });
}

/* Tab-separated for plain paste, HTML alongside so a paste into Excel or
 * Sheets lands cell by cell rather than as one line of text. */
async function copyTables(tables, what) {
  if (!tables.length) {
    toast('Nothing to copy yet; wait for the preview to load.');
    return;
  }
  const cell = (c) => (c.innerText || '').replace(/\s+/g, ' ').trim();
  const tsv = tables.map((t) => [...t.rows]
    .map((r) => [...r.cells].map(cell).join('\t')).join('\n')).join('\n\n');
  const html = tables.map((t) => t.outerHTML).join('<br>');
  try {
    if (window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/plain': new Blob([tsv], { type: 'text/plain' }),
        'text/html': new Blob([html], { type: 'text/html' }),
      })]);
    } else {
      await navigator.clipboard.writeText(tsv);
    }
    toast(`Copied ${what}.`);
  } catch (e) {
    toast('The browser would not give access to the clipboard.');
  }
}

/* ------------------------------------------------------- the review screen
 *
 * Nothing is copied or downloaded until someone has looked at it against the
 * page. The source sits on the left with every value boxed and labelled, the
 * values sit on the right with a tick each, and clicking either side finds
 * the other. A workbook unlocks only once every row in it is ticked.
 *
 * It lives in its own root, outside #app: render() rebuilds #app on every
 * streamed step, and the pages here must keep their scroll and their loaded
 * images while that happens. */

const RV = { msg: null, data: null, sel: null, tab: 'fields', zoom: 100,
             busy: false, error: null };

const msgByRun = (runId) => S.msgs.find((m) => m.role === 'run' && m.runId === runId);

/* The two locks for this run, as the card and the side panel need them. */
function lockOf(m, what) {
  const st = m && m.approval && m.approval[what];
  return !st || !st.complete;
}

async function refreshApproval(m) {
  if (!m || !m.runId || m.status !== 'done') return;
  try {
    const r = await api(`/api/runs/${m.runId}/review`);
    m.approval = r.state;
    render();
  } catch (e) { /* the card still renders; the lock just stays shut */ }
}

async function openReview(m, selectKey) {
  RV.msg = m;
  RV.data = null;
  RV.error = null;
  RV.sel = selectKey || null;
  RV.tab = selectKey && selectKey.startsWith('t:') ? 'tables' : 'fields';
  renderReview();
  try {
    RV.data = await api(`/api/runs/${m.runId}/review`);
    m.approval = RV.data.state;
  } catch (e) {
    RV.error = e.message;
  }
  renderReview();
  if (RV.sel) requestAnimationFrame(() => focusItem(RV.sel, true));
}

function closeReview() {
  RV.msg = null;
  RV.data = null;
  renderReview();
  render();
}

const isApproved = (key) => !!(RV.data && RV.data.state.approved.includes(key));

async function approve(keys, on) {
  if (!keys.length || RV.busy) return;
  RV.busy = true;
  try {
    const state = await api(`/api/runs/${RV.msg.runId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys, on }),
    });
    RV.data.state = state;
    RV.msg.approval = state;
  } catch (e) {
    toast(`Could not save that: ${e.message}`);
  }
  RV.busy = false;
  renderReview();
  render();
}

/* Everything that can be ticked, in the order the right-hand list shows it. */
function reviewItems() {
  if (!RV.data) return [];
  return RV.tab === 'fields'
    ? RV.data.fields.filter((f) => f.value)
    : RV.data.tables;
}

function nextUnapproved(fromKey) {
  const items = reviewItems();
  const start = Math.max(0, items.findIndex((i) => i.key === fromKey) + 1);
  return items.slice(start).concat(items.slice(0, start))
    .find((i) => !isApproved(i.key));
}

/* Select an item, and bring both its box and its row into view. */
function focusItem(key, scrollPage) {
  RV.sel = key;
  const root = el('review-root');
  root.querySelectorAll('.rv-sel').forEach((n) => n.classList.remove('rv-sel'));
  root.querySelectorAll(`[data-key="${CSS.escape(key)}"]`).forEach((n) => {
    n.classList.add('rv-sel');
    // A field sharing a box with another is reached through its own tag.
    const boxEl = n.closest('.rv-box');
    if (boxEl && scrollPage) {
      boxEl.classList.add('rv-sel');
      boxEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
      boxEl.classList.remove('rv-pulse');
      void boxEl.offsetWidth;
      boxEl.classList.add('rv-pulse');
    }
    if (n.classList.contains('rv-row')) n.scrollIntoView({ block: 'nearest' });
  });
  // An item with no box still has a page: go to it.
  const item = [...(RV.data.fields || []), ...(RV.data.tables || [])]
    .find((i) => i.key === key);
  if (scrollPage && item && !item.box) {
    const page = root.querySelector(`.rv-page[data-page="${item.page}"]`);
    if (page) page.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
}

function reviewTop() {
  const m = RV.msg;
  const top = document.createElement('div');
  top.className = 'rv-top';

  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'icon-btn';
  x.setAttribute('aria-label', 'Close the review');
  x.innerHTML = svg('x', 18);
  x.onclick = closeReview;

  const title = document.createElement('div');
  title.className = 'rv-title';
  title.innerHTML = '<b>Review</b><span></span>';
  const path = (RV.data && RV.data.source_path) || m.sourcePath || '';
  title.querySelector('span').textContent = path || m.file;
  title.querySelector('span').title = path || 'File path not recorded for this run';
  top.append(x, title);

  if (RV.data) {
    const st = RV.data.state;
    const prog = document.createElement('div');
    prog.className = 'rv-progress';
    const bar = (label, s) => {
      const p = document.createElement('span');
      p.className = `rv-count${s.complete ? ' done' : ''}`;
      p.textContent = `${label} ${s.done}/${s.need}`;
      return p;
    };
    prog.append(bar('Fields', st.fields), bar('Tables', st.tables));
    top.append(prog);

    const zoom = document.createElement('div');
    zoom.className = 'rv-zoom';
    [['-', -20], ['+', 20]].forEach(([t, d]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn';
      b.textContent = t;
      b.setAttribute('aria-label', d < 0 ? 'Zoom out' : 'Zoom in');
      b.onclick = () => {
        RV.zoom = Math.max(60, Math.min(260, RV.zoom + d));
        const pages = el('review-root').querySelector('.rv-pages-inner');
        if (pages) pages.style.width = `${RV.zoom}%`;
      };
      zoom.append(b);
    });
    top.append(zoom);

    const pdf = document.createElement('a');
    pdf.className = 'btn';
    pdf.href = `/api/runs/${m.runId}/highlighted.pdf`;
    pdf.innerHTML = `${svg('down', 14)}<span>Highlighted PDF</span>`;
    if (!RV.data.has_source) pdf.classList.add('disabled');
    top.append(pdf);

    [['fields', 'Fields (XLSX)'], ['tables', 'Table (XLSX)']].forEach(([what, label]) => {
      const locked = lockOf(m, what);
      const a = document.createElement('a');
      a.className = `btn${locked ? ' disabled' : ' primary'}`;
      a.innerHTML = `${svg(locked ? 'lock' : 'down', 14)}<span>${label}</span>`;
      if (locked) {
        a.title = `Tick every ${what === 'fields' ? 'field' : 'table'} to unlock`;
        a.onclick = (e) => {
          e.preventDefault();
          RV.tab = what;
          renderReview();
          toast(a.title + '.');
        };
        a.href = '#';
      } else {
        a.href = `/api/runs/${m.runId}/export/${what}`;
      }
      top.append(a);
    });
  }
  return top;
}

function reviewPages() {
  const d = RV.data;
  const wrap = document.createElement('div');
  wrap.className = 'rv-pages';
  if (!d.has_source) {
    const p = document.createElement('div');
    p.className = 'muted-line rv-empty';
    p.textContent = 'The document this run read was not kept, so there is no '
      + 'page to check against. Upload it again to review it.';
    wrap.append(p);
    return wrap;
  }
  const inner = document.createElement('div');
  inner.className = 'rv-pages-inner';
  inner.style.width = `${RV.zoom}%`;

  d.pages.forEach((n) => {
    const page = document.createElement('div');
    page.className = 'rv-page';
    page.dataset.page = n;
    const cap = document.createElement('div');
    cap.className = 'rv-page-no';
    cap.textContent = `Page ${n}`;
    const sheet = document.createElement('div');
    sheet.className = 'rv-sheet';
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = `Page ${n}`;
    img.src = `/api/runs/${RV.msg.runId}/page/${n}?dpi=130`;
    sheet.append(img);

    const box = (item, kind) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `rv-box ${kind}${isApproved(item.key) ? ' ok' : ''}`;
      b.dataset.key = item.key;
      const [x0, y0, x1, y1] = item.box;
      b.style.left = `${x0 * 100}%`;
      b.style.top = `${y0 * 100}%`;
      b.style.width = `${(x1 - x0) * 100}%`;
      b.style.height = `${(y1 - y0) * 100}%`;
      b.style.setProperty('--c', kind === 'table' ? d.table_color : item.color);
      b.setAttribute('aria-label', kind === 'table'
        ? `Table ${item.sheet}` : `${fieldLabel(item.field)}: ${item.value}`);
      b.onclick = () => {
        RV.tab = kind === 'table' ? 'tables' : 'fields';
        renderReview();
        requestAnimationFrame(() => focusItem(item.key, false));
      };
      return b;
    };

    d.tables.filter((t) => t.page === n && t.box).forEach((t) => {
      const b = box(t, 'table');
      const tag = document.createElement('span');
      tag.className = 'rv-tag';
      tag.textContent = `${isApproved(t.key) ? '✓ ' : ''}Table ${t.sheet}`;
      b.append(tag);
      sheet.append(b);
    });
    // Fields that are the same printed value share one box, labels side by side.
    const groups = new Map();
    d.fields.filter((f) => f.page === n && f.box).forEach((f) => {
      const k = f.box.join(',');
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(f);
    });
    groups.forEach((members) => {
      const b = box(members[0], 'field');
      const tags = document.createElement('span');
      tags.className = 'rv-tags';
      members.forEach((f) => {
        const tag = document.createElement('span');
        tag.className = 'rv-tag';
        tag.dataset.key = f.key;
        tag.style.setProperty('--c', f.color);
        tag.textContent = `${isApproved(f.key) ? '✓ ' : ''}${fieldLabel(f.field)}`;
        tag.onclick = (e) => {
          e.stopPropagation();
          RV.tab = 'fields';
          renderReview();
          requestAnimationFrame(() => focusItem(f.key, false));
        };
        tags.append(tag);
      });
      b.append(tags);
      sheet.append(b);
    });

    page.append(cap, sheet);
    inner.append(page);
  });
  wrap.append(inner);
  return wrap;
}

function reviewRow(item, kind) {
  const row = document.createElement('div');
  const ok = isApproved(item.key);
  row.className = `rv-row${ok ? ' ok' : ''}`;
  row.dataset.key = item.key;

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = ok;
  cb.setAttribute('aria-label', `Approve ${kind === 'table' ? item.sheet : fieldLabel(item.field)}`);
  cb.onclick = (e) => e.stopPropagation();
  cb.onchange = () => approve([item.key], cb.checked);

  const body = document.createElement('div');
  body.className = 'rv-row-body';
  if (kind === 'table') {
    const head = document.createElement('div');
    head.className = 'rv-row-head';
    head.innerHTML = '<span class="rv-dot"></span><b></b><small></small>';
    head.querySelector('.rv-dot').style.background = RV.data.table_color;
    head.querySelector('b').textContent = `Table ${item.sheet}`;
    head.querySelector('small').textContent = item.box ? '' : 'not outlined on the page';
    const t = document.createElement('div');
    t.className = 'rv-table';
    t.append(safeHtml(item.html));
    body.append(head, t);
  } else {
    const head = document.createElement('div');
    head.className = 'rv-row-head';
    head.innerHTML = '<span class="rv-dot"></span><b></b><span class="verdict"></span>';
    head.querySelector('.rv-dot').style.background = item.color;
    head.querySelector('b').textContent = fieldLabel(item.field);
    const V = { yes: ['ok', 'found'], check: ['warn', 'CHECK'], guess: ['warn', 'guess'] };
    const [cls, txt] = V[item.verdict] || ['none', item.verdict];
    const vt = head.querySelector('.verdict');
    vt.classList.add(cls);
    vt.textContent = txt;
    const val = document.createElement('div');
    val.className = 'rv-val';
    val.textContent = item.value;
    const where = document.createElement('small');
    where.className = 'rv-where';
    where.textContent = item.box
      ? `${item.where}${item.precise ? '' : ' · region of the page'}`
      : `${item.where} · could not be placed on the page; check it by eye`;
    body.append(head, val, where);
  }
  row.append(cb, body);
  row.onclick = () => focusItem(item.key, true);
  return row;
}

function reviewSide() {
  const d = RV.data;
  const side = document.createElement('div');
  side.className = 'rv-side';

  const tabs = document.createElement('div');
  tabs.className = 'rv-tabs';
  [['fields', 'Fields', d.state.fields], ['tables', 'Tables', d.state.tables]]
    .forEach(([id, label, st]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `rv-tab${RV.tab === id ? ' on' : ''}`;
      b.textContent = `${label} ${st.done}/${st.need}`;
      b.onclick = () => { RV.tab = id; renderReview(); };
      tabs.append(b);
    });
  side.append(tabs);

  const hint = document.createElement('div');
  hint.className = 'rv-hint';
  hint.textContent = 'Click a value to find it on the page, or a box on the '
    + 'page to find its value. Tick each one you have checked. Space ticks the '
    + 'selected one and moves to the next.';
  side.append(hint);

  const list = document.createElement('div');
  list.className = 'rv-list';
  const items = RV.tab === 'fields' ? d.fields : d.tables;
  const pages = [...new Set(items.map((i) => i.page))].sort((a, b) => a - b);
  pages.forEach((n) => {
    const onPage = items.filter((i) => i.page === n);
    const tickable = onPage.filter((i) => RV.tab === 'tables' || i.value);
    const head = document.createElement('div');
    head.className = 'rv-group';
    const h = document.createElement('span');
    h.textContent = `Page ${n}`;
    head.append(h);
    if (tickable.length) {
      const allOk = tickable.every((i) => isApproved(i.key));
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'link';
      b.textContent = allOk ? 'Untick page' : 'Approve page';
      b.onclick = () => approve(tickable.map((i) => i.key), !allOk);
      head.append(b);
    }
    list.append(head);
    tickable.forEach((i) => list.append(reviewRow(i, RV.tab === 'tables' ? 'table' : 'field')));

    const missing = onPage.filter((i) => RV.tab === 'fields' && !i.value);
    if (missing.length) {
      const miss = document.createElement('div');
      miss.className = 'rv-missing';
      miss.textContent = `Not printed on this page: ${missing.map((i) => fieldLabel(i.field)).join(', ')}`;
      list.append(miss);
    }
  });
  if (!items.length) {
    const p = document.createElement('div');
    p.className = 'muted-line';
    p.textContent = RV.tab === 'fields'
      ? 'No fields pulled from this run yet. Close this, pick the fields and press Extract again.'
      : 'The reader found no tables in this document.';
    list.append(p);
  }
  side.append(list);
  return side;
}

function renderReview() {
  let root = el('review-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'review-root';
    document.body.append(root);
  }
  // Keep both scroll positions across a re-render: ticking a box must not
  // throw the analyst back to page 1.
  const keep = {
    pages: root.querySelector('.rv-pages')?.scrollTop || 0,
    list: root.querySelector('.rv-list')?.scrollTop || 0,
  };
  root.textContent = '';
  document.body.classList.toggle('reviewing', !!RV.msg);
  if (!RV.msg) return;

  const wrap = document.createElement('div');
  wrap.className = 'review';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-label', `Review ${RV.msg.file}`);
  wrap.append(reviewTop());

  const body = document.createElement('div');
  body.className = 'rv-body';
  if (RV.error) {
    const f = document.createElement('div');
    f.className = 'fail rv-empty';
    f.textContent = RV.error;
    body.append(f);
  } else if (!RV.data) {
    const p = document.createElement('div');
    p.className = 'muted-line rv-empty';
    p.textContent = 'Placing each value on its page...';
    body.append(p);
  } else {
    body.append(reviewPages(), reviewSide());
  }
  wrap.append(body);
  root.append(wrap);

  const pagesEl = root.querySelector('.rv-pages');
  const listEl = root.querySelector('.rv-list');
  if (pagesEl) pagesEl.scrollTop = keep.pages;
  if (listEl) listEl.scrollTop = keep.list;
  if (RV.sel) {
    root.querySelectorAll(`[data-key="${CSS.escape(RV.sel)}"]`)
      .forEach((n) => n.classList.add('rv-sel'));
  }
}

document.addEventListener('keydown', (e) => {
  if (!RV.msg) return;
  if (e.key === 'Escape') { closeReview(); return; }
  if (e.target.closest && e.target.closest('input, textarea')) return;
  if ((e.key === ' ' || e.key === 'Enter') && RV.sel && RV.data) {
    e.preventDefault();
    const key = RV.sel;
    const next = nextUnapproved(key);
    approve([key], true).then(() => {
      if (next && next.key !== key) focusItem(next.key, true);
    });
  }
});

/* The drag handle. Width is written straight to the two elements while the
 * pointer moves: re-rendering the page on every mousemove would rebuild the
 * whole thread, and the drag would stutter against it. */
/* The widest the panel may be: whatever leaves the thread its minimum. The
 * old limit forgot the sidebar, so a long drag crushed the thread to 140px
 * and every line in it wrapped over the next -- and the width was remembered,
 * so it came back that way on every load. */
const MIN_THREAD = 560;
function viewMax() {
  const side = S.sidebar ? (el('app')?.querySelector('.side')?.offsetWidth || 280) : 0;
  return Math.max(320, window.innerWidth - side - MIN_THREAD);
}
const viewWidthNow = () => Math.min(Math.max(S.viewWidth, 320), viewMax());

window.addEventListener('resize', () => {
  const panel = document.querySelector('.viewer');
  if (panel) panel.style.width = `${viewWidthNow()}px`;
});

function viewGrip() {
  const grip = document.createElement('div');
  grip.className = 'viewer-grip';
  grip.setAttribute('role', 'separator');
  grip.setAttribute('aria-label', 'Resize the preview');
  grip.onpointerdown = (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    const panel = grip.nextSibling;
    const startX = e.clientX;
    const startW = panel.getBoundingClientRect().width;
    const move = (ev) => {
      const want = startW + (startX - ev.clientX);
      S.viewWidth = Math.round(Math.min(Math.max(want, 320), viewMax()));
      panel.style.width = `${S.viewWidth}px`;
    };
    const up = () => {
      grip.releasePointerCapture(e.pointerId);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      try { localStorage.setItem('lv-view-w', String(S.viewWidth)); }
      catch (err) { /* a remembered width is not worth failing over */ }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  return grip;
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
    const read = entry.file || (entry.text || '').trim();
    S.msgs = !read ? [] : [
      { role: 'you', text: '', file: { name: entry.file, pdf: false } },
      {
        role: 'run', runId: entry.id, model: entry.model,
        modelName: entry.model_name || (model ? model.name : entry.model),
        file: entry.file, sourcePath: entry.source_path || '',
        status: 'done', steps: [], text: entry.text,
        annotated: entry.annotated, error: null, elapsed: entry.elapsed,
        saved: [], docType: null, docTypeName: '',
        want: (entry.fields || []).length ? { fields: [], extra: '' } : null,
        fieldRows: (entry.fields || []).length ? entry.fields : null,
        fieldSummary: entry.field_summary || '',
        fieldsBusy: false, fieldError: null, reopened: true,
        tab: (entry.fields || []).length ? 'fields' : null,
      },
    ];
    // The typed conversation comes back with it. A thread is one document
    // plus what was asked about it, and reopening half of that was how the
    // questions came to be lost.
    (entry.turns || []).forEach((t) => {
      S.msgs.push({ role: 'you', text: t.question, file: null });
      S.msgs.push({ role: 'chat', question: t.question, answer: t.answer || '',
                    model: t.model || '', busy: false, error: null,
                    startedAt: 0 });
    });
    S.openRun = id;
    S.threadId = id;
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
  // A value that changed no longer carries its old tick.
  refreshApproval(msg);

}

async function attach(file) {
  if (!file) return;
  try {
    let r;
    if (file.size > SMALL_UPLOAD) {
      r = await uploadInPieces(file);
    } else {
      const body = new FormData();
      body.append('file', file, file.name || 'pasted.png');
      r = await api('/api/upload', { method: 'POST', body });
    }
    if (!r) return;              // stopped part way; Resume is on screen
    if (r.batch) { startBatch(r); return; }
    S.file = r;
    S.menu = null;
    render();
  } catch (e) {
    S.uploading = null;
    render();
    toast(`Could not attach that: ${e.message}`);
  }
}

/* Large files go up in pieces. From another computer this page is reached
 * through a tunnel whose relay refuses any request much over 12MB (a 413),
 * and whose throughput from there was measured at ~35KB/s with requests
 * that sometimes never complete at all. So: small pieces, a few in flight at
 * once (a slow, high-latency link moves more that way), a time limit on
 * each so a stuck one is cancelled and sent again rather than waited on
 * forever, and a Resume that carries on from the pieces that already
 * arrived if it gives up anyway. */
const SMALL_UPLOAD = 4 * 1024 * 1024;   // below this, one ordinary request
const PIECE = 1024 * 1024;
const IN_FLIGHT = 3;
const PIECE_TRIES = 6;
const PIECE_TIMEOUT_MS = 120000;        // 1MB at ~10KB/s, the worst seen

async function sendPiece(pid, at, blob) {
  for (let tries = 1; ; tries += 1) {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), PIECE_TIMEOUT_MS);
    try {
      const r = await fetch(`/api/upload/${pid}/at/${at}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' },
        body: blob, signal: stop.signal,
      });
      if (r.ok) return;
      let detail = `the connection answered ${r.status}`;
      try { detail = (await r.json()).detail || detail; } catch (e) { /* keep */ }
      // The app refusing the piece will not change on a second try.
      if (r.status === 400 || r.status === 404) throw Object.assign(new Error(detail), { fatal: true });
      throw new Error(detail);
    } catch (raw) {
      // "Failed to fetch" and "AbortError" say nothing to a person.
      const e = raw.fatal ? raw : new Error(
        raw.name === 'AbortError' ? 'a piece timed out in the connection'
          : /fetch|network/i.test(raw.message) ? 'the connection dropped'
            : raw.message);
      if (e.fatal || tries >= PIECE_TRIES) throw e;
      if (S.uploading) {
        S.uploading.retrying = `retrying a piece (try ${tries + 1} of ${PIECE_TRIES})`;
        updateUploadBar();
      }
      await new Promise((ok) => setTimeout(ok, Math.min(1500 * tries, 8000)));
    } finally {
      clearTimeout(timer);
    }
  }
}

async function uploadInPieces(file, resume) {
  let pid = resume && resume.pid;
  if (!pid) {
    const start = await api('/api/upload/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: file.name, size: file.size }),
    });
    pid = start.id;
  }
  const done = new Set(resume ? resume.done : []);
  const todo = [];
  for (let at = 0; at < file.size; at += PIECE) if (!done.has(at)) todo.push(at);
  const sentBefore = [...done].reduce((n, at) => n + Math.min(PIECE, file.size - at), 0);
  S.uploading = { name: file.name, sent: sentBefore, size: file.size,
                  began: Date.now(), sentAtStart: sentBefore, retrying: '' };
  S.uploadResume = null;
  render();

  let failure = null;
  const worker = async () => {
    while (todo.length && !failure) {
      const at = todo.shift();
      try {
        await sendPiece(pid, at, file.slice(at, Math.min(at + PIECE, file.size)));
      } catch (e) {
        failure = failure || e;
        todo.unshift(at);
        return;
      }
      done.add(at);
      S.uploading.sent += Math.min(PIECE, file.size - at);
      S.uploading.retrying = '';
      updateUploadBar();
    }
  };
  await Promise.all(Array.from({ length: IN_FLIGHT }, worker));

  if (failure) {
    // Keep what arrived: the server holds the pieces for hours, so Resume
    // sends only the missing ones. A piece the app itself refused (the
    // upload expired, say) cannot be resumed, so that one starts over.
    S.uploading = null;
    S.uploadResume = failure.fatal ? null
      : { file, pid, done: [...done], error: failure.message };
    render();
    if (failure.fatal) throw failure;
    toast(`The upload stopped at ${Math.floor(100 * sentOf(done, file.size) / file.size)}%: `
          + `${failure.message}. Press Resume to carry on from there.`);
    return null;
  }

  S.uploading.finishing = true;
  render();
  try {
    return await api(`/api/upload/${pid}/finish`, { method: 'POST' });
  } finally {
    S.uploading = null;
  }
}

function sentOf(done, size) {
  return [...done].reduce((n, at) => n + Math.min(PIECE, size - at), 0);
}

async function resumeUpload() {
  const r = S.uploadResume;
  if (!r) return;
  try {
    const out = await uploadInPieces(r.file, r);
    if (!out) return;
    if (out.batch) { startBatch(out); return; }
    S.file = out;
    render();
  } catch (e) {
    S.uploading = null;
    S.uploadResume = null;
    render();
    toast(`Could not attach that: ${e.message}`);
  }
}

/* What the bar says: how far, how fast, and whether a piece is being sent
 * again -- a slow upload and a dead one must not look the same. */
function uploadStatus(u) {
  const mb = (n) => (n / 1048576).toFixed(n < 10 * 1048576 ? 1 : 0);
  const secs = (Date.now() - u.began) / 1000;
  const rate = secs > 3 ? (u.sent - u.sentAtStart) / secs : 0;
  let text = `${Math.floor((100 * u.sent) / u.size)}% · ${mb(u.sent)} of ${mb(u.size)} MB`;
  if (rate > 0) {
    text += ` · ${(rate / 1024).toFixed(0)} KB/s`;
    const left = (u.size - u.sent) / rate;
    if (left > 5) text += ` · about ${left < 90 ? `${Math.ceil(left)}s` : `${Math.ceil(left / 60)} min`} left`;
  }
  if (u.retrying) text += ` · ${u.retrying}`;
  return text;
}

/* Moved in place, piece by piece: a full render per piece would rebuild the
 * page hundreds of times for one zip. */
function updateUploadBar() {
  const u = S.uploading;
  const fill = document.querySelector('.uploading .step-bar span');
  const pct = document.querySelector('.uploading .uploading-pct');
  if (!u || !fill) { render(); return; }
  fill.style.width = `${Math.floor((100 * u.sent) / u.size)}%`;
  if (pct) pct.textContent = uploadStatus(u);
}

function detach() {
  S.file = null;
  render();
}

function newRun() {
  S.msgs = [];
  S.openRun = null;
  S.threadId = null;
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
  if (S.batch && !S.batch.running && batchLeft().length) return true;
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
  // A thread with no document still needs somewhere to be remembered.
  if (!S.threadId) S.threadId = newThreadId();

  S.msgs.push({ role: 'you', text, file: null });
  const msg = { role: 'chat', question: text, answer: '', model: '',
                busy: true, error: null, startedAt: Date.now() };
  S.msgs.push(msg);
  S.busy = true;
  S.prompt = '';
  S.promptDirty = false;
  render();

  // Every finished exchange in this thread, oldest first. The message just
  // pushed has no answer yet, so it is not in here -- which is why nothing is
  // sliced off the end. Doing that dropped the most recent exchange, and
  // "rewrite that, more casual" arrived with the thing to rewrite missing.
  const history = [];
  S.msgs.forEach((m) => {
    if (m.role === 'chat' && m.answer) {
      history.push({ role: 'user', content: m.question });
      history.push({ role: 'assistant', content: m.answer });
    }
  });

  // A chat is one document plus what was asked about it, so the document goes
  // too. Without it the model was told it could not see one, and a question
  // about the page on screen could only be refused. The server decides how
  // much of it fits.
  const read = S.msgs.filter((m) => m.role === 'run' && m.text);
  const doc = read.length ? read[read.length - 1] : null;

  try {
    const out = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: text,
        history,
        document: doc ? doc.text : '',
        document_name: doc ? doc.file : '',
      }),
    });
    msg.answer = out.answer;
    msg.model = out.model;
  } catch (e) {
    msg.error = e.message;
  }
  msg.busy = false;
  S.busy = false;
  render();
  saveThread();
}

async function run() {
  if (S.busy) return;
  if (S.batch && !S.batch.running && batchLeft().length) return runBatch();
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
  S.threadId = null;      // the run's own id becomes it, once it is started
  S.msgs.push({ role: 'you', text: m.takes_prompt ? S.prompt : '', file: S.file });
  const msg = {
    role: 'run', model: m.id, modelName: m.name, file: S.file.name,
    sourcePath: S.file.source_path || '',
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
  // Settles once the read AND its field search are both over, which is what
  // a batch waits on before starting the next document.
  msg.settled = new Promise((resolve) => { msg._settle = resolve; });
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
    msg._settle(msg);
    return msg.settled;
  }

  msg.runId = started.id;
  S.threadId = started.id;
  msg.steps = started.steps.map((name, i) => ({ i, name, state: 'wait', note: '' }));
  S.openRun = started.id;
  render();

  const es = new EventSource(`/api/runs/${started.id}/events`);
  let lastEvent = Date.now();
  es.onmessage = (ev) => {
    lastEvent = Date.now();
    const d = JSON.parse(ev.data);
    if (d.type === 'step') {
      const s = msg.steps.find((x) => x.i === d.i);
      if (s) {
        Object.assign(s, { state: d.state, note: d.note || '', since: d.since,
                           done: d.done, total: d.total });
      }
      render();
    } else if (d.type === 'error') {
      msg.status = 'error';
      msg.error = d.error;
      es.close();
      finish(msg);
      msg._settle(msg);
    } else if (d.type === 'done') {
      es.close();
      loadResult(msg);
    }
  };
  // A dropped stream is not a finished run. Through the tunnel the stream is
  // cut off or held back, and treating its end as the end used to ask for
  // the result mid-read, get "running", and stop following the run for
  // good: the card froze and the fields were never searched for. The poll
  // below follows the run to its real end whatever the stream does.
  es.onerror = () => { es.close(); };
  const poll = async () => {
    if (msg._settled) return;
    try {
      const r = await api(`/api/runs/${started.id}`);
      if (r.status !== 'running') {
        es.close();
        await loadResult(msg, r);
        return;
      }
      // The stream has gone quiet (held back, or closed): show the server's
      // own record of the steps so progress keeps moving on screen.
      if (Date.now() - lastEvent > 4000 && r.steps && r.steps.length) {
        msg.steps = r.steps;
        render();
      }
    } catch (e) {
      if (/no such run/.test(e.message)) {
        msg.status = 'error';
        msg.error = 'The server restarted during this read. Read the document again.';
        finish(msg);
        msg._settle(msg);
        return;
      }
      // Anything else is the connection hiccuping: try again next tick.
    }
    setTimeout(poll, 2500);
  };
  setTimeout(poll, 2500);
  return msg.settled;
}

async function loadResult(msg, known) {
  // The `done` event and the poll can both land here; whichever arrives
  // first is the one that counts. `known` is a state the poll already has,
  // so a large transcript is not fetched twice through a slow tunnel.
  if (msg._settled || msg._loading) return;
  msg._loading = true;
  let r = known;
  try {
    if (!r) r = await api(`/api/runs/${msg.runId}`);
  } catch (e) {
    // A hiccup on the way: the poll asks again, rather than calling a run
    // that may well have finished a failure.
    msg._loading = false;
    return;
  }
  if (r.status === 'running') {
    msg._loading = false;     // not over yet; the poll keeps following it
    return;
  }
  msg._settled = true;
  try {
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
    await extractFields(msg, msg.want.fields, msg.want.extra);
  }
  if (msg._settle) msg._settle(msg);
}

function finish(msg) {
  S.busy = false;
  render();
}

/* ------------------------------------------------------------ a zip, read
 *
 * A zip is every document inside it, read one after another -- each its own
 * run, with its own fields, review and row in the history, exactly as if it
 * had been attached by itself. One at a time on purpose: there is one GPU,
 * and a run that starts while another is reading only waits for it anyway.
 */

/* What a zip upload hands back, as the state the batch panel draws. */
function startBatch(r) {
  S.batch = {
    name: r.name, sourcePath: r.source_path || '', skipped: r.skipped || [],
    items: r.files.map((f) => ({ file: f, status: 'queued', runId: null,
                                 msg: null, error: null, found: null })),
    running: false, stop: false, open: true,
  };
  S.file = null;
  S.menu = null;
  render();
  const n = r.files.length;
  toast(`${r.name}: ${n} document${n === 1 ? '' : 's'} ready to read`
        + (r.skipped && r.skipped.length ? `, ${r.skipped.length} skipped` : ''));
}

const batchLeft = () => (S.batch ? S.batch.items.filter(
  (i) => i.status === 'queued' || i.status === 'failed') : []);

async function runBatch() {
  const b = S.batch;
  if (!b || b.running) return;
  b.running = true;
  b.stop = false;
  render();
  for (const it of b.items) {
    if (b.stop) break;
    if (it.status === 'done') continue;
    S.file = it.file;
    it.status = 'reading';
    it.error = null;
    const msg = await run();
    if (!msg) { it.status = 'failed'; it.error = 'it did not start'; continue; }
    it.msg = msg;
    it.runId = msg.runId;
    it.status = msg.status === 'done' ? 'done' : 'failed';
    it.error = msg.error;
    it.found = (msg.fieldRows || []).filter((r) => r.value).length;
    render();
  }
  b.running = false;
  S.file = null;
  render();
  const done = b.items.filter((i) => i.status === 'done').length;
  toast(b.stop ? `Stopped after ${done} of ${b.items.length}.`
               : `${b.name}: read ${done} of ${b.items.length}.`);
}

function batchPanel() {
  const b = S.batch;
  const n = b.items.length;
  const done = b.items.filter((i) => i.status === 'done').length;
  const failed = b.items.filter((i) => i.status === 'failed').length;
  const current = b.items.find((i) => i.status === 'reading');

  const wrap = document.createElement('div');
  wrap.className = 'batch';

  const head = document.createElement('div');
  head.className = 'batch-head';
  const title = document.createElement('div');
  title.className = 'batch-title';
  title.innerHTML = '<b></b><small></small>';
  title.querySelector('b').textContent = b.name;
  title.querySelector('small').textContent = b.sourcePath || 'path not recorded';
  head.append(title);

  const count = document.createElement('span');
  count.className = 'batch-count';
  count.textContent = `${done} of ${n} read` + (failed ? ` · ${failed} failed` : '');
  head.append(count);

  const btn = (label, cls, fn) => {
    const x = document.createElement('button');
    x.type = 'button';
    x.className = `btn ${cls}`;
    x.textContent = label;
    x.onclick = fn;
    head.append(x);
  };
  if (b.running) {
    btn(b.stop ? 'Stopping after this one...' : 'Stop after this one', '',
        () => { b.stop = true; render(); });
  } else {
    const left = batchLeft().length;
    if (left) {
      btn(done || failed ? `Read the remaining ${left}` : `Read all ${left}`,
          'primary', runBatch);
    }
    btn('Clear', '', () => { S.batch = null; render(); });
  }
  const fold = document.createElement('button');
  fold.type = 'button';
  fold.className = `icon-btn batch-fold${b.open ? ' open' : ''}`;
  fold.setAttribute('aria-label', b.open ? 'Hide the list' : 'Show the list');
  fold.setAttribute('aria-expanded', String(b.open));
  fold.innerHTML = svg('chev', 16);
  fold.onclick = () => { b.open = !b.open; render(); };
  head.append(fold);
  wrap.append(head);

  const bar = document.createElement('div');
  bar.className = 'step-bar batch-bar';
  const fill = document.createElement('span');
  fill.style.width = `${Math.round(100 * (done + failed) / n)}%`;
  bar.append(fill);
  wrap.append(bar);

  if (current) {
    // The document being read right now, with its own step and clock, so the
    // batch never sits silent between one finished file and the next.
    const live = S.msgs.find((m) => m.role === 'run' && m.status === 'running');
    const step = live && live.steps.find((s) => s.state === 'active');
    const now = document.createElement('div');
    now.className = 'batch-now';
    now.append(`Reading ${current.file.name}`);
    if (step && step.note) now.append(` · ${step.note}`);
    if (step && step.since) { now.append(' '); now.append(clock(step.since)); }
    wrap.append(now);
  }

  if (b.open) {
    const list = document.createElement('div');
    list.className = 'batch-list';
    b.items.forEach((it) => {
      const row = document.createElement('div');
      row.className = `batch-row ${it.status}`;
      const pill = document.createElement('span');
      pill.className = `batch-pill ${it.status}`;
      pill.textContent = { queued: 'waiting', reading: 'reading', done: 'done',
                           failed: 'failed' }[it.status];
      const name = document.createElement('span');
      name.className = 'batch-name';
      name.textContent = it.file.inner || it.file.name;
      name.title = it.file.source_path || it.file.name;
      const meta = document.createElement('small');
      meta.textContent = it.status === 'failed' ? (it.error || '')
        : it.status === 'done' && it.found !== null ? `${it.found} value(s) found`
        : it.file.pages ? `${it.file.pages} page${it.file.pages === 1 ? '' : 's'}` : '';
      row.append(pill, name, meta);
      if (it.runId && it.status === 'done') {
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'link';
        open.textContent = S.openRun === it.runId ? 'Showing' : 'Open';
        open.disabled = S.openRun === it.runId;
        open.onclick = () => reopen(it.runId);
        row.append(open);
      }
      list.append(row);
    });
    if (b.skipped.length) {
      const sk = document.createElement('div');
      sk.className = 'batch-skipped';
      sk.textContent = 'Not read: '
        + b.skipped.map((x) => `${x.name} (${x.why})`).join('; ');
      list.append(sk);
    }
    wrap.append(list);
  }
  return wrap;
}

/* ------------------------------------------------------- getting an image */

/* The server is on this PC, so it opens the Windows file dialog itself and
 * hands back the file together with where it lives. A browser picker cannot:
 * it only ever tells a page the file's name. */
async function pickFromPC() {
  S.menu = null;
  S.picking = true;
  render();
  toast('Choose the file in the Windows dialog. It may open behind this window.');
  try {
    const r = await api('/api/pick', { method: 'POST' });
    if (r.batch) startBatch(r);
    else if (!r.cancelled) S.file = r;
  } catch (e) {
    toast(`The file dialog did not open (${e.message}). Using the browser picker instead; the path will not be recorded.`);
    chooseFile();
  }
  S.picking = false;
  render();
}

function chooseFile() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.png,.jpg,.jpeg,.webp,.bmp,.pdf,.zip';
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

  // The appraisal rating-guide page, served by this same server. A new tab,
  // so a document open here is not lost by going there.
  const tool = document.createElement('a');
  tool.className = 'pick tool';
  tool.href = '/appraisals/';
  tool.target = '_blank';
  tool.rel = 'noopener';
  tool.innerHTML = `<span class="pick-icon">${svg('table', 15)}</span>`
    + '<span class="pick-text"><span class="pick-head"><span class="pick-name">'
    + 'Appraisal rating guides</span></span>'
    + '<span class="pick-sub">A zip of appraisals, one workbook</span></span>';
  node.append(tool);

  // One list of runs, the way a chat sidebar lists conversations: newest
  // first, bucketed by when, the open one marked, and a filter once there
  // are enough of them to need one. A run joins it the moment it finishes.
  const needle = S.search.trim().toLowerCase();
  const matching = S.history.filter(
    (h) => !needle || (h.file || '').toLowerCase().includes(needle)
           || (h.title || '').toLowerCase().includes(needle)
           || (h.model_name || '').toLowerCase().includes(needle));

  // Everything above stays put; only the runs scroll, under their search box.
  const scroller = document.createElement('div');
  scroller.className = 'side-scroll';
  node.append(scroller);

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
    scroller.append(find);
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
      // A conversation with no document is named by what was first asked
      // in it, the way a chat sidebar names a chat.
      const asked = h.turns ? `${h.turns} message${h.turns === 1 ? '' : 's'}` : '';
      b.querySelector('.past-file').textContent =
        h.file || h.title || 'Untitled conversation';
      b.querySelector('.past-sub').textContent = h.file
        ? `${h.model_name}${h.fields ? `, ${h.fields} fields` : ''}`
          + (asked ? `, ${asked}` : '')
        : asked;
      b.title = h.file
        ? `${h.file}, read with ${h.model_name}`
          + (h.chars ? `, ${h.chars} characters` : '')
        : (h.title || 'Conversation');
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
    scroller.append(list);
  } else if (needle) {
    const none = document.createElement('div');
    none.className = 'muted-line no-runs';
    none.textContent = `Nothing matching "${S.search.trim()}".`;
    scroller.append(none);
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
    const name = document.createElement('button');
    name.type = 'button';
    name.className = 'openable';
    name.textContent = m.file.pdf
      ? `${m.file.name} · ${m.file.pages} page${m.file.pages === 1 ? '' : 's'}`
      : m.file.name;
    // The source, beside the values that were taken off it.
    if (m.file.id) {
      name.onclick = () => openView(`upload:${m.file.id}`, m.file.name);
    } else {
      name.disabled = true;
      name.className = '';
      name.title = 'This document was read in an earlier session and is no '
        + 'longer held on disk.';
    }
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

/* A running clock since `since` (seconds, the server's clock -- the same
 * machine). The tick below updates these in place, once a second, without
 * re-rendering the page. */
function clock(since) {
  const c = document.createElement('b');
  c.className = 'step-clock';
  c.dataset.since = String(since);
  c.textContent = elapsedText(since);
  return c;
}

function elapsedText(since) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

const ticker = setInterval(() => {
  const app = el('app');
  if (!app || !app.querySelectorAll) return;
  app.querySelectorAll('.step-clock').forEach((c) => {
    c.textContent = elapsedText(Number(c.dataset.since));
  });
}, 1000);
// Node (the headless test) would otherwise stay alive for this forever.
if (ticker && ticker.unref) ticker.unref();

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
  const sub = head.querySelector('small');
  if (m.status === 'running') {
    sub.textContent = `reading ${m.file}… `;
    // The whole read's clock, ticking: proof it is still going.
    const first = (m.steps.find((s) => s.since) || {}).since;
    if (first) sub.append(clock(first));
  } else {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'openable';
    open.textContent = m.file;
    open.onclick = () => openView(`run:${m.runId}`, m.file);
    sub.append(open);
    if (m.elapsed) sub.append(` · ${m.elapsed.toFixed(1)}s`);
  }
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
      if (s.state === 'active' && s.since && m.status === 'running') {
        row.append(clock(s.since));
      }
      steps.append(row);
      // Pages as they finish, as a bar under the step that reads them.
      if (s.state === 'active' && s.total > 1) {
        const bar = document.createElement('div');
        bar.className = 'step-bar';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', String(s.total));
        bar.setAttribute('aria-valuenow', String(s.done || 0));
        const fill = document.createElement('span');
        fill.style.width = `${Math.round(100 * (s.done || 0) / s.total)}%`;
        bar.append(fill);
        steps.append(bar);
      }
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
    // Where the sign-off stands is what matters here, more than the length.
    const note = document.createElement('small');
    const ap = m.approval;
    note.textContent = ap
      ? `Approved: fields ${ap.fields.done}/${ap.fields.need}`
        + ` · tables ${ap.tables.done}/${ap.tables.need}`
      : `${m.text.length} characters`;
    foot.append(note);

    // The two workbooks, side by side, because they answer different
    // questions: the fields one is the values another tool reads, the table
    // one is the document as it was rendered. Fields only appears once the
    // fields have actually been pulled -- an empty workbook is worse than no
    // button, since it looks like the read found nothing.
    // Both workbooks open in the panel rather than downloading straight
    // away. Checking what is about to be sent is the whole reason the panel
    // exists; the download sits in its header, one click further on.
    // The source with every value boxed on it. Checking happens there, and
    // the workbooks below stay locked until it is done.
    const hl = document.createElement('button');
    hl.className = 'btn primary';
    hl.type = 'button';
    hl.innerHTML = `${svg('mark2', 14)}<span>Highlighted PDF</span>`;
    hl.title = 'See every value on the page it came from, and approve it';
    hl.onclick = () => openReview(m);
    foot.append(hl);

    const workbook = (what, label, on) => {
      if (!on) return;
      const locked = lockOf(m, what);
      const b = document.createElement('button');
      b.className = 'btn';
      b.type = 'button';
      b.innerHTML = `${svg(locked ? 'lock' : 'sheet', 14)}<span>${label}</span>`;
      b.title = locked ? 'Preview only until every row is approved in Highlighted PDF'
                       : 'Approved: preview, copy and download';
      b.onclick = () => openView(`export:${m.runId}:${what}`,
                                 `${m.file} — ${label}`,
                                 `/api/runs/${m.runId}/export/${what}`);
      foot.append(b);
    };
    workbook('fields', 'Fields (XLSX)', m.fieldRows && m.fieldRows.length);
    workbook('tables', 'Table (XLSX)', (m.text || '').includes('<table'));
    if (m.approval === undefined) {
      m.approval = null;
      refreshApproval(m);
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

  if (S.uploading) {
    const u = S.uploading;
    const up = document.createElement('div');
    up.className = 'attached uploading';
    const line = document.createElement('div');
    line.className = 'uploading-line';
    const nm = document.createElement('span');
    nm.textContent = u.finishing ? `Unpacking ${u.name}...` : `Uploading ${u.name}`;
    const pct = document.createElement('span');
    pct.className = 'uploading-pct';
    pct.textContent = u.finishing ? '' : uploadStatus(u);
    line.append(nm, pct);
    const bar = document.createElement('div');
    bar.className = 'step-bar';
    const fill = document.createElement('span');
    fill.style.width = `${u.finishing ? 100 : Math.floor((100 * u.sent) / u.size)}%`;
    bar.append(fill);
    up.append(line, bar);
    box.append(up);
  }

  if (S.uploadResume && !S.uploading) {
    const r = S.uploadResume;
    const stopped = document.createElement('div');
    stopped.className = 'attached uploading stopped';
    const line = document.createElement('div');
    line.className = 'uploading-line';
    const msgEl = document.createElement('span');
    const pctDone = Math.floor((100 * sentOf(new Set(r.done), r.file.size)) / r.file.size);
    msgEl.textContent = `${r.file.name} stopped at ${pctDone}% (${r.error}).`;
    const acts = document.createElement('span');
    acts.className = 'uploading-acts';
    const go = document.createElement('button');
    go.type = 'button';
    go.className = 'btn primary';
    go.textContent = 'Resume';
    go.onclick = resumeUpload;
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.className = 'btn';
    drop.textContent = 'Cancel';
    drop.onclick = () => { S.uploadResume = null; render(); };
    acts.append(go, drop);
    line.append(msgEl, acts);
    stopped.append(line);
    box.append(stopped);
  }

  // During a batch the panel above names the file being read; a second,
  // ever-changing chip here would only repeat it.
  if (S.file && !(S.batch && S.batch.running)) {
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
    // Where it came from, so it can be traced -- or a plain warning that it
    // cannot be, with the way to fix that.
    const where = document.createElement('span');
    if (S.file.source_path) {
      where.className = 'attached-path';
      where.textContent = S.file.source_path;
      where.title = S.file.source_path;
    } else {
      where.className = 'attached-path missing';
      where.textContent = 'File path not recorded. Use + then Open from this PC to keep it.';
    }
    row.append(where);
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
  m.append(menuItem('doc', 'Open from this PC', 'Keeps the file’s full path for tracking', pickFromPC));
  m.append(menuItem('image', 'Browser upload', 'PDF or image; the browser hides the path', chooseFile));
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


/* The fields to pull out, inside the composer once a type is chosen.
 *
 * Closed, it is only what is selected: one small tag per field, each with a
 * cross. The full list lives behind "Choose fields", a searchable dropdown
 * where the same box that filters the list also adds a field the type does
 * not name -- so there is one place to pick from, not a grid plus a second
 * "other fields" form. */
function fieldPicker() {
  const d = docTypeById(S.docType);
  if (!d) return null;

  const panel = document.createElement('div');
  panel.className = 'picker';
  panel.onclick = (e) => e.stopPropagation();

  const head = document.createElement('div');
  head.className = 'picker-head';
  const title = document.createElement('div');
  title.innerHTML = '<strong></strong><small></small>';
  title.querySelector('strong').textContent = d.name;
  const count = S.chosen.length + S.extras.length;
  title.querySelector('small').textContent = count
    ? `${count} field${count === 1 ? '' : 's'} to find, read with ${d.reader_short}`
    : `read with ${d.reader_short}`;
  head.append(title);
  const off = document.createElement('button');
  off.type = 'button';
  off.className = 'link';
  off.textContent = 'Remove';
  off.onclick = clearDocType;
  head.append(off);
  panel.append(head);

  const row = document.createElement('div');
  row.className = 'picked';

  const tag = (name, onRemove, custom) => {
    const t = document.createElement('span');
    t.className = `picked-tag${custom ? ' custom' : ''}`;
    const txt = document.createElement('span');
    txt.textContent = custom ? name : fieldLabel(name);
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'chip-x';
    x.setAttribute('aria-label', `Remove ${txt.textContent}`);
    x.textContent = '×';
    x.onclick = onRemove;
    t.append(txt, x);
    return t;
  };
  // In the type's own order, not the order they were ticked.
  d.fields.filter((f) => S.chosen.includes(f))
    .forEach((f) => row.append(tag(f, () => toggleField(f), false)));
  S.extras.forEach((f) => row.append(tag(f, () => {
    S.extras = S.extras.filter((x) => x !== f);
    render();
  }, true)));

  const open = S.menu === 'fields';
  const choose = document.createElement('button');
  choose.type = 'button';
  choose.className = `picked-add${open ? ' on' : ''}`;
  choose.setAttribute('aria-expanded', String(open));
  choose.innerHTML = `${svg('plus', 13)}<span></span>`;
  choose.querySelector('span').textContent = count ? 'Choose fields' : 'Choose the fields to find';
  choose.onclick = () => {
    S.menu = open ? null : 'fields';
    S.fieldQuery = '';
    render();
    if (S.menu) el('field-search')?.focus();
  };
  row.append(choose);
  panel.append(row);

  if (!d.fields.length && !count) {
    // Rent roll, Lease and Tax bill carry no field list on purpose: the whole
    // document is the answer, or formats vary too much to fix one.
    const note = document.createElement('div');
    note.className = 'muted-line no-fields';
    note.textContent = d.why
      || 'This type has no fixed field list, so name the fields you want.';
    panel.append(note);
  }

  if (open) panel.append(fieldDropdown(d));
  return panel;
}

function fieldDropdown(d) {
  const box = document.createElement('div');
  box.className = 'field-menu';
  box.onclick = (e) => e.stopPropagation();

  const q = (S.fieldQuery || '').trim();
  const norm = (t) => t.toLowerCase().replace(/[_\s]+/g, ' ').trim();
  const nq = norm(q);

  const search = document.createElement('input');
  search.id = 'field-search';
  search.type = 'text';
  search.autocomplete = 'off';
  search.placeholder = d.fields.length
    ? 'Search, or type a new field and press Enter'
    : 'Type a field to find, for example Tenant name';
  search.value = S.fieldQuery || '';
  search.oninput = () => { S.fieldQuery = search.value; render(); };

  const matches = d.fields.filter((f) => !nq || norm(f).includes(nq)
                                     || norm(fieldLabel(f)).includes(nq));
  const exact = d.fields.some((f) => norm(f) === nq)
    || S.extras.some((f) => norm(f) === nq);

  const addCustom = (name) => {
    name = name.trim();
    if (!name) return;
    if (!S.extras.some((f) => norm(f) === norm(name))) S.extras.push(name);
    S.fieldQuery = '';
    render();
    el('field-search')?.focus();
  };

  search.onkeydown = (e) => {
    if (e.key === 'Escape') { S.menu = null; render(); return; }
    if (e.key !== 'Enter') return;
    e.preventDefault();
    // One match ticks it; anything else is a field of their own.
    if (nq && matches.length === 1 && !S.chosen.includes(matches[0])) {
      toggleField(matches[0]);
      S.fieldQuery = '';
      render();
      el('field-search')?.focus();
    } else if (nq && !exact) {
      // A pasted list is still one field per name.
      q.split(',').forEach(addCustom);
    }
  };
  box.append(search);

  if (d.fields.length) {
    const bar = document.createElement('div');
    bar.className = 'field-menu-bar';
    const n = document.createElement('span');
    n.textContent = `${d.fields.filter((f) => S.chosen.includes(f)).length} of ${d.fields.length} selected`;
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'link';
    all.textContent = 'Select all';
    all.onclick = () => { S.chosen = d.fields.slice(); render(); el('field-search')?.focus(); };
    const none = document.createElement('button');
    none.type = 'button';
    none.className = 'link';
    none.textContent = 'Clear';
    none.onclick = () => { S.chosen = []; render(); el('field-search')?.focus(); };
    bar.append(n, all, none);
    box.append(bar);
  }

  const list = document.createElement('div');
  list.className = 'field-menu-list';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-multiselectable', 'true');

  const option = (label, checked, onToggle, custom) => {
    const o = document.createElement('label');
    o.className = `field-option${checked ? ' on' : ''}`;
    o.setAttribute('role', 'option');
    o.setAttribute('aria-selected', String(checked));
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = checked;
    cb.onchange = () => { onToggle(); el('field-search')?.focus(); };
    const t = document.createElement('span');
    t.textContent = label;
    o.append(cb, t);
    if (custom) {
      const c = document.createElement('small');
      c.textContent = 'your field';
      o.append(c);
    }
    return o;
  };

  matches.forEach((f) => list.append(
    option(fieldLabel(f), S.chosen.includes(f), () => toggleField(f), false)));
  S.extras.filter((f) => !nq || norm(f).includes(nq)).forEach((f) => list.append(
    option(f, true, () => { S.extras = S.extras.filter((x) => x !== f); render(); }, true)));

  if (nq && !exact) {
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'field-option add';
    add.innerHTML = `${svg('plus', 13)}<span></span>`;
    add.querySelector('span').textContent = `Add "${q}" as a field`;
    add.onclick = () => q.split(',').forEach(addCustom);
    list.append(add);
  }
  if (!list.children.length) {
    const p = document.createElement('div');
    p.className = 'field-menu-empty';
    p.textContent = 'Type the name of a field to find.';
    list.append(p);
  }
  box.append(list);

  const foot = document.createElement('div');
  foot.className = 'field-menu-foot';
  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'btn primary';
  done.textContent = 'Done';
  done.onclick = () => { S.menu = null; S.fieldQuery = ''; render(); };
  foot.append(done);
  box.append(foot);
  return box;
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
    f.textContent = fieldLabel(r.field);
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
  wrap.append(bar);
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

  // The run list is rebuilt below; keep where it was scrolled to.
  const runsTop = app.querySelector('.side-scroll')?.scrollTop || 0;

  app.className = `shell${S.sidebar ? '' : ' collapsed'}`;
  app.textContent = '';
  if (S.sidebar) {
    app.append(sidebar());
    const sc = app.querySelector('.side-scroll');
    if (sc) sc.scrollTop = runsTop;
  }

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
  if (S.batch) main.append(batchPanel());

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
  if (S.view) { app.append(viewGrip()); app.append(viewPanel()); }

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
    const [models, skills, docTypes] = await Promise.all([
      api('/api/models'), api('/api/skills'), api('/api/doc_types'),
    ]);
    S.models = models;
    S.skills = skills;
    S.docTypes = docTypes;
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
