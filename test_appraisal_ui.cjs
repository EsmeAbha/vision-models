// Render the appraisal page without a browser, the way test_workspace_ui.cjs does.
//
// The HTTP tests prove the server hands back the right data; this proves the
// page turns that data into the right page. app.js builds nodes with real DOM
// calls rather than innerHTML, so the shim below has to behave like elements
// do -- append, textContent, classList -- but it only has to do the handful of
// things app.js actually uses.
//
//   .\.venv\Scripts\python.exe appraisal_server.py
//   node test_appraisal_ui.cjs
const fs = require('node:fs');
const vm = require('node:vm');

const BASE = 'http://127.0.0.1:7885';
const failed = [];

// Set when the server under test wants a password, which it does whenever it
// was started by serve_appraisals.ps1 rather than run bare on loopback.
let cookie = '';

function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (cookie) headers.Cookie = cookie;
  return fetch(BASE + path, { ...opts, headers });
}

async function signIn() {
  const state = await (await fetch(BASE + '/api/session')).json();
  if (!state.required) return null;

  const password = process.env.APPRAISAL_PASSWORD || '';
  if (!password) {
    return 'that server wants a password. Either run a bare one on loopback,\n'
      + '  or set APPRAISAL_PASSWORD to the one it was started with:\n'
      + '    $env:APPRAISAL_PASSWORD = "..."; node test_appraisal_ui.cjs';
  }
  const r = await fetch(BASE + '/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!r.ok) return `the password was refused (${r.status})`;
  cookie = (r.headers.get('set-cookie') || '').split(';')[0];
  console.log(`signed in to the guarded server on ${BASE}\n`);
  return null;
}

function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) {
    console.log(`        wanted ${JSON.stringify(want)}`);
    console.log(`        got    ${JSON.stringify(got)}`);
    failed.push(label);
  }
}

/* ------------------------------------------------------------- the shim */

function textNode(s) {
  return { tag: '#text', children: [], _text: String(s), className: '' };
}

function element(tag) {
  const n = {
    tag,
    className: '',
    children: [],
    attrs: {},
    style: {},
    _text: '',
    _html: '',
    classList: {
      add(c) { n.className = (n.className + ' ' + c).trim(); },
      remove(c) {
        n.className = n.className.split(/\s+/).filter((x) => x && x !== c).join(' ');
      },
      contains(c) { return n.className.split(/\s+/).includes(c); },
    },
    append(...xs) {
      for (const x of xs) n.children.push(typeof x === 'object' ? x : textNode(x));
    },
    appendChild(x) { n.children.push(x); return x; },
    setAttribute(k, v) { n.attrs[k] = String(v); },
    getAttribute(k) { return n.attrs[k]; },
    set innerHTML(v) { n._html = String(v); n.children = []; },
    get innerHTML() { return n._html; },
    set textContent(v) { n._text = String(v); n.children = []; },
    get textContent() {
      return n.children.length
        ? n.children.map((c) => c.textContent).join('')
        : n._text;
    },
  };
  return n;
}

function walk(root, out = []) {
  out.push(root);
  for (const c of root.children || []) walk(c, out);
  return out;
}

const hasClass = (n, c) => (n.className || '').split(/\s+/).includes(c);
const byClass = (root, c) => walk(root).filter((n) => hasClass(n, c));
const byTag = (root, t) => walk(root).filter((n) => n.tag === t);

/* ------------------------------------------------------------- the page */

