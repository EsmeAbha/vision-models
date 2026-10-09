/* Rating Guide Extraction — upload a zip of appraisals, take one table.
 *
 * The list of rows this looks for is not written here. It comes from
 * /api/spec, which reads it off the extractor, so the page cannot end up
 * advertising a row the software stopped matching on.
 *
 * File names and notes go in through textContent rather than innerHTML. They
 * come out of an archive someone else built, and a file called
 * "<img onerror=...>.pdf" is a perfectly legal file name.
 */

const ICON = {
  mark: '<path d="M4 4h16v5H4z"/><path d="M4 11h7v9H4z"/><path d="M13 11h7v9h-7z"/>',
  up: '<path d="M12 19V7"/><path d="M6 12l6-6 6 6"/><path d="M4 21h16"/>',
  down: '<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 21h16"/>',
  sheet: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 9v12"/>',
  again: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
};

function svg(name, size) {
  const s = size || 16;
  return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
    stroke-linejoin="round">${ICON[name] || ''}</svg>`;
}

const S = {
  spec: null,
  status: 'idle',      // idle | uploading | running | done | error
  job: null,
  name: '',
  note: '',
  total: 0,
  done: 0,
  files: [],
  counts: {},
  elapsed: null,
  workbook: null,
  error: null,
  uploadPct: 0,
  selected: null,
  preview: null,
  // Loopback with no password set leaves required false, and the page never
  // shows a login at all.
  auth: { required: false, signed_in: true },
  authError: null,
  authBusy: false,
};

const el = (id) => document.getElementById(id);

function toast(text) {
  const t = el('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 4200);
}

function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/* ---------------------------------------------------------------- uploading */

function pickFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,application/zip';
  input.onchange = () => { if (input.files[0]) upload(input.files[0]); };
  input.click();
}

function upload(file) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    toast('That is not a .zip — upload the archive of appraisals.');
    return;
  }
  Object.assign(S, {
    status: 'uploading', name: file.name, uploadPct: 0, files: [], total: 0,
    done: 0, counts: {}, workbook: null, error: null, selected: null,
    preview: null, note: '', elapsed: null,
  });
  render();

  const body = new FormData();
  body.append('file', file);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', 'api/jobs');

  // fetch() cannot report upload progress, and a 400 MB archive over
  // localhost is still long enough that a dead-looking page invites a second
  // upload on top of the first.
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    S.uploadPct = Math.round((e.loaded / e.total) * 100);
    render();
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* below */ }
    if (xhr.status >= 400) {
      S.status = 'error';
      S.error = data.detail || `upload failed (${xhr.status})`;
      render();
      return;
    }
    S.job = data.id;
    S.status = 'running';
    render();
    listen(data.id);
  };
  xhr.onerror = () => {
    S.status = 'error';
    // The server rejects an over-limit or unauthenticated upload without
    // reading the rest of the body, which closes the connection mid-send —
    // the browser reports that as a generic network error, so say what it
    // actually tends to mean rather than just "failed".
    S.error = 'the upload stopped partway. The server drops an archive it '
      + 'will not accept without reading the rest of it, so this usually '
      + 'means the file is too large, it is not a zip, another archive is '
      + 'still being read, or the session expired — reload and sign in again.';
    render();
  };
  xhr.send(body);
}

function listen(jid) {
  const es = new EventSource(`api/jobs/${jid}/events`);
  es.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === 'start') {
      S.total = m.total || 0;
      S.note = m.note || '';
    } else if (m.type === 'file') {
      S.done = m.done;
      S.total = m.total;
      const at = S.files.findIndex((f) => f.file === m.result.file);
      if (at >= 0) S.files[at] = m.result; else S.files.push(m.result);
    } else if (m.type === 'done') {
      S.status = 'done';
      S.counts = m.counts || {};
      S.elapsed = m.elapsed;
      S.workbook = m.workbook || null;
      if (m.note) S.note = m.note;
      es.close();
    } else if (m.type === 'error') {
      S.status = 'error';
      S.error = m.error;
      es.close();
    }
    render();
  };
  es.onerror = () => {
    // A finished stream closes on the server side; only shout if we were
    // still expecting work.
    if (S.status === 'running') {
      S.status = 'error';
      S.error = 'lost contact with the server while it was working';
      es.close();
      render();
    }
  };
}

async function showTable(index) {
  if (S.selected === index) { S.selected = null; S.preview = null; render(); return; }
  S.selected = index;
  S.preview = null;
  render();
  try {
    const r = await fetch(`api/jobs/${S.job}/table/${index}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    S.preview = await r.json();
  } catch (e) {
    toast(`Could not open that table: ${e.message}`);
    S.selected = null;
  }
  render();
}

