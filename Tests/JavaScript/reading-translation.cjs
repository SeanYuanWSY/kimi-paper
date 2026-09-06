const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../Resources/paper_viewer.js'), 'utf8');

function fixture(saved = {}) {
  const output = {textContent: ''}, calls = [];
  const popup = {hidden: true, style: {}}, popupOutput = {textContent: ''};
  let response;
  const context = vm.createContext({
    sessionStorage: {getItem: key => saved[key] ?? null, setItem: (key,value) => {saved[key]=value;}},
    openCompose() {}, focusComment() {}, jumpToComment() {}, document: {addEventListener() {}}, state: {},
    $: id => id === '#kp-selection-translation' ? popup : id === '#kp-selection-output' ? popupOutput : output, clearTimeout, setTimeout, setInterval,
    window: {innerWidth: 640, innerHeight: 480, webkit: {messageHandlers: {paper: {postMessage(body) {
      return new Promise(resolve => calls.push({body, resolve}));
    }}}}},
    fetch: async () => ({ok: true, json: () => new Promise(resolve => {response = resolve;})}),
  });
  vm.runInContext(source, context);
  return {context, output, popup, popupOutput, calls, resolveHTTP: value => response(value), run: code => vm.runInContext(code, context)};
}

(async () => {
  const disabled = fixture({"kp.selection-enabled":"false"});
  assert.equal(disabled.run("kpSelectionEnabled"), false, "PDF refresh must preserve the selection translation toggle");
  const f = fixture();
  await f.run("kpTranslate('automatic while disabled')");
  assert.equal(f.calls.length, 0);
  const old = f.run("kpTranslate('old selected text', true)");
  const latest = f.run("kpTranslate('new selected text', true)");
  f.calls[1].resolve({text: 'new translation'}); await latest;
  f.calls[0].resolve({text: 'obsolete translation'}); await old;
  assert.equal(f.output.textContent, 'new translation');
  assert.equal(f.run('kpTranslationEnabled'), false, 'Selection must not enable scrolling translation');

  const g = fixture();
  g.run("kpTranslationEnabled = true; kpReadKey = 'viewport'");
  const visible = g.run("kpReadVisible('viewport', [], 'paper-version', 0)");
  await new Promise(resolve => setImmediate(resolve));
  const selected = g.run("kpTranslate('explicit selection', true)");
  g.resolveHTTP({text: 'obsolete whole viewport'}); await visible;
  assert.equal(g.calls.length, 1, 'Late viewport extraction must not replace a manual selection');
  g.calls[0].resolve({text: 'selected translation'}); await selected;
  assert.equal(g.output.textContent, 'selected translation');
  const p = fixture();
  p.run("state.pdfDigest = 'version1'; state.selection = {getSelectedText: () => ({toPromise: async () => ['first selection']})}; kpPopupPoint = {x:630,y:470}");
  const firstPopup = p.run('kpShowSelection()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.popup.hidden, false);
  assert.equal(p.calls.length, 1);
  assert.ok(parseFloat(p.popup.style.left) + parseFloat(p.popup.style.width) <= 628);
  assert.ok(parseFloat(p.popup.style.top) >= 12);
  p.run("state.selection.getSelectedText = () => ({toPromise: async () => ['second selection']})");
  await p.run('kpShowSelection()');
  p.run("state.selection.getSelectedText = () => ({toPromise: async () => ['newest selection']})");
  await p.run('kpShowSelection()');
  assert.equal(p.calls.length, 1, 'Only one popup request runs; obsolete queued selections are replaced');
  p.calls[0].resolve({text:'obsolete popup'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.calls.length, 2);
  assert.equal(p.calls[1].body.text, 'newest selection');
  assert.notEqual(p.popupOutput.textContent, 'obsolete popup');
  p.run('kpClosePopup()');
  p.calls[1].resolve({text:'closed result'}); await firstPopup;
  assert.equal(p.popup.hidden, true, 'Closed popup must not reopen when a request finishes');
  assert.notEqual(p.popupOutput.textContent, 'closed result');
  console.log('PASS: translation stays on the newest selection; obsolete viewport responses are ignored (mock bridge)');
})().catch(error => {console.error(error); process.exitCode = 1;});
