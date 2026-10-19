/* Workflow templates and explicitly confirmed blank-page selections.
 * Templates are local to the browser; files and detected page images are NEVER stored.
 */
const WORKFLOW_TEMPLATE_STORAGE = 'pdf-toolbox-workflow-templates-v1';
const WORKFLOW_TEMPLATE_LIMIT = 15;
const WORKFLOW_FIELD_IDS = ['workflowSort', 'workflowPageSize', 'workflowMerge', 'workflowStart', 'workflowNumber', 'workflowCompress'];
let workflowScanEpoch = 0;
let workflowCandidates = [];

function workflowCurrentOptions() {
  const options = {};
  for (const id of WORKFLOW_FIELD_IDS) options[id] = $(id).value;
  return options;
}

function workflowTemplatesRead() {
  try {
    const stored = JSON.parse(localStorage.getItem(WORKFLOW_TEMPLATE_STORAGE) || '[]');
    return Array.isArray(stored) ? stored.filter(v => v && typeof v.name === 'string' && v.options && typeof v.options === 'object').slice(0, WORKFLOW_TEMPLATE_LIMIT) : [];
  } catch (_) { return []; }
}

function workflowTemplatesWrite(templates) {
  try {
    localStorage.setItem(WORKFLOW_TEMPLATE_STORAGE, JSON.stringify(templates));
    return true;
  } catch (_) {
    $('workflowTemplateMessage').textContent = '浏览器存储不可用，无法保存。';
    return false;
  }
}

function workflowTemplateList() {
  const select = $('workflowTemplateSelect');
  const selected = select.value;
  select.replaceChildren();
  select.add(new Option('选择一个模板', ''));
  for (const item of workflowTemplatesRead()) select.add(new Option(item.name, item.id));
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
}

function workflowTemplateSave() {
  const name = $('workflowTemplateName').value.trim().slice(0, 40);
  if (!name) { $('workflowTemplateMessage').textContent = '请先填写模板名称。'; return; }
  let templates = workflowTemplatesRead();
  const existing = templates.find(v => v.name === name);
  if (!existing && templates.length >= WORKFLOW_TEMPLATE_LIMIT) {
    $('workflowTemplateMessage').textContent = '最多保存15个模板，请先删除旧模板。'; return;
  }
  const id = existing ? existing.id : String(Date.now()) + '-' + Math.random().toString(36).slice(2, 9);
  const item = {id, name, version: 1, options: workflowCurrentOptions()};
  templates = existing ? templates.map(v => v.id === id ? item : v) : [...templates, item];
  if (!workflowTemplatesWrite(templates)) return;
  workflowTemplateList();
  $('workflowTemplateSelect').value = id;
  $('workflowTemplateMessage').textContent = existing ? '已更新同名模板。' : '模板已保存在此浏览器。';
}

function workflowTemplateLoad() {
  const item = workflowTemplatesRead().find(v => v.id === $('workflowTemplateSelect').value);
  if (!item) { $('workflowTemplateMessage').textContent = '请先选择已保存的模板。'; return; }
  for (const id of WORKFLOW_FIELD_IDS) {
    if (typeof item.options[id] === 'string') {
      const input = $(id);
      if ([...input.options || []].some(opt => opt.value === item.options[id]) || input.type === 'number')
        input.value = item.options[id];
    }
  }
  $('workflowTemplateName').value = item.name;
  $('workflowTemplateMessage').textContent = '模板已加载。文件列表和已确认的删除页没有改变。';
}

function workflowTemplateDelete() {
  const id = $('workflowTemplateSelect').value;
  if (!id) { $('workflowTemplateMessage').textContent = '请先选择模板。'; return; }
  if (!window.confirm('确定删除这个本地模板吗？')) return;
  if (!workflowTemplatesWrite(workflowTemplatesRead().filter(v => v.id !== id))) return;
  workflowTemplateList();
  $('workflowTemplateSelect').value = '';
  $('workflowTemplateMessage').textContent = '已删除模板。';
}

window.resetWorkflowScan = function resetWorkflowScan() {
  workflowScanEpoch += 1;
  workflowCandidates = [];
  state.blankApproved = null;
  const result = $('workflowBlankResults');
  if (result) { result.replaceChildren(); result.hidden = true; }
  const confirmation = $('workflowConfirmDeletion');
  if (confirmation) confirmation.hidden = true;
  const status = $('workflowScanStatus');
  if (status) status.textContent = '';
};

