const tools = [
  { id: 'merge', label: 'PDF 合并', desc: '上传多个 PDF，按列表顺序合并为一个文件。', endpoint: '/api/pdf/merge', accept: '.pdf,application/pdf', multiple: true, outputName: 'merged.pdf', tips: ['至少上传 2 个 PDF。', '合并顺序与文件列表顺序一致。', '可以使用上移、下移按钮调整顺序。'] },
  { id: 'extract', label: '页面提取', desc: '从单个 PDF 中提取指定页码范围。', endpoint: '/api/pdf/extract', accept: '.pdf,application/pdf', multiple: false, outputName: 'extracted.pdf', tips: ['页码从 1 开始。', '示例：1-3, 5, 8-10。', '页码范围留空表示提取全部页面。'] },
  { id: 'delete-pages', label: '删除页面', desc: '删除指定页面后生成新的 PDF。', endpoint: '/api/pdf/delete-pages', accept: '.pdf,application/pdf', multiple: false, outputName: 'deleted_pages.pdf', tips: ['必须输入需要删除的页码。', '不能删除全部页面。', '示例：2, 4-6。'] },
  { id: 'split', label: '按页拆分', desc: '把 PDF 按每页拆分，并以 zip 打包下载。', endpoint: '/api/pdf/split', accept: '.pdf,application/pdf', multiple: false, outputName: 'split_pages.zip', tips: ['上传一个 PDF。', '每一页会生成一个单独 PDF。', '最终结果会打包为 zip 下载。'] },
  { id: 'rotate', label: '页面旋转', desc: '按全部、奇数页、偶数页或指定页码旋转 PDF。', endpoint: '/api/pdf/rotate', accept: '.pdf,application/pdf', multiple: false, outputName: 'rotated.pdf', tips: ['角度仅支持 90°、180°、270°。', '指定页码模式下需要填写页码范围。', '奇数页和偶数页按文档页码判断。'] },
  { id: 'to-images', label: 'PDF 转图片', desc: '将 PDF 页面转换为 PNG 或 JPG，并以 zip 下载。', endpoint: '/api/pdf/to-images', accept: '.pdf,application/pdf', multiple: false, outputName: 'pdf_images.zip', tips: ['DPI 越高，图片越清晰但文件越大。', '建议常规使用 200 或 300 DPI。', '页码范围留空表示转换全部页面。'] },
  { id: 'images-to-pdf', label: '图片转 PDF', desc: '上传多张图片，按顺序合成为一个 PDF。', endpoint: '/api/pdf/images-to-pdf', accept: '.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,image/*', multiple: true, outputName: 'images.pdf', tips: ['支持 PNG、JPG、BMP、TIFF、WEBP。', '生成顺序与文件列表顺序一致。', '可以使用上移、下移按钮调整顺序。'] },
  { id: 'watermark', label: '文字水印', desc: '给 PDF 添加文字水印，可设置位置、字号和页码范围。', endpoint: '/api/pdf/watermark', accept: '.pdf,application/pdf', multiple: false, outputName: 'watermarked.pdf', tips: ['水印文字不能为空。', '页码范围留空表示全部页面。', '当前版本为文字水印。'] },
  { id: 'page-number', label: '添加页码', desc: '在页面底部居中添加页码。', endpoint: '/api/pdf/page-number', accept: '.pdf,application/pdf', multiple: false, outputName: 'page_numbered.pdf', tips: ['可以自定义前缀、后缀和起始页码。', '页码范围留空表示全部页面。', '页码会添加在底部居中位置。'] },
  { id: 'encrypt', label: 'PDF 加密', desc: '设置 PDF 打开密码和权限密码。', endpoint: '/api/pdf/encrypt', accept: '.pdf,application/pdf', multiple: false, outputName: 'encrypted.pdf', tips: ['打开密码不能为空。', '权限密码可选。', '已加密 PDF 需要先解密后再重新加密。'] },
  { id: 'decrypt', label: 'PDF 解密', desc: '已知密码时解除 PDF 加密。', endpoint: '/api/pdf/decrypt', accept: '.pdf,application/pdf', multiple: false, outputName: 'decrypted.pdf', tips: ['需要输入正确的原 PDF 密码。', '此功能不是破解密码。', '仅用于你有权限处理的文件。'] },
  { id: 'info', label: '信息查看', desc: '查看文件大小、页数、是否加密、页面尺寸和元数据。', endpoint: '/api/pdf/info', accept: '.pdf,application/pdf', multiple: false, tips: ['上传一个 PDF 后点击开始处理。', '加密 PDF 可输入密码后查看页数。', '页面尺寸最多展示前 10 页。'] }
];