function reset() {
  Object.assign(S, {
    status: 'idle', job: null, name: '', files: [], total: 0, done: 0,
    counts: {}, workbook: null, error: null, selected: null, preview: null,
    note: '', elapsed: null, uploadPct: 0,
  });
  render();
}

/* ------------------------------------------------------------------ pieces */

function header() {
  const h = node('header', 'top');
  const mark = node('div', 'mark');
  mark.innerHTML = svg('mark', 22);
  h.append(mark);
  const box = node('div');
  box.append(node('h1', null, 'Rating guide extraction'));
  box.append(node('p', null,
    'Upload a zip of appraisals. Every PDF inside is searched for the '
    + 'manufactured-housing rating guide — the Class A / B / C / Unratable '
    + 'grid — and only that table is taken. Everything else in the document '
    + 'is left alone.'));
  h.append(box);
  return h;
}

function dropCard() {
  const card = node('section', 'card');
  const busy = S.status === 'uploading' || S.status === 'running';

  const z = node('div', 'drop');
  z.tabIndex = 0;
  z.setAttribute('role', 'button');
  z.setAttribute('aria-disabled', String(busy));
  const ic = node('div', 'ic');
  ic.innerHTML = svg('up', 26);
  z.append(ic);

  if (S.status === 'uploading') {
    z.append(node('b', null, `Uploading ${S.name} — ${S.uploadPct}%`));
    z.append(node('small', null, 'the archive is being copied to this machine'));
  } else if (S.status === 'running') {
    z.append(node('b', null, `Reading ${S.name}`));
    z.append(node('small', null, 'the extraction is running below'));
  } else {
    z.append(node('b', null, 'Drop a .zip of appraisals here'));
    z.append(node('small', null, 'or click to choose one — PDFs at any depth '
      + 'inside the archive are found'));
  }
  if (!busy) {
    z.onclick = pickFile;
    z.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFile(); }
    };
    z.ondragover = (e) => { e.preventDefault(); z.classList.add('over'); };
    z.ondragleave = () => z.classList.remove('over');
    z.ondrop = (e) => {
      e.preventDefault();
      z.classList.remove('over');
      if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    };
  }
  card.append(z);

  if (S.status === 'uploading') {
    const bar = node('div', 'bar');
    const i = node('i');
    i.style.width = `${S.uploadPct}%`;
    bar.append(i);
    card.append(bar);
  }
  return card;
}

function specCard() {
  if (!S.spec) return null;
  const card = node('section', 'card');
  const h = node('h2');
  h.append(document.createTextNode('What counts as the rating guide'));
  card.append(h);
  card.append(node('p', 'sub',
    'A table is matched on its shape, not its heading — firms re-type the '
    + 'title, so it drifts. To be taken at all a table must carry at least '
    + `${S.spec.min_core_rows} of the ${S.spec.rows.length} rows below and `
    + `score ${Math.round(S.spec.min_score * 100)}% overall. A file whose `
    + 'closest table falls short is reported as not having the guide, rather '
    + 'than having the nearest grid returned in its place.'));

  const grid = node('div', 'spec');
  const left = node('div');
  left.append(node('h3', null, 'Columns'));
  const cc = node('div', 'chips');
  S.spec.columns.forEach((c) => cc.append(node('span', 'chip cls', c)));
  left.append(cc);
  grid.append(left);

  const right = node('div');
  right.append(node('h3', null, 'Rows'));
  const rc = node('div', 'chips');
  S.spec.rows.forEach((r) => rc.append(node('span', 'chip', r)));
  S.spec.star_rows.forEach((r) => rc.append(node('span', 'chip star', r)));
  right.append(rc);
  grid.append(right);

  card.append(grid);
  return card;
}

