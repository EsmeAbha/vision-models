// Render every route with live API data without requiring a browser installation.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

async function main() {
  const root = await fetch('http://127.0.0.1:7880/');
  assert.equal(root.status, 200);
  const cookie = root.headers.get('set-cookie').split(';')[0];
  const nodes = Object.fromEntries(['#app', '#toast', '#dialog'].map(key => [key, {
    innerHTML: '', classList: {add(){},remove(){}}, showModal(){this.open=true;}, close(){this.open=false;}
  }]));
  const sandbox = {
    document: {querySelector: key => nodes[key] || null, addEventListener(){}},
    window: {scrollTo(){}}, console, setTimeout, clearTimeout, setInterval(){},
    FormData, fetch: (url, options={}) => fetch('http://127.0.0.1:7880' + url, {
      ...options, headers: {...options.headers, Cookie: cookie}
    })
  };
  vm.createContext(sandbox);
  const source = fs.readFileSync('workspace_web/app.js', 'utf8').replace(/boot\(\);\s*$/, 'globalThis.ready = boot();');
  vm.runInContext(source, sandbox);
  await sandbox.ready;
  assert.match(nodes['#app'].innerHTML, /Good work starts here/);
  assert.match(nodes['#app'].innerHTML, /value="utility-bill-extraction" selected/);
  assert.match(nodes['#app'].innerHTML, /Extract Requested Fields/);
  const routes = {
    analyst: ['new', 'qc', 'monitor', 'review', 'results', 'history'],
    studio: ['catalog', 'builder', 'adapters', 'workflow', 'templates', 'bench', 'releases', 'permissions', 'observability', 'security']
  };
  for (const [workspace, pages] of Object.entries(routes)) {
    for (const page of pages) {
      vm.runInContext(`workspace=${JSON.stringify(workspace)};page=${JSON.stringify(page)};render()`, sandbox);
      assert.match(nodes['#app'].innerHTML, /class="content"/);
      assert.ok(!nodes['#app'].innerHTML.includes('undefined'), `Undefined data on ${page}`);
    }
  }
  const job = {id:'synthetic', status:'Needs review', stage:'Review findings', skill:'Utility Bill Extraction', version:'1.0.0',
    prompt:'Extract utility bill fields', progress:100, attempt:1, updated:new Date().toISOString(),
    scope:'Source evidence and review', files:[], stages:['Read pages','Extract fields'], trace:[], findings:[], artifacts:[],
    extractions:[{source:'Synthetic bill',columns:['Account number','Total due'],rows:[{values:['001234567','105.00']}],total_rows:1,pages_read:1}]};
  vm.runInContext(`workspace='analyst';page='detail';currentJob=${JSON.stringify(job)};render()`,sandbox);
  assert.match(nodes['#app'].innerHTML,/001234567/);
  assert.match(nodes['#app'].innerHTML,/Extracted fields/);
  const css = await fetch('http://127.0.0.1:7880/assets/style.css');
  assert.equal(css.status, 200);
  assert.match(await css.text(), /@media\(max-width:700px\)/);
  console.log('PASS: live bootstrap, all 16 workspace routes, static assets, responsive rules.');
  console.log('Visual browser verification remains unavailable in this environment.');
}
main().catch(error => { console.error(error); process.exitCode=1; });
