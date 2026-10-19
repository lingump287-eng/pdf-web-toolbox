/* Run with node tests/test_upload_selection.cjs. No browser dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const file = fs.readFileSync('app/frontend_dist/static/app.js', 'utf8');
const start = file.indexOf('function setFiles(fileList) {');
const end = file.indexOf('\nfunction moveFile(', start);
assert(start >= 0 && end > start);
const code = file.slice(start, end);
const elements = { fileInput: {value: 'previous selection'}, resultCard: {classList: {add() {}}}, completedCard: {classList: {add() {}}}};
const state = {busy: false, files: []};
let lastStatus = '';
let maxTotal = 1000;
const context = {
  state,
  currentTool: () => ({multiple: true, id: 'merge'}),
  fileCompatibleWithTool: f => f.name.endsWith('.pdf'),
  validateFiles: files => {
    if (files.reduce((total, f) => total + f.size, 0) > maxTotal) throw Error('limit exceeded');
  },
  renderFiles() {},
  clearPageSelection() {},
  hidePreview() {},
  loadPreview() {},
  setStatus: txt => {lastStatus = txt;},
  formatSize: n => String(n),
  $: id => elements[id],
};
vm.createContext(context);
vm.runInContext(code, context);
const f = name => ({name, size: 100, lastModified: 12});
context.setFiles([f('a.pdf'), f('b.pdf')]);
context.setFiles([f('c.pdf')]);
assert.equal(state.files.length, 3, 'second selection should append');
assert.deepEqual(state.files.map(x => x.name), ['a.pdf','b.pdf','c.pdf']);
context.setFiles([f('a.pdf')]);
assert.equal(state.files.length, 4, 'intentional duplicate must remain');
assert.equal(elements.fileInput.value, '', 'chooser value must reset');
maxTotal = 200;
context.setFiles([f('too-large.pdf')]);
assert.equal(state.files.length, 4, 'failed selection may not discard prior list');
assert.equal(lastStatus, 'limit exceeded');
console.log('Upload append/duplicate/validation regressions passed');