function progressCard() {
  if (S.status !== 'running' && S.status !== 'done') return null;
  const card = node('section', 'card');
  const h = node('h2');
  h.append(document.createTextNode(S.status === 'done' ? 'Finished' : 'Reading'));
  const c = node('span', 'c', S.name);
  h.append(c);
  card.append(h);

  const pct = S.total ? Math.round((S.done / S.total) * 100) : 0;
  const bar = node('div', 'bar');
  const i = node('i');
  i.style.width = `${S.status === 'done' ? 100 : pct}%`;
  bar.append(i);
  card.append(bar);

  const line = node('div', 'runline');
  line.append(node('span', null,
    S.status === 'done'
      ? `${S.total} file${S.total === 1 ? '' : 's'} read`
      : `${S.done} of ${S.total || '?'} read`));
  if (S.elapsed) {
    line.append(node('span', null, `${S.elapsed.toFixed(1)}s`));
  }
  card.append(line);
  if (S.note) card.append(node('div', 'src', S.note));

  if (S.status === 'done') {
    card.append(countsRow());
    const row = node('div', 'row');
    if (S.workbook) {
      const b = node('button', 'btn primary');
      b.innerHTML = svg('down', 15);
      b.append(node('span', null, 'Download workbook'));
      b.onclick = () => { window.location = `api/jobs/${S.job}/workbook`; };
      row.append(b);
      row.append(node('span', 'src', S.workbook));
    }
    const again = node('button', 'btn');
    again.innerHTML = svg('again', 15);
    again.append(node('span', null, 'Another archive'));
    again.onclick = reset;
    row.append(again);
    card.append(row);
  }
  return card;
}

const COUNT_LABEL = {
  found: ['found', 'the guide was read'],
  not_found: ['no guide', 'no table matched'],
  scanned: ['scans', 'need OCR first'],
  error: ['unreadable', 'could not be opened'],
};

function countsRow() {
  const wrap = node('div', 'counts');
  const cls = { found: 'found', not_found: 'miss', scanned: 'miss', error: 'bad' };
  Object.keys(COUNT_LABEL).forEach((k) => {
    const n = S.counts[k] || 0;
    if (!n) return;
    const d = node('div', `count ${cls[k]}`);
    d.append(node('b', null, String(n)));
    d.append(node('span', null, COUNT_LABEL[k][0]));
    wrap.append(d);
  });
  return wrap;
}

function filesCard() {
  if (!S.files.length) return null;
  const card = node('section', 'card');
  const h = node('h2');
  h.append(document.createTextNode('Files'));
  h.append(node('span', 'c', 'click a match to see the table that was taken'));
  card.append(h);

  const wrap = node('div', 'scroll');
  const t = node('table', 'files');
  const thead = node('thead');
  const hr = document.createElement('tr');
  ['File', 'Result', 'Page', 'Rows', 'Note'].forEach((x, i) => {
    const th = node('th', i === 2 || i === 3 ? 'num' : null, x);
    hr.append(th);
  });
  thead.append(hr);
  t.append(thead);

  const tb = node('tbody');
  S.files.forEach((f, i) => {
    const tr = document.createElement('tr');
    if (f.status === 'found') {
      tr.className = S.selected === i ? 'hit sel' : 'hit';
      tr.onclick = () => showTable(i);
    }
    tr.append(node('td', 'name', f.file));

    const st = node('td');
    const p = node('span', `pill ${f.status}`, {
      found: 'found', not_found: 'no guide', scanned: 'scan', error: 'error',
    }[f.status] || f.status);
    st.append(p);
    tr.append(st);

    tr.append(node('td', 'num', f.page ? String(f.page) : '—'));
    tr.append(node('td', 'num', f.status === 'found' ? String(f.rows) : '—'));
    tr.append(node('td', 'note', f.note || ''));
    tb.append(tr);
  });
  t.append(tb);
  wrap.append(t);
  card.append(wrap);
  return card;
}