const state = {
  activeId: 'merge',
  files: [],
  busy: false
};

const $ = (id) => document.getElementById(id);
const panels = ['pageRangePanel', 'rotatePanel', 'imagePanel', 'watermarkPanel', 'pageNumberPanel', 'passwordPanel'];
const pageRangeTools = new Set(['extract', 'delete-pages', 'rotate', 'to-images', 'watermark', 'page-number']);

function currentTool() {
  return tools.find(t => t.id === state.activeId) || tools[0];
}

function formatSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  for (const unit of units) {
    if (value < 1024 || unit === units[units.length - 1]) {
      return unit === 'B' ? `${value} B` : `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${bytes} B`;
}

function setStatus(text, isError = false) {
  const status = $('statusText');
  status.textContent = text;
  status.style.color = isError ? 'var(--danger)' : 'var(--muted)';
}

function renderNav() {
  const nav = $('toolNav');
  nav.innerHTML = '';
  for (const tool of tools) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `nav-btn ${tool.id === state.activeId ? 'active' : ''}`;
    btn.innerHTML = `${tool.label}<span>${tool.desc}</span>`;
    btn.addEventListener('click', () => switchTool(tool.id));
    nav.appendChild(btn);
  }
}

function switchTool(id) {
  state.activeId = id;
  state.files = [];
  $('fileInput').value = '';
  $('rangeText').value = '';
  $('password').value = '';
  $('ownerPassword').value = '';
  $('resultCard').classList.add('hidden');
  renderAll();
  setStatus('等待上传文件。');
}

function renderAll() {
  const tool = currentTool();
  $('toolTitle').textContent = tool.label;
  $('toolDesc').textContent = tool.desc;
  $('fileInput').accept = tool.accept;
  $('fileInput').multiple = tool.multiple;
  $('acceptText').textContent = tool.id === 'images-to-pdf' ? '支持 PNG、JPG、BMP、TIFF、WEBP 图片' : '支持 PDF 文件';

  panels.forEach(id => $(id).classList.add('hidden'));
  if (pageRangeTools.has(tool.id)) $('pageRangePanel').classList.remove('hidden');
  if (tool.id === 'rotate') $('rotatePanel').classList.remove('hidden');
  if (tool.id === 'to-images') $('imagePanel').classList.remove('hidden');
  if (tool.id === 'watermark') $('watermarkPanel').classList.remove('hidden');
  if (tool.id === 'page-number') $('pageNumberPanel').classList.remove('hidden');
  if (['encrypt', 'decrypt', 'info'].includes(tool.id)) {
    $('passwordPanel').classList.remove('hidden');
    $('passwordLabel').textContent = tool.id === 'encrypt' ? '打开密码' : tool.id === 'decrypt' ? '原 PDF 密码' : 'PDF 密码，可选';
    $('ownerPasswordField').classList.toggle('hidden', tool.id !== 'encrypt');
  }

  renderNav();
  renderFiles();
  renderTips();
}

function renderTips() {
  const tips = $('tipsList');
  tips.innerHTML = '';
  for (const item of currentTool().tips) {
    const li = document.createElement('li');
    li.textContent = item;
    tips.appendChild(li);
  }
}