async function main() {
  let problem;
  try {
    problem = await signIn();
  } catch (e) {
    console.log(`the server is not answering on ${BASE}`);
    console.log('start it:  .\\.venv\\Scripts\\python.exe appraisal_server.py');
    return 2;
  }
  if (problem) { console.log(problem); return 2; }

  const app = element('div');
  const toast = element('div');
  const nodes = { app, toast };

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    document: {
      createElement: element,
      createTextNode: textNode,
      getElementById: (id) => nodes[id] || null,
      addEventListener() {},
    },
    window: { addEventListener() {}, innerWidth: 1280, location: {} },
    fetch: (url, opts) => api(url, opts),
    EventSource: function EventSource() {},
    XMLHttpRequest: function XMLHttpRequest() {},
    FormData,
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('appraisal_web/app.js', 'utf8'), sandbox,
    { filename: 'app.js' });

  // The start() at the bottom of app.js fetches the spec and re-renders.
  await new Promise((r) => setTimeout(r, 400));

  console.log('the page describes what it looks for, from the live spec');
  const spec = await (await api('/api/spec')).json();
  check('a chip per class column', byClass(app, 'cls').length,
    spec.columns.length);
  check('a chip per row of the guide',
    byClass(app, 'chip').length - byClass(app, 'cls').length,
    spec.rows.length + spec.star_rows.length);
  check('the star rows are marked apart', byClass(app, 'star').length,
    spec.star_rows.length);
  check('the floor is shown as a percentage',
    app.textContent.includes(`${Math.round(spec.min_score * 100)}%`), true);

  console.log('\nthe idle page invites an upload');
  check('a drop zone', byClass(app, 'drop').length, 1);
  check('it is not disabled', byClass(app, 'drop')[0].attrs['aria-disabled'],
    'false');
  check('no results yet', byClass(app, 'files').length, 0);

  console.log('\na finished job renders');
  vm.runInContext(`
    S.status = 'done';
    S.job = 'testjob';
    S.name = 'appraisals.zip';
    S.total = 4; S.done = 4; S.elapsed = 12.5;
    S.workbook = 'rating_guides_20261001_120000.xlsx';
    S.counts = { found: 2, not_found: 1, scanned: 1, error: 0 };
    S.files = [
      { file: 'sunset_ridge.pdf', status: 'found', page: 3, score: 1.0,
        pages: 3, note: '', title: 'Rating Guide', rows: 12 },
      { file: '2026/oak_meadows.pdf', status: 'found', page: 3, score: 1.0,
        pages: 3, note: '', title: 'Rating Guide', rows: 12 },
      { file: 'cedar_flats.pdf', status: 'not_found', page: null, score: 0.3,
        pages: 2, note: 'no page mentions the class columns', title: '', rows: 0 },
      { file: 'scanned_copy.pdf', status: 'scanned', page: null, score: 0,
        pages: 1, note: 'no text layer -- OCR is required', title: '', rows: 0 }
    ];
    render();
  `, sandbox);

  check('a count tile per outcome that occurred',
    byClass(app, 'count').length, 3);
  check('the found tile says 2',
    byClass(app, 'found').filter((n) => n.tag === 'div')[0].textContent,
    '2found');
  check('one row per file', byTag(app, 'tbody')[0].children.length, 4);
  check('the two matches are clickable', byClass(app, 'hit').length, 2);
  check('each result wears its own pill',
    byClass(app, 'pill').map((n) => n.textContent),
    ['found', 'found', 'no guide', 'scan']);
  check('a nested file keeps its path',
    byClass(app, 'name')[1].textContent, '2026/oak_meadows.pdf');
  check('the scan explains itself',
    app.textContent.includes('OCR is required'), true);
  check('the workbook is offered',
    byClass(app, 'primary')[0].textContent, 'Download workbook');

  console.log('\nthe preview draws the table that was taken');
  vm.runInContext(`
    S.selected = 0;
    S.preview = {
      file: 'sunset_ridge.pdf', page: 3, score: 1.0,
      title: 'Manufacture Housing Communites Rating Guide',
      columns: ['Class A', 'Class B', 'Class C', 'Unratable'],
      rows: [
        { category: 'Density', printed: 'Density', section: false,
          canonical: true, values: ['Low (4-7 sites/acre)', 'Medium', 'High', 'High'] },
        { category: 'Comparison to Star Rating', printed: 'Comparison to Star Rating',
          section: true, canonical: false, values: ['', '', '', ''] },
        { category: 'Star Rating (Woodall)', printed: 'Star Rating (Woodall)',
          section: false, canonical: true, values: ['N/A', 'N/A', 'N/A', 'N/A'] },
        { category: 'Location', printed: 'Location', section: false,
          canonical: false, values: ['Urban', 'Suburban', 'Rural', 'Rural'] }
      ]
    };
    render();
  `, sandbox);

  const guide = byClass(app, 'guide')[0];
  check('the heading is the caption, as printed',
    byTag(guide, 'caption')[0].textContent,
    'Manufacture Housing Communites Rating Guide');
  check('a header cell for the label and each class',
    byTag(byTag(guide, 'thead')[0], 'th').length, 5);
  check('the star band is drawn as a band',
    byClass(guide, 'band')[0].children[0].textContent,
    'Comparison to Star Rating');
  check('the band cell spans every column',
    byClass(guide, 'band')[0].children[0].colSpan, 5);
  check('a row the guide does not define is flagged',
    byClass(guide, 'extra').length, 1);
  check('values land in order',
    byClass(guide, 'v').slice(0, 4).map((n) => n.textContent),
    ['Low (4-7 sites/acre)', 'Medium', 'High', 'High']);
  check('the source is cited',
    byClass(app, 'src').some((n) => n.textContent.includes('page 3')), true);

  console.log('\nan error replaces the page with something sayable');
  vm.runInContext(`
    S.status = 'error';
    S.error = 'that archive is over 3 GB';
    render();
  `, sandbox);
  check('the message is shown', byClass(app, 'err')[0].textContent,
    'that archive is over 3 GB');

  console.log('\na page that wants a password shows a login instead');
  vm.runInContext(`
    S.status = 'idle'; S.error = null; S.selected = null; S.preview = null;
    S.auth = { required: true, signed_in: false };
    render();
  `, sandbox);
  check('a password field', byClass(app, 'pw').length, 1);
  check('the field is a password field',
    byClass(app, 'pw')[0].type, 'password');
  check('there is nothing to upload into yet', byClass(app, 'drop').length, 0);
  check('and no results are shown', byClass(app, 'files').length, 0);
  check('it says what it wants',
    app.textContent.toLowerCase().includes('password'), true);

  console.log('\na refused password is reported on that screen');
  vm.runInContext(`
    S.authError = 'that is not the password';
    render();
  `, sandbox);
  check('the reason is shown', byClass(app, 'err')[0].textContent,
    'that is not the password');
  check('the field is still there', byClass(app, 'pw').length, 1);

  console.log();
  if (failed.length) {
    console.log(`${failed.length} check(s) failed: ${failed.join(', ')}`);
    return 1;
  }
  console.log('all checks passed');
  return 0;
}

// Setting exitCode and letting node wind down on its own; calling
// process.exit() from inside a promise races libuv's handle teardown on
// Windows and prints an assertion failure after a clean run.
main().then((c) => { process.exitCode = c; },
  (e) => { console.error(e); process.exitCode = 1; });
