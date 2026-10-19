const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/frontend_dist/static/workflow_extras.js', 'utf8');
const values = {
  workflowSort: 'filename',
  workflowPageSize: 'A4',
  workflowMerge: 'yes',
  workflowStart: '1',
  workflowNumber: 'yes',
  workflowCompress: 'no',
};
const fields = Object.fromEntries(Object.entries(values).map(([k,v]) => [k,{
  value: v, type: k === 'workflowStart' ? 'number' : 'select-one',
  options: k === 'workflowStart' ? undefined : [{value:v}, {value:'upload'}, {value:'keep'}, {value:'no'}],
}]));
const sel = {
  value: '', options: [],
  replaceChildren() {this.options = []; this.value = '';},
  add(option) {this.options.push(option);},
};
const nodes = {
  ...fields,
  workflowTemplateName: {value:'Office automation'},
  workflowTemplateMessage: {textContent:''},
  workflowTemplateSelect: sel,
  workflowScanStatus: {textContent:''},
  workflowBlankResults: {replaceChildren(){},hidden:true},
  workflowConfirmDeletion: {hidden:true},
};
const storage = {};
const state = {files:[{name:'client-secret.pdf',size:1234}],blankApproved:null};
const context = {
  state, document: {
    addEventListener(){},
    querySelectorAll(){return [{dataset:{fileIndex:'0',page:'1'}}];}
  }, window:{confirm(){return true;}},
  localStorage:{
    getItem(key){return storage[key]||null;},
    setItem(key,value){storage[key]=value;},
  }, Option: class Option {constructor(text,value){this.text=text;this.value=value;}},
  $: name => nodes[name],
};
vm.createContext(context);
vm.runInContext(source,context);
vm.runInContext('workflowTemplateSave()',context);
const saved=storage['pdf-toolbox-workflow-templates-v1'];
assert(saved.includes('Office automation'));
assert(!saved.includes('client-secret.pdf'),'templates must never contain uploaded file names');
assert(!saved.includes('1234'),'templates must never contain uploaded file sizes');
assert.equal(JSON.parse(saved).length,1);
nodes.workflowSort.value='upload';
vm.runInContext('workflowTemplateLoad()',context);
assert.equal(nodes.workflowSort.value,'filename');
vm.runInContext('workflowConfirmBlankDeletions()',context);
assert.equal(JSON.stringify(state.blankApproved),JSON.stringify({'0':[1]}));
vm.runInContext('window.resetWorkflowScan()',context);
assert.equal(state.blankApproved,null,'changing files or clearing scan revokes prior confirmation');
vm.runInContext('workflowTemplateDelete()',context);
assert.equal(JSON.parse(storage['pdf-toolbox-workflow-templates-v1']).length,0);
console.log('Template privacy, load/delete, confirmation reset regressions passed');
