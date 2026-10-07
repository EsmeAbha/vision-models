// Render the chat UI against the live API without a browser installation.
//
// workspace_web builds HTML strings, so its test can stub #app with a plain
// innerHTML box. vision_web builds real nodes and calls querySelector on
// markup it has just assigned, so this carries a small DOM instead: enough
// element, class and text handling to run render() for real and read back
// what a person would see.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const BASE = 'http://127.0.0.1:7862';
const VOID = new Set(['img', 'input', 'br', 'hr', 'meta', 'link', 'path',
                      'circle', 'rect', 'line', 'polyline', 'polygon', 'use']);

class Node {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.dataset = {};
    this._text = '';
    this.classList = {
      add: (c) => { this.className = [...this._classes(), c].join(' '); },
      remove: (c) => {
        this.className = this._classes().filter((x) => x !== c).join(' ');
      },
      contains: (c) => this._classes().includes(c),
    };
  }

  _classes() { return (this.className || '').split(/\s+/).filter(Boolean); }

  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent).join('');
    return this._text;
  }

  set textContent(v) { this._text = String(v == null ? '' : v); this.children = []; }

  set innerHTML(html) { this.children = parse(String(html)); this._text = ''; }

  get innerHTML() { return this.children.map(serialise).join(''); }

  append(...nodes) {
    for (const n of nodes) if (n) this.children.push(n);
  }

  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === 'class') this.className = String(v);
  }

  getAttribute(k) { return this.attrs[k]; }

  contains() { return false; }
  removeAttribute(k) { delete this.attrs[k]; }
  remove() {}
  focus() {}
  setSelectionRange() {}

  *walk() {
    for (const c of this.children) { yield c; yield* c.walk(); }
  }

  matches(sel) {
    if (sel.startsWith('#')) return this.id === sel.slice(1);
    // ".a", ".a.b", "tag", "tag.a". Two classes used to fall through to a
    // lookup for the literal class "pick.skill" and match nothing, which
    // read as the app drawing no rows.
    const parts = sel.split('.');
    const tag = parts.shift();
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const mine = this._classes();
    return parts.every((c) => mine.includes(c));
  }

  querySelector(sel) {
    // Only the forms this app actually uses: ".cls", "#id", "tag",
    // "tag.cls", and the descendant pair ".a tag".
    const parts = sel.trim().split(/\s+/);
    if (parts.length === 2) {
      for (const n of this.walk()) {
        if (n.matches(parts[0])) {
          const hit = n.querySelector(parts[1]);
          if (hit) return hit;
        }
      }
      return null;
    }
    if (sel.includes(':last-child')) {
      const base = sel.replace(':last-child', '');
      const kids = this.children.filter((c) => c.matches(base));
      return kids.length ? kids[kids.length - 1] : null;
    }
    for (const n of this.walk()) if (n.matches(sel)) return n;
    return null;
  }

  querySelectorAll(sel) {
    return [...this.walk()].filter((n) => n.matches(sel));
  }
}

function serialise(node) {
  const cls = node.className ? ` class="${node.className}"` : '';
  const inner = node.children.length
    ? node.children.map(serialise).join('') : node._text;
  return `<${node.tagName.toLowerCase()}${cls}>${inner}</${node.tagName.toLowerCase()}>`;
}

// A tag soup parser: enough for the icon markup and the small templates the
// app assigns, including self-closing SVG shapes.
function parse(html) {
  const out = [];
  const stack = [];
  const push = (n) => (stack.length ? stack[stack.length - 1].append(n) : out.push(n));
  const re = /<\/?([a-zA-Z][\w-]*)([^>]*?)(\/?)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const [whole, tag, attrs, selfClose, text] = m;
    if (text !== undefined) {
      if (text.trim()) {
        const t = new Node('span');
        t._text = text;
        t._isText = true;
        push(t);
      }
      continue;
    }
    if (whole.startsWith('</')) { stack.pop(); continue; }
    const node = new Node(tag);
    const cls = /class="([^"]*)"/.exec(attrs || '');
    if (cls) node.className = cls[1];
    const id = /id="([^"]*)"/.exec(attrs || '');
    if (id) node.id = id[1];
    push(node);
    if (!selfClose && !VOID.has(tag.toLowerCase())) stack.push(node);
  }
  return out;
}

