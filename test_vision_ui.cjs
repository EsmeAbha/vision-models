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

async function main() {
  const app = new Node('div');
  app.id = 'app';
  const toast = new Node('div');
  toast.id = 'toast';
  const byId = { app, toast };

  const root = new Node('html');
  const sandbox = {
    console, setTimeout, clearTimeout,
    FormData, URL,
    fetch: (url, opts) => fetch(url.startsWith('http') ? url : BASE + url, opts),
    localStorage: { getItem: () => null, setItem() {} },
    window: { innerWidth: 1440, addEventListener() {} },
    navigator: {},
    EventSource: class { constructor() { this.close = () => {}; } },
    document: {
      getElementById: (id) => byId[id] || null,
      createElement: (tag) => new Node(tag),
      addEventListener() {},
      documentElement: root,
      activeElement: null,
      body: new Node('body'),
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  // Top-level const is script-scoped in a vm, so expose what the test drives.
  const source = fs.readFileSync('vision_web/app.js', 'utf8')
    + '\n;globalThis.__t = { S, render, pickDocType, pickSkill, pickModel,'
    + ' toggleField, fieldsTable, docTypeMenu, fieldPicker, templatesFor,'
    + ' dealPanel, dealMenu, addMappingRow, knownFields, fieldsWanted,'
    + ' extractFields, dayOf, loadHistory, toggleExpanded, isOpen,'
    + ' toggleGroup, isFolded };';
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

  // ---- earlier sessions are listed and can be reopened -------------------
  t.S.history = [
    { id: 'aaaaaaaa11', file: 'utility_bill.pdf', model: 'paddleocr_vl',
      model_name: 'PaddleOCR-VL', at: new Date().toISOString(),
      elapsed: 71.4, chars: 1684, fields: 2 },
    { id: 'bbbbbbbb22', file: 'statement.pdf', model: 'paddleocr_vl',
      model_name: 'PaddleOCR-VL', at: '2020-01-02T09:00:00', elapsed: 9,
      chars: 21520, fields: 6 },
  ];
  t.render();
  seen = visible(app);
  assert.match(seen, /Earlier/, 'no Earlier section');
  assert.match(seen, /utility_bill\.pdf/, 'a remembered run is not listed');
  assert.match(seen, /statement\.pdf/, 'an older run is not listed');
  assert.match(seen, /Today/, 'runs are not grouped by day');
  assert.equal(t.dayOf(new Date().toISOString()), 'Today');
  assert.equal(app.querySelectorAll('.past-forget').length, 2,
               'no way to forget a remembered run');

  // A run already open in the thread is not offered again below it.
  t.S.msgs.push({ role: 'run', runId: 'aaaaaaaa11', modelName: 'PaddleOCR-VL',
                  file: 'utility_bill.pdf', status: 'done', steps: [],
                  text: 'x', want: null, fieldRows: null, saved: [],
                  elapsed: 1, error: null, annotated: null, tab: null,
                  docTypeName: '', reopened: true });
  t.render();
  assert.equal(app.querySelectorAll('.past-row').length, 1,
               'a run already open is still listed as earlier');
  t.S.msgs.length = 0;
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

  // ---- the new-deal form survives being clicked ---------------------------
  // It used to live on S.menu, and the global click handler clears S.menu on
  // any click, so clicking into the name box destroyed the form before a
  // character could be typed.
  t.S.newDeal = true;
  t.S.dealName = '';
  t.render();
  seen = visible(app);
  assert.match(seen, /New deal/, 'the new-deal form did not render');
  assert.match(seen, /Name the deal, then choose its workbook/,
               'no hint that the name comes first');

  const chooser = () => app.querySelectorAll('.btn')
    .find((b) => visible(b).includes('Choose workbook'));
  assert.ok(chooser(), 'no workbook button');
  assert.equal(chooser().disabled, true,
               'offered to take a workbook before the deal was named');

  // A click anywhere closes menus. The form has to still be there.
  t.S.menu = 'deal';
  t.S.menu = null;
  t.render();
  assert.ok(visible(app).includes('New deal'),
            'the form vanished when a menu was closed');

  // Typing a name enables the button.
  const nameBox = app.querySelector('#deal-name');
  assert.ok(nameBox, 'no name field');
  nameBox.value = 'Maple Avenue';
  nameBox.oninput();
  assert.equal(t.S.dealName, 'Maple Avenue', 'the name was not captured');
  assert.equal(chooser().disabled, false,
               'the button stayed disabled after naming the deal');

  t.S.newDeal = false;
  t.S.dealName = '';
  t.render();

  // ---- a mapping row can be added by hand ---------------------------------
  // The scan knows only the names the document types list, so a template
  // with its own labels is unusable without this.
  t.S.deal = {
    id: 'sample', name: 'sample', confirmed: false, filled: {}, history: [],
    mapping: [{ field: 'Account number', cell: 'D7', sheet: 'Utility Recon',
                label_cell: 'D6', label_text: 'Account Number',
                matched: 'account number', how: 'under the label' }],
  };
  t.render();
  seen = visible(app);
  assert.match(seen, /sample/, 'the deal panel did not render');
  assert.match(seen, /Account Number/, 'the matched label is not shown');
  assert.ok(t.knownFields().length > 10, 'no suggestions to offer');

  const fieldBox = app.querySelector('#add-field');
  const cellBox = app.querySelector('#add-cell');
  assert.ok(fieldBox && cellBox, 'no row for adding a field by hand');

  // A name the vocabulary has never heard of still has to be accepted.
  fieldBox.value = 'Meter number';
  fieldBox.oninput();
  cellBox.value = 'c18';
  cellBox.oninput();
  t.addMappingRow();
  assert.equal(t.S.deal.mapping.length, 2, 'the row was not added');
  const added = t.S.deal.mapping[1];
  assert.equal(added.field, 'Meter number');
  assert.equal(added.cell, 'C18', 'the cell was not normalised to upper case');
  assert.equal(added.how, 'set by hand');

  // Nonsense is refused rather than stored.
  fieldBox.value = 'Tariff';
  fieldBox.oninput();
  cellBox.value = 'over there';
  cellBox.oninput();
  t.addMappingRow();
  assert.equal(t.S.deal.mapping.length, 2, 'a bad cell reference was accepted');

  // And the added field is actually looked for, or it could never fill.
  assert.ok(t.fieldsWanted().includes('Meter number'),
            'a hand-added field is not searched for');

  t.S.deal = null;
  t.S.addField = '';
  t.S.addCell = '';
  t.render();

  // ---- reading a document into an open deal fills it ----------------------
  // Confirming a mapping did nothing by itself, so a deal could sit at
  // "0 of 3 cells filled" with a workbook that downloaded empty and no
  // sign of what had been missed.
  t.S.deal = {
    id: 'sample', name: 'sample', confirmed: true, filled: {}, history: [],
    mapping: [{ field: 'Account number', cell: 'D7', sheet: 'Utility Recon',
                label_cell: 'D6', label_text: 'Account Number',
                matched: 'account number', how: 'under the label' }],
  };
  t.render();
  assert.match(visible(app), /Nothing read into it yet/,
               'an empty deal does not say what to do about it');

  const into = {
    role: 'run', runId: 'into-deal', model: bank.reader,
    modelName: 'PaddleOCR-VL', file: 'bill.pdf', status: 'done', steps: [],
    text: '# bill', annotated: null, error: null, elapsed: 7, saved: [],
    docType: null, docTypeName: '', want: null, fieldRows: null,
    fieldSummary: '', fieldsBusy: false, fieldError: null, tab: null,
  };
  t.S.msgs.length = 0;
  t.S.msgs.push(into);

  const seenCalls = [];
  const realFetch2 = sandbox.fetch;
  sandbox.fetch = (url, opts) => {
    seenCalls.push(url);
    const body = url.endsWith('/fields')
      ? { rows: [{ field: 'Account number', value: 'EL-88342710',
                   verdict: 'yes', where: 'beside the label', evidence: 'x' }],
          summary: '1 of 1 found.' }
      : url.endsWith('/apply')
        ? { written: [{ field: 'Account number', cell: 'D7',
                        value: 'EL-88342710' }],
            skipped: [], clashed: [], download: '/api/deals/sample/workbook' }
        : { id: 'sample', name: 'sample', confirmed: true, mapping: [],
            filled: { 'Account number': {} }, history: [{ source: 'bill.pdf' }] };
    return Promise.resolve({ ok: true, status: 200,
                             json: () => Promise.resolve(body) });
  };
  await t.extractFields(into, ['Account number'], '');
  sandbox.fetch = realFetch2;

  assert.ok(seenCalls.some((u) => u.endsWith('/fields')), 'nothing was extracted');
  assert.ok(seenCalls.some((u) => u.endsWith('/apply')),
            'the document was never put into the open deal');
  assert.ok(into.dealResult, 'no record of what went into the deal');
  assert.equal(into.dealResult.written[0].cell, 'D7');

  t.S.deal = null;
  t.render();

  // ---- an error is shown as an error, not swallowed -----------------------
  const last = t.S.msgs[t.S.msgs.length - 1];
  last.status = 'error';
  last.error = 'RuntimeError: OCR worker exited unexpectedly.';
  t.render();
  assert.match(visible(app), /OCR worker exited unexpectedly/);

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
    + 'field picker, fields result, template bar, error state, one palette.');
  console.log('Visual browser verification remains unavailable in this environment.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