function renderFiles() {
  const list = $('fileList');
  if (!state.files.length) {
    list.className = 'file-list empty';
    list.textContent = '尚未选择文件。';
    return;
  }
  list.className = 'file-list';
  list.innerHTML = '';
  state.files.forEach((file, index) => {
    const row = document.createElement('div');
    row.className = 'file-row';
    const disableUp = index === 0 ? 'disabled' : '';
    const disableDown = index === state.files.length - 1 ? 'disabled' : '';
    row.innerHTML = `
      <div class="file-order">${index + 1}</div>
      <div>
        <div class="file-name">${escapeHtml(file.name)}</div>
        <div class="file-meta">${formatSize(file.size)} · ${file.type || '未知类型'}</div>
      </div>
      <div class="file-actions">
        <button class="icon-btn" data-action="up" data-index="${index}" ${disableUp}>↑</button>
        <button class="icon-btn" data-action="down" data-index="${index}" ${disableDown}>↓</button>
        <button class="icon-btn" data-action="remove" data-index="${index}">删</button>
      </div>`;
    list.appendChild(row);
  });
}

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function setFiles(fileList) {
  const tool = currentTool();
  const files = Array.from(fileList || []);
  state.files = tool.multiple ? files : files.slice(0, 1);
  renderFiles();
  $('resultCard').classList.add('hidden');
  setStatus(state.files.length ? `已选择 ${state.files.length} 个文件。` : '等待上传文件。');
}

function moveFile(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= state.files.length) return;
  [state.files[index], state.files[target]] = [state.files[target], state.files[index]];
  renderFiles();
}

function removeFile(index) {
  state.files.splice(index, 1);
  renderFiles();
}

function validateBeforeRun() {
  const tool = currentTool();
  if (!state.files.length) throw new Error('请先上传文件。');
  if (tool.id === 'merge' && state.files.length < 2) throw new Error('PDF 合并至少需要 2 个 PDF。');
  if (tool.id === 'delete-pages' && !$('rangeText').value.trim()) throw new Error('删除页面必须输入页码范围。');
  if (tool.id === 'watermark' && !$('watermarkText').value.trim()) throw new Error('水印文字不能为空。');
  if (['encrypt', 'decrypt'].includes(tool.id) && !$('password').value.trim()) throw new Error('请输入密码。');
}

function buildFormData() {
  const tool = currentTool();
  const fd = new FormData();
  if (tool.multiple) {
    state.files.forEach(file => fd.append('files', file));
  } else {
    fd.append('file', state.files[0]);
  }
  if (pageRangeTools.has(tool.id)) fd.append('range_text', $('rangeText').value.trim());
  if (tool.id === 'rotate') {
    fd.append('degrees', $('degrees').value);
    fd.append('mode', $('rotateMode').value);
  }
  if (tool.id === 'to-images') {
    fd.append('dpi', $('dpi').value);
    fd.append('image_format', $('imageFormat').value);
  }
  if (tool.id === 'watermark') {
    fd.append('text', $('watermarkText').value);
    fd.append('font_size', $('fontSize').value);
    fd.append('position', $('watermarkPosition').value);
  }
  if (tool.id === 'page-number') {
    fd.append('prefix', $('prefix').value);
    fd.append('suffix', $('suffix').value);
    fd.append('start_number', $('startNumber').value);
    fd.append('font_size', $('pageFontSize').value);
  }
  if (tool.id === 'encrypt') {
    fd.append('user_password', $('password').value);
    fd.append('owner_password', $('ownerPassword').value);
  }
  if (tool.id === 'decrypt') {
    fd.append('password', $('password').value);
  }
  if (tool.id === 'info') {
    fd.append('password', $('password').value);
  }
  return fd;
}

function getDownloadName(response, fallback) {
  const disposition = response.headers.get('content-disposition') || '';
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8 && utf8[1]) return decodeURIComponent(utf8[1]);
  const ascii = disposition.match(/filename="?([^";]+)"?/i);
  if (ascii && ascii[1]) return ascii[1];
  return fallback;
}