function visible(node) {
  // Everything a reader would see, with the SVG noise collapsed out.
  return node.textContent.replace(/\s+/g, ' ').trim();
}

const S_selected = () => globalThis.__sel ? globalThis.__sel() : null;

const seenBootCalls = [];

async function main() {
  const app = new Node('div');
  app.id = 'app';
  const toast = new Node('div');
  toast.id = 'toast';
  const byId = { app, toast };

  const root = new Node('html');
  // A reload is a fresh context over the same storage, so the sandbox has to
  // be buildable more than once.
  const makeSandbox = (storage, byIdFor) => {
    const s = {
      console, setTimeout, clearTimeout, setInterval, clearInterval,
      FormData, URL,
      fetch: (url, opts) => {
        seenBootCalls.push(url);
        return fetch(url.startsWith('http') ? url : BASE + url, opts);
      },
      localStorage: storage,
      window: { innerWidth: 1440, addEventListener() {} },
      navigator: {},
      EventSource: class { constructor() { this.close = () => {}; } },
      document: {
        getElementById: (id) => byIdFor[id] || null,
        createElement: (tag) => new Node(tag),
        addEventListener() {},
        documentElement: new Node('html'),
        activeElement: null,
        body: new Node('body'),
      },
    };
    s.globalThis = s;
    return s;
  };

  const storage = {
    _data: {},
    getItem(k) { return k in this._data ? this._data[k] : null; },
    setItem(k, v) { this._data[k] = String(v); },
  };
  const sandbox = makeSandbox(storage, byId);
  vm.createContext(sandbox);

  // Top-level const is script-scoped in a vm, so expose what the test drives.
  const source = fs.readFileSync('vision_web/app.js', 'utf8')
    + '\n;globalThis.__t = { S, render, pickDocType, pickSkill, pickModel,'
    + ' toggleField, fieldsTable, docTypeMenu, fieldPicker, templatesFor,'
    + ' knownFields, fieldsWanted, run, ask,'
    + ' extractFields, dayOf, loadHistory, toggleExpanded, isOpen,'
    + ' toggleGroup, isFolded, reopen, newRun, recall, remember };';
  vm.runInContext(source, sandbox);
  const t = sandbox.__t;
  globalThis.__sel = () => `${t.S.model}|${t.S.skill}`;

  // boot() is already running; wait for the three catalogues to arrive.
  for (let i = 0; i < 100 && !t.S.models.length; i++) {
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(t.S.models.length, 'the server returned no models');

  // ---- the shell, as it first renders -------------------------------------
  t.render();
  let seen = visible(app);
  assert.match(seen, /Local Vision/);
  assert.match(seen, /Read a document, locally/, 'empty state missing');
  assert.match(seen, /PaddleOCR-VL/);
  assert.match(seen, /DeepSeek-OCR/);
  assert.ok(!seen.includes('undefined'), 'undefined leaked into the shell');

  // Skills come off disk, so assert the shape rather than exact wording.
  assert.ok(t.S.skills.length >= 4, 'fewer skills than expected');
  for (const skill of t.S.skills) {
    assert.ok(seen.includes(skill.name), `sidebar is missing ${skill.name}`);
  }

  // ---- the detail for a model or a skill is inside its row ---------------
  // It used to hang off the right edge, which wrapped the names onto two
  // lines and left the detail only in a tooltip.
  for (const m of t.S.models) {
    assert.ok(seen.includes(m.sub), `${m.name} does not show what it does`);
  }
  // Scoped to the skill rows: the reader names also appear under Models, so
  // searching the whole page would pass with the skill rows empty.
  const skillRows = app.querySelectorAll('.pick.skill');
  assert.equal(skillRows.length, t.S.skills.length, 'skills are not all drawn');
  skillRows.forEach((row, i) => {
    const tag = row.querySelector('.pick-tag');
    assert.ok(tag && visible(tag), `skill ${i} shows no reader`);
    assert.equal(visible(tag), t.S.skills[i].model_short,
                 `skill ${i} names the wrong reader`);
  });
  assert.ok(app.querySelectorAll('.pick-head').length >= t.S.models.length,
            'rows are not laid out as a head plus a detail line');
  assert.equal(app.querySelectorAll('.pick-aside').length, 0,
               'a detail is still hung off the right edge');

  // ---- boot fetches the history ------------------------------------------
  // loadHistory() was inserted into startDeal() by mistake, so it only ran
  // when a deal was created and the Earlier section never appeared.
  assert.ok(Array.isArray(t.S.history),
            'boot never fetched the history at all');
  assert.ok(seenBootCalls.some((u) => u.includes('/api/history')),
            'boot did not ask the server for the history');

  // ---- a group heading folds its list away --------------------------------
  assert.ok(!t.isFolded('models'), 'Models starts folded');
  assert.equal(app.querySelectorAll('.pick.model').length, t.S.models.length,
               'the models are not all listed');

  t.toggleGroup('models');
  seen = visible(app);
  assert.ok(t.isFolded('models'), 'Models did not fold');
  assert.equal(app.querySelectorAll('.pick.model').length, 0,
               'folding Models left its rows on screen');
  // The heading and its count stay, so a folded group does not read as
  // empty. Scoped to the heading: a bare "2" appears elsewhere on the page.
  const heading = (name) => app.querySelectorAll('.group-label')
    .find((h) => visible(h.querySelector('.group-name')) === name);
  const modelsHead = heading('Models');
  assert.ok(modelsHead, 'the heading went with the rows');
  assert.equal(modelsHead.getAttribute('aria-expanded'), 'false',
               'a folded heading does not say it is folded');
  assert.equal(visible(modelsHead.querySelector('.group-count')),
               String(t.S.models.length),
               'a folded group does not say how many it holds');
  // Folding one must not fold the other.
  assert.equal(app.querySelectorAll('.pick.skill').length, t.S.skills.length,
               'folding Models also folded Skills');

  t.toggleGroup('models');
  assert.equal(app.querySelectorAll('.pick.model').length, t.S.models.length,
               'Models did not come back');

  t.toggleGroup('skills');
  assert.equal(app.querySelectorAll('.pick.skill').length, 0,
               'folding Skills left its rows on screen');
  assert.equal(app.querySelectorAll('.pick.model').length, t.S.models.length,
               'folding Skills also folded Models');
  t.toggleGroup('skills');

  // ---- the empty state no longer repeats the skills -----------------------
  // Four buttons there duplicated the sidebar list for no gain.
  t.S.msgs.length = 0;
  t.render();
  seen = visible(app);
  assert.match(seen, /Read a document, locally/, 'the empty state is gone');
  assert.equal(app.querySelectorAll('.starters').length, 0,
               'the starter buttons are back');
  assert.equal(app.querySelectorAll('.starter').length, 0,
               'a starter button is still drawn');

  // ---- a row opens to show what is clipped --------------------------------
  // The sidebar is narrow, so a closed row clips both the name and the
  // detail. Nothing should be reachable only by reading a tooltip.
  const skill = t.S.skills[0];
  const key = `skill:${skill.id}`;
  assert.ok(!t.isOpen(key), 'a row starts open');
  assert.equal(app.querySelectorAll('.pick-detail').length, 0,
               'a detail is showing before anything was opened');

  const toggles = app.querySelectorAll('.pick-more');
  assert.equal(toggles.length, t.S.models.length + t.S.skills.length,
               'not every row has a disclosure');

  const before = S_selected();
  t.toggleExpanded(key);
  seen = visible(app);
  assert.ok(t.isOpen(key), 'the row did not open');
  assert.equal(app.querySelectorAll('.pick-detail').length, 1,
               'opening showed no detail');
  for (const [label, value] of [['Takes', skill.takes], ['Gives', skill.gives],
                                ['Speed', skill.speed], ['Note', skill.note]]) {
    if (!value) continue;
    assert.ok(seen.includes(label), `the open row has no ${label} line`);
    assert.ok(seen.includes(value.slice(0, 40)),
              `${label} is still truncated when open`);
  }
  // Opening must not also pick the skill: the toggle is its own control.
  assert.equal(S_selected(), before,
               'opening a row changed what was selected');

  t.toggleExpanded(key);
  assert.ok(!t.isOpen(key), 'the row did not close again');
  assert.equal(app.querySelectorAll('.pick-detail').length, 0,
               'the detail stayed after closing');

  // Models open too, and say what they give back.
  const model = t.S.models[0];
  t.toggleExpanded(`model:${model.id}`);
  seen = visible(app);
  assert.match(seen, /Prompt/, 'an open model does not say whether it takes one');
  assert.ok(seen.includes(model.label), 'an open model does not give its full name');
  t.toggleExpanded(`model:${model.id}`);

  // ---- no theme switcher, and only one palette ---------------------------
  assert.equal(app.querySelectorAll('.theme-row').length, 0,
               'the theme switcher is back');
  assert.ok(!seen.includes('Tideform') && !seen.includes('Teal'),
            'the theme buttons are still drawn');

  // ---- what is folded stays folded next time ------------------------------
  // Array.from on everything that crosses out of the vm: assert/strict
  // compares prototypes, and an array built inside the context has a
  // different Array.prototype than this realm's.
  // A disclosure that springs back open on every reload is not a
  // preference, it is a nuisance.
  t.toggleGroup('skills');
  assert.deepEqual(Array.from(t.recall('collapsed', null)), ['skills'],
                   'folding a group was not remembered');
  t.toggleExpanded('model:paddleocr_vl');
  assert.deepEqual(Array.from(t.recall('expanded', null)), ['model:paddleocr_vl'],
                   'opening a row was not remembered');

  // A fresh page reads it back: a new context over the same storage.
  const reloadApp = new Node('div');
  reloadApp.id = 'app';
  const reloadToast = new Node('div');
  reloadToast.id = 'toast';
  const second = makeSandbox(storage, { app: reloadApp, toast: reloadToast });
  vm.createContext(second);
  vm.runInContext(fs.readFileSync('vision_web/app.js', 'utf8')
                  + ';globalThis.__again = { S };', second);
  assert.deepEqual(Array.from(second.__again.S.collapsed), ['skills'],
                   'a reload forgot which group was folded');
  assert.deepEqual(Array.from(second.__again.S.expanded), ['model:paddleocr_vl'],
                   'a reload forgot which row was open');

  t.toggleGroup('skills');
  t.toggleExpanded('model:paddleocr_vl');
  assert.deepEqual(Array.from(t.recall('collapsed', null)), [],
                   'unfolding was not remembered either');

  // Storage can refuse outright; the sidebar must not care.
  const realStore = sandbox.localStorage;
  sandbox.localStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  assert.deepEqual(Array.from(t.recall('collapsed', ['fallback'])), ['fallback'],
                   'a blocked read did not fall back');
  t.toggleGroup('models');
  t.toggleGroup('models');
  sandbox.localStorage = realStore;

  // ---- the run list behaves like a chat sidebar ---------------------------
  const now = new Date().toISOString();
  const old = new Date(Date.now() - 3 * 86400000).toISOString();
  t.S.history = [
    { id: 'aaaaaaaa11', file: 'utility_bill.pdf', model: 'paddleocr_vl',
      model_name: 'PaddleOCR-VL', at: now, elapsed: 71, chars: 1684, fields: 2 },
    { id: 'bbbbbbbb22', file: 'statement.pdf', model: 'paddleocr_vl',
      model_name: 'PaddleOCR-VL', at: old, elapsed: 9, chars: 21520, fields: 6 },
  ];
  t.S.openRun = null;
  t.S.search = '';
  t.render();
  seen = visible(app);
  assert.match(seen, /utility_bill\.pdf/, 'a run is not listed');
  assert.match(seen, /statement\.pdf/, 'an older run is not listed');

  // Bucketed by when, the way conversations are.
  assert.equal(t.dayOf(now), 'Today');
  assert.equal(t.dayOf(old), 'Previous 7 days');
  assert.equal(t.dayOf(new Date(Date.now() - 40 * 86400000).toISOString())
                 .match(/^[A-Z]/) !== null, true, 'an old run has no month label');
  const buckets = app.querySelectorAll('.run-bucket').map((b) => visible(b));
  assert.deepEqual(buckets, ['Today', 'Previous 7 days'], `buckets were ${buckets}`);

  // There is one list, not a separate "this session" one beside it.
  assert.ok(!seen.includes('This session'),
            'the old second list is still drawn');

  // The open run is marked, the way a selected conversation is.
  const rows = () => app.querySelectorAll('.history-item.past');
  assert.ok(rows().every((r) => r.getAttribute('aria-current') === 'false'),
            'something is marked open before anything was opened');
  t.S.openRun = 'bbbbbbbb22';
  t.render();
  const marked = rows().filter((r) => r.getAttribute('aria-current') === 'true');
  assert.equal(marked.length, 1, 'the open run is not marked');
  assert.match(visible(marked[0]), /statement\.pdf/, 'the wrong run is marked');

  // Opening replaces the thread rather than stacking onto it.
  t.S.msgs = [{ role: 'you', text: 'leftover', file: null },
              { role: 'run', runId: 'zzz', modelName: 'x', file: 'old.pdf',
                status: 'done', steps: [], text: 'old', want: null,
                fieldRows: null, saved: [], elapsed: 1, error: null,
                annotated: null, tab: null, docTypeName: '' }];
  const realFetch3 = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      id: 'aaaaaaaa11', file: 'utility_bill.pdf', model: 'paddleocr_vl',
      model_name: 'PaddleOCR-VL', text: 'fresh transcript', annotated: null,
      elapsed: 71, fields: [], field_summary: '' }),
  });
  await t.reopen('aaaaaaaa11');
  sandbox.fetch = realFetch3;
  assert.equal(t.S.openRun, 'aaaaaaaa11', 'opening did not mark the run');
  assert.equal(t.S.msgs.length, 2, 'opening stacked onto the old thread');
  assert.ok(!visible(app).includes('old.pdf'),
            'the previous thread is still on screen');
  // Fields leads the tabs, so the transcript is not the visible one. Assert
  // the opened run is what the thread holds, and that its file is on screen.
  assert.equal(t.S.msgs[1].text, 'fresh transcript',
               'the opened run brought no transcript');
  assert.equal(t.S.msgs[1].runId, 'aaaaaaaa11');
  assert.match(visible(app), /utility_bill\.pdf/,
               'the opened run is not shown in the thread');

  // New run clears both.
  t.newRun();
  assert.equal(t.S.openRun, null, 'New run left a run marked open');
  assert.equal(t.S.msgs.length, 0, 'New run left the thread behind');

  // Searching filters the list.
  t.S.history = t.S.history.concat(Array.from({ length: 6 }, (_, i) => ({
    id: `cccccccc${i}0`, file: `other_${i}.pdf`, model: 'paddleocr_vl',
    model_name: 'DeepSeek-OCR', at: now, elapsed: 1, chars: 10, fields: 0 })));
  t.S.search = 'statement';
  t.render();
  assert.equal(app.querySelectorAll('.history-item.past').length, 1,
               'the filter did not narrow the list');
  assert.match(visible(app), /statement\.pdf/);
  t.S.search = 'nothing here';
  t.render();
  assert.match(visible(app), /Nothing matching/, 'an empty filter says nothing');
  t.S.search = '';
  t.S.history = [];
  t.render();

  // ---- a document type opens the field picker -----------------------------
  const bill = t.S.docTypes.find((d) => d.name === 'Utility bill');
  assert.ok(bill, 'Utility bill type is missing');
  t.pickDocType(bill.id);
  seen = visible(app);
  assert.equal(t.S.chosen.length, bill.fields.length, 'fields not all ticked');
  assert.equal(t.S.model, bill.reader, 'picking a type did not set its reader');
  for (const field of bill.fields) {
    assert.ok(seen.includes(field), `picker is missing ${field}`);
  }
  assert.match(seen, /Other fields/);
  const boxes = app.querySelectorAll('.field-chip');
  assert.equal(boxes.length, bill.fields.length, 'wrong number of checkboxes');

  // Unticking one is reflected in the count on the composer button.
  t.toggleField(bill.fields[0]);
  assert.equal(t.S.chosen.length, bill.fields.length - 1);
  assert.match(visible(app),
               new RegExp(`${bill.fields.length - 1}/${bill.fields.length}`),
               'the composer count did not follow the picker');

  // ---- a type with no fixed list explains itself --------------------------
  const lease = t.S.docTypes.find((d) => d.fields.length === 0);
  assert.ok(lease, 'expected a type with no field list');
  t.pickDocType(lease.id);
  seen = visible(app);
  assert.equal(app.querySelectorAll('.field-chip').length, 0,
               'a type with no fields still drew checkboxes');
  assert.match(seen, /Fields to find/, 'no prompt to name fields');
  assert.ok(seen.includes(lease.why.slice(0, 40)),
            'the reason for having no field list is not shown');

  // ---- a finished run renders its fields and its template button ----------
  const bank = t.S.docTypes.find((d) => d.name === 'Bank statement');
  t.pickDocType(bank.id);
  t.S.msgs.push({
    role: 'you', text: '', file: { name: 'statement.pdf', pdf: true, pages: 2 },
  });
  t.S.msgs.push({
    role: 'run', runId: 'synthetic', model: bank.reader,
    modelName: 'PaddleOCR-VL', file: 'statement.pdf', status: 'done',
    steps: [{ i: 0, name: 'Started PaddleOCR-VL worker', state: 'done', note: '' }],
    text: '# statement', annotated: null, error: null, elapsed: 8.9, saved: [],
    docType: bank.id, docTypeName: bank.name,
    want: { fields: ['Account number', 'Ending balance'], extra: '' },
    tab: 'fields',
    fieldRows: [
      { field: 'Account number', value: '4021587390', verdict: 'yes',
        where: 'beside the label', evidence: 'Account Number 4021587390' },
      { field: 'Ending balance', value: '5,940.20', verdict: 'yes',
        where: 'under the Balance column', evidence: '' },
      { field: 'Total credits', value: '', verdict: 'none', where: '',
        evidence: '' },
    ],
    fieldSummary: '2 of 3 found. 1 label(s) not printed on the page.',
    fieldsBusy: false, fieldError: null,
  });
  t.render();
  seen = visible(app);
  assert.match(seen, /4021587390/, 'the found value is not rendered');
  assert.match(seen, /5,940\.20/, 'the corrected balance is not rendered');
  assert.match(seen, /under the Balance column/, 'the evidence column is gone');
  assert.match(seen, /not found/, 'a missing field is not marked');
  assert.match(seen, /2 of 3 found/, 'the summary line is missing');
  assert.ok(!seen.includes('undefined'), 'undefined leaked into the result card');

  // The template button only appears for a type that has one.
  if (t.templatesFor(bank.name).length) {
    assert.match(seen, /Drop these into:/, 'no template bar for Bank statement');
    assert.match(seen, /Bank statement summary/, 'template name missing');
  }
  t.S.msgs[1].docTypeName = 'Rent roll';
  t.render();
  assert.ok(!visible(app).includes('Drop these into:'),
            'template bar shown for a type with no template');

  // ---- a run made WITHOUT choosing a type first ---------------------------
  // This is the path that hid the template button: fields, and so the
  // spreadsheet, used to be reachable only by running the document again.
  t.S.msgs.length = 0;
  t.S.docType = null;
  t.S.chosen = [];
  t.S.extra = '';
  const plain = {
    role: 'run', runId: 'plain', model: bank.reader, modelName: 'PaddleOCR-VL',
    file: 'statement.pdf', status: 'done', steps: [], text: '# statement',
    annotated: null, error: null, elapsed: 9.1, saved: [],
    docType: null, docTypeName: '', want: null, fieldRows: null,
    fieldSummary: '', fieldsBusy: false, fieldError: null, tab: null,
  };
  t.S.msgs.push(plain);
  t.render();

  // The tab must be in the bar, not merely reachable by forcing m.tab, and
  // it must lead, so a plain run lands on it.
  const tabBar = app.querySelector('.tabs');
  assert.ok(tabBar, 'a finished run drew no tab bar');
  const tabNames = tabBar.children.map((b) => visible(b));
  assert.ok(tabNames.includes('Fields'),
            `Fields tab missing, bar has ${JSON.stringify(tabNames)}`);
  assert.equal(tabNames[0], 'Fields', 'Fields is not the leading tab');

  seen = visible(app);
  assert.match(seen, /Choose a document type/, 'no guidance to pick a type');
  assert.ok(!seen.includes('Extract fields'),
            'offered to extract with nothing selected');

  // Pick a type now, after the fact, and the action appears.
  t.pickDocType(bank.id);
  t.render();
  seen = visible(app);
  assert.match(seen, /Extract fields/, 'no extract action after picking a type');
  assert.match(seen, new RegExp(`${bank.name}: ${t.S.chosen.length} field`),
               'the selection is not described');

  // The button has to be wired to a real request, not merely drawn. Capture
  // the call rather than running a model.
  const extract = app.querySelectorAll('.btn')
    .find((b) => visible(b) === 'Extract fields');
  assert.ok(extract, 'the extract button is not a button');
  assert.equal(typeof extract.onclick, 'function', 'extract button is inert');
  const calls = [];
  const realFetch = sandbox.fetch;
  sandbox.fetch = (url, opts) => {
    calls.push({ url, opts });
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ rows: [], summary: '0 of 0 found.' }),
    });
  };
  await extract.onclick();
  sandbox.fetch = realFetch;
  assert.equal(calls.length, 1, 'clicking extract sent no request');
  assert.match(calls[0].url, /\/api\/runs\/plain\/fields$/,
               `extract posted to ${calls[0].url}`);
  assert.equal(calls[0].opts.method, 'POST');
  const sent = JSON.parse(calls[0].opts.body);
  assert.deepEqual(sent.fields, bank.fields,
                   'the ticked fields were not the ones sent');
  assert.equal(plain.docTypeName, bank.name,
               'the type was not recorded on the run, so no template matches');

  // ---- an error is shown as an error, not swallowed -----------------------
  const last = t.S.msgs[t.S.msgs.length - 1];
  last.status = 'error';
  last.error = 'RuntimeError: OCR worker exited unexpectedly.';
  t.render();
  assert.match(visible(app), /OCR worker exited unexpectedly/);

  // ---- each document read starts its own chat -----------------------------
  // The sidebar lists one row per run, and reopening a row replaces the
  // thread. A thread that had accumulated two documents could not be any one
  // of those rows, so reading a second document starts a fresh chat. A typed
  // question still belongs to the document above it.
  t.S.msgs = [];
  t.S.busy = false;
  t.S.openRun = 'a-previous-run';
  t.S.model = t.S.models[0].id;
  t.S.file = { id: 'no-such-upload', name: 'first.pdf', pdf: false };
  await t.run();
  assert.equal(t.S.msgs.length, 2, 'a read should leave one exchange in the thread');
  assert.equal(t.S.msgs[1].file, 'first.pdf');
  assert.notEqual(t.S.openRun, 'a-previous-run',
                  'the sidebar still points at the run before this one');

  t.S.busy = false;
  t.S.file = null;
  t.S.prompt = 'what is the total?';
  await t.ask();
  assert.equal(t.S.msgs.length, 4, 'a question should join the chat, not replace it');
  assert.equal(t.S.msgs[1].file, 'first.pdf', 'the document left the thread');

  t.S.busy = false;
  t.S.file = { id: 'no-such-upload-2', name: 'second.pdf', pdf: false };
  await t.run();
  assert.equal(t.S.msgs.length, 2,
               'the second document stacked instead of starting its own chat');
  assert.equal(t.S.msgs[1].file, 'second.pdf');
  assert.ok(!t.S.msgs.some((m) => m.file === 'first.pdf' || m.role === 'chat'),
            'the previous chat is still in the thread');

  // ---- the one palette defines every token the stylesheet uses -----------
  const css = await fetch(BASE + '/assets/style.css');
  assert.equal(css.status, 200);
  const sheet = await css.text();
  assert.ok(!/\[data-theme="teal"\]/.test(sheet), 'the teal palette is back');
  const used = new Set([...sheet.matchAll(/var\((--[\w-]+)\)/g)].map((m) => m[1]));
  const block = /:root\s*\{([^}]*)\}/.exec(sheet);
  assert.ok(block, 'no palette');
  const defined = new Set(
    [...block[1].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const missing = [...used].filter(
    (v) => !defined.has(v) && !['--sans', '--mono', '--serif'].includes(v));
  assert.equal(missing.length, 0, `the palette never defines ${missing.join(', ')}`);

  console.log(`PASS: live bootstrap, ${t.S.models.length} models, `
    + `${t.S.skills.length} skills, ${t.S.docTypes.length} document types, `
    + 'field picker, fields result, template bar, error state, one chat per read, one palette.');
  console.log('Visual browser verification remains unavailable in this environment.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