function previewCard() {
  if (S.selected === null) return null;
  const card = node('section', 'card preview');
  if (!S.preview) {
    card.append(node('div', 'empty', 'opening that table…'));
    return card;
  }
  const g = S.preview;
  const wrap = node('div', 'scroll');
  const t = node('table', 'guide');

  const cap = node('caption', null, g.title || 'Rating guide');
  t.append(cap);

  const thead = node('thead');
  const hr = document.createElement('tr');
  hr.append(node('th', null, 'Category'));
  g.columns.forEach((c) => hr.append(node('th', null, c)));
  thead.append(hr);
  t.append(thead);

  const tb = node('tbody');
  g.rows.forEach((r) => {
    const tr = document.createElement('tr');
    if (r.section) {
      tr.className = 'band';
      const td = node('td', null, r.category);
      td.colSpan = g.columns.length + 1;
      tr.append(td);
    } else {
      if (!r.canonical) tr.className = 'extra';
      tr.append(node('th', null, r.printed || r.category));
      r.values.forEach((v) => tr.append(node('td', 'v', v)));
    }
    tb.append(tr);
  });
  t.append(tb);
  wrap.append(t);
  card.append(wrap);

  card.append(node('div', 'src',
    `${g.file} · page ${g.page} · match score ${g.score}`));
  return card;
}

function loginCard() {
  const card = node('section', 'card');
  const h = node('h2');
  h.append(document.createTextNode('Sign in'));
  card.append(h);
  card.append(node('p', 'sub',
    'This page is reachable from outside this machine, so it asks for the '
    + 'password before it will read anything.'));

  const row = node('div', 'row');
  const input = document.createElement('input');
  input.type = 'password';
  input.className = 'pw';
  input.placeholder = 'Password';
  input.autocomplete = 'current-password';
  input.setAttribute('aria-label', 'Password');
  row.append(input);

  const go = node('button', 'btn primary');
  go.append(node('span', null, S.authBusy ? 'Checking…' : 'Sign in'));
  go.disabled = S.authBusy;
  const submit = async () => {
    if (S.authBusy) return;
    S.authBusy = true;
    S.authError = null;
    render();
    try {
      const r = await fetch('api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: input.value || '' }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      S.auth = { required: true, signed_in: true };
      S.authBusy = false;
      await loadSpec();
    } catch (e) {
      S.authBusy = false;
      S.authError = e.message;
    }
    render();
  };
  go.onclick = submit;
  input.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
  row.append(go);
  card.append(row);

  if (S.authError) {
    const err = node('div', 'err');
    err.style.marginTop = '12px';
    err.textContent = S.authError;
    card.append(err);
  }
  // The field is created fresh on every render, so put the caret back.
  setTimeout(() => { try { input.focus(); } catch (e) { /* shim */ } }, 0);
  return card;
}

function errorCard() {
  if (S.status !== 'error') return null;
  const card = node('section', 'card');
  card.append(node('div', 'err', S.error || 'something went wrong'));
  const row = node('div', 'row');
  row.style.marginTop = '12px';
  const again = node('button', 'btn');
  again.innerHTML = svg('again', 15);
  again.append(node('span', null, 'Start over'));
  again.onclick = reset;
  row.append(again);
  card.append(row);
  return card;
}

function footer() {
  const f = node('div', 'foot');
  f.append(document.createTextNode(
    'Everything here runs on this machine; no file leaves it. The workbook '
    + 'holds an index of every file, one combined sheet that puts the '
    + 'appraisals side by side, and a verbatim copy of each table. Extracted '
    + 'values are read from the PDF text layer, not retyped — but a scanned '
    + 'appraisal has no text layer, and is reported rather than guessed at.'));
  return f;
}

/* ----------------------------------------------------------------- render */

function render() {
  const app = el('app');
  app.textContent = '';
  const locked = S.auth.required && !S.auth.signed_in;
  const cards = locked
    ? [header(), loginCard(), footer()]
    : [header(), errorCard(), dropCard(), specCard(), progressCard(),
      filesCard(), previewCard(), footer()];
  cards.forEach((n) => { if (n) app.append(n); });
}

// A file dropped anywhere but the zone would otherwise be opened by the
// browser, replacing the page with the raw archive.
['dragover', 'drop'].forEach((ev) => {
  window.addEventListener(ev, (e) => e.preventDefault());
});

async function loadSpec() {
  const r = await fetch('api/spec');
  if (r.status === 401) {          // the session lapsed mid-use
    S.auth = { required: true, signed_in: false };
    return;
  }
  S.spec = await r.json();
}

(async function start() {
  render();
  try {
    S.auth = await (await fetch('api/session')).json();
    if (!(S.auth.required && !S.auth.signed_in)) await loadSpec();
  } catch (e) {
    toast('Could not reach the server — is appraisal_server.py running?');
  }
  render();
}());
