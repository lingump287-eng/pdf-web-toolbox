const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/frontend_dist/static/result_extras.js', 'utf8');

function element() {
  const list = new Set();
  return {
    children: [],
    classList: {add: x => list.add(x), remove: x => list.delete(x), contains: x => list.has(x)},
    append(...kids) {this.children.push(...kids);},
    appendChild(child) {this.children.push(child);},
    replaceChildren() {this.children=[];},
    addEventListener() {},
    querySelectorAll() {return [];},
    textContent:'',
  };
}
const names=['recoveryCard','recoveryMessage','recoveryHint','recoveryActionBtn',
  'recoveryDismissBtn','resultPreviewCard','resultPreviewSummary',
  'resultPreviewItems','resultPreviewImages','runBtn','cancelBtn'];
const nodes=Object.fromEntries(names.map(n=>[n,element()]));
const requests=[];
const context={
  document:{addEventListener(){},createElement(){return element();}},
  window:{}, $:name=>nodes[name],
  state:{busy:false,jobId:null,pollTransientErrors:0},
  formatSize:n=>n+' B',
  fetch:async url=>{
    requests.push(url);
    return {ok:true,json:async()=>({kind:'pdf',page_count:2,size_bytes:1530})};
  },
  encodeURIComponent,
};
vm.createContext(context);
vm.runInContext(code,context);
(async()=>{
  context.window.resultUI.showRecovery('文件已加密','resubmit');
  assert.equal(nodes.recoveryCard.classList.contains('hidden'),false);
  assert(nodes.recoveryHint.textContent.includes('解密'));
  context.window.resultUI.showRecovery('网络连接断开','recheck','task42');
  assert.equal(nodes.recoveryActionBtn.textContent,'继续查询原任务');
  await context.window.resultUI.showPreview('task42');
  assert.equal(requests[0],'/api/jobs/task42/result-summary');
  assert.equal(nodes.resultPreviewImages.children.length,2);
  const img=nodes.resultPreviewImages.children[0].children[0];
  assert(img.src.includes('/api/jobs/task42/result-preview?page=1&file_index=0'));
  context.window.resultUI.clear();
  assert.equal(nodes.resultPreviewImages.children.length,0);
  assert.equal(nodes.resultPreviewCard.classList.contains('hidden'),true);
  console.log('Result preview and recovery UI regressions passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