function workflowBlankCandidateRow(candidate) {
  const label = document.createElement('label');
  label.className = 'workflow-blank-item';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'workflow-blank-choice';
  checkbox.dataset.fileIndex = String(candidate.file_index);
  checkbox.dataset.page = String(candidate.page);
  checkbox.setAttribute('aria-label', candidate.file_name + ' 第' + candidate.page + '页');
  const img = document.createElement('img');
  img.className = 'workflow-blank-preview';
  img.alt = '疑似空白页缩略图（需人工检查）';
  if (typeof candidate.image === 'string' && candidate.image.startsWith('data:image/jpeg;base64,'))
    img.src = candidate.image;
  const info = document.createElement('span');
  info.textContent = candidate.file_name + ' · 第 ' + candidate.page + ' 页';
  label.append(checkbox, img, info);
  return label;
}

async function workflowScanBlanks() {
  if (state.busy || !state.files.length) {
    $('workflowScanStatus').textContent = '请先添加 PDF 文件，且当前不能有执行中的任务。';
    return;
  }
  window.resetWorkflowScan();
  const epoch = workflowScanEpoch;
  const files = [...state.files];
  const button = $('workflowScanBtn');
  button.disabled = true;
  $('workflowScanStatus').textContent = '正在扫描疑似空白页，请勿修改文件列表……';
  try {
    const fd = new FormData();
    files.forEach(file => fd.append('files', file));
    const res = await fetch('/api/pdf/workflow/blank-scan', {method: 'POST', body: fd});
    const payload = await res.json();
    if (epoch !== workflowScanEpoch) return;
    if (!res.ok) throw Error(payload.detail || '检测失败');
    workflowCandidates = payload.candidates || [];
    const container = $('workflowBlankResults');
    container.replaceChildren();
    for (const item of workflowCandidates) container.appendChild(workflowBlankCandidateRow(item));
    container.hidden = !workflowCandidates.length;
    $('workflowConfirmDeletion').hidden = !workflowCandidates.length;
    $('workflowScanStatus').textContent = workflowCandidates.length
      ? '扫描 ' + payload.scanned_pages + ' 页，找到 ' + workflowCandidates.length + ' 页疑似空白页。请逐页查看并勾选，系统不会自动删除。' + (payload.truncated ? ' 候选页超过显示上限，请分批处理。' : '')
      : '扫描完成，没有发现符合保守检测规则的疑似空白页。';
  } catch (e) {
    if (epoch === workflowScanEpoch) $('workflowScanStatus').textContent = e.message || '扫描失败';
  } finally {
    button.disabled = false;
  }
}

function workflowConfirmBlankDeletions() {
  const selected = [...document.querySelectorAll('.workflow-blank-choice:checked')];
  if (!selected.length) {
    $('workflowScanStatus').textContent = '未勾选任何页面，不会删除。';
    state.blankApproved = null;
    return;
  }
  const message = '即将从原始文件中移除 ' + selected.length +
    ' 页。请确认所有缩略图均为空白；此操作不会修改原文件，仅影响本次生成的结果。确定继续吗？';
  if (!window.confirm(message)) { state.blankApproved = null; return; }
  const deletePages = {};
  for (const input of selected) {
    const i = input.dataset.fileIndex, page = Number(input.dataset.page);
    if (!deletePages[i]) deletePages[i] = [];
    deletePages[i].push(page);
  }
  state.blankApproved = deletePages;
  $('workflowScanStatus').textContent = '已确认删除 ' + selected.length +
    ' 页。点击“开始处理”执行；修改文件列表会自动清除本次删除选择。';
}

document.addEventListener('DOMContentLoaded', () => {
  workflowTemplateList();
  $('workflowTemplateSave').addEventListener('click', workflowTemplateSave);
  $('workflowTemplateLoad').addEventListener('click', workflowTemplateLoad);
  $('workflowTemplateDelete').addEventListener('click', workflowTemplateDelete);
  $('workflowScanBtn').addEventListener('click', workflowScanBlanks);
  $('workflowScanClear').addEventListener('click', window.resetWorkflowScan);
  $('workflowConfirmDeletion').addEventListener('click', workflowConfirmBlankDeletions);
  $('workflowBlankResults').addEventListener('change', () => {
    if (state.blankApproved) {
      state.blankApproved = null;
      $('workflowScanStatus').textContent = '勾选发生变化，之前的确认已失效。请重新点击“确认删除勾选的页面”。';
    }
  });
});