async function parseError(response) {
  let message = `请求失败：${response.status}`;
  try {
    const data = await response.json();
    if (data && data.detail) message = Array.isArray(data.detail) ? JSON.stringify(data.detail) : data.detail;
  } catch {
    const text = await response.text();
    if (text) message = text;
  }
  return message;
}

async function downloadResponse(response, fallbackName) {
  if (!response.ok) throw new Error(await parseError(response));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = getDownloadName(response, fallbackName);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function runTool() {
  try {
    validateBeforeRun();
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  const tool = currentTool();
  state.busy = true;
  $('runBtn').disabled = true;
  $('runBtn').textContent = '处理中...';
  setStatus('正在上传并处理，请稍候。');
  $('resultCard').classList.add('hidden');

  try {
    const response = await fetch(tool.endpoint, { method: 'POST', body: buildFormData() });
    if (tool.id === 'info') {
      if (!response.ok) throw new Error(await parseError(response));
      const data = await response.json();
      renderInfo(data);
      setStatus('信息读取完成。');
    } else {
      await downloadResponse(response, tool.outputName || 'result.bin');
      setStatus('处理完成，结果已开始下载。');
    }
  } catch (err) {
    setStatus(err.message || String(err), true);
  } finally {
    state.busy = false;
    $('runBtn').disabled = false;
    $('runBtn').textContent = '开始处理';
  }
}

function renderInfo(info) {
  const entries = [
    ['文件名', info.filename],
    ['文件大小', info.size],
    ['页数', info.page_count === -1 ? '加密，未读取' : info.page_count],
    ['是否加密', info.encrypted ? '是' : '否'],
    ['标题', info.title || '无'],
    ['作者', info.author || '无'],
    ['创建工具', info.creator || '无'],
    ['生产者', info.producer || '无'],
    ['创建时间', info.created_at || '无'],
    ['修改时间', info.modified_at || '无'],
    ['页面尺寸', (info.page_sizes && info.page_sizes.length) ? info.page_sizes.join('\n') : '无']
  ];
  $('infoResult').innerHTML = entries.map(([k, v]) => `
    <div class="info-row">
      <div class="info-key">${escapeHtml(k)}</div>
      <div class="info-value">${escapeHtml(String(v)).replaceAll('\n', '<br>')}</div>
    </div>`).join('');
  $('resultCard').classList.remove('hidden');
}

async function checkHealth() {
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    if (!response.ok) throw new Error('bad');
    $('healthDot').className = 'dot ok';
    $('healthText').textContent = '服务正常';
  } catch {
    $('healthDot').className = 'dot bad';
    $('healthText').textContent = '服务暂不可用';
  }
}

async function loadConfig() {
  try {
    const response = await fetch('/api/config', { cache: 'no-store' });
    if (!response.ok) throw new Error('bad');
    const data = await response.json();
    $('limitText').textContent = `单文件上限 ${data.max_file_mb} MB · 单次最多 ${data.max_files_per_request} 个文件`;
  } catch {
    $('limitText').textContent = '文件仅用于当前处理任务';
  }
}

function bindEvents() {
  $('dropZone').addEventListener('click', () => $('fileInput').click());
  $('dropZone').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') $('fileInput').click();
  });
  $('fileInput').addEventListener('change', (event) => setFiles(event.target.files));
  $('clearFilesBtn').addEventListener('click', () => {
    state.files = [];
    $('fileInput').value = '';
    renderFiles();
    setStatus('已清空文件列表。');
  });
  $('fileList').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const index = Number(button.dataset.index);
    const action = button.dataset.action;
    if (action === 'up') moveFile(index, -1);
    if (action === 'down') moveFile(index, 1);
    if (action === 'remove') removeFile(index);
  });
  $('runBtn').addEventListener('click', runTool);

  const dropZone = $('dropZone');
  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add('dragging');
    });
  });
  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove('dragging');
    });
  });
  dropZone.addEventListener('drop', (event) => {
    const files = event.dataTransfer.files;
    setFiles(files);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  bindEvents();
  renderAll();
  checkHealth();
  loadConfig();
});
