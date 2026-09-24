const tools = [
  { id: 'merge', label: 'PDF 合并', desc: '上传多个 PDF，按列表顺序合并为一个文件。', endpoint: '/api/pdf/merge', accept: '.pdf,application/pdf', multiple: true, outputName: 'merged.pdf', tips: ['至少上传 2 个 PDF。', '合并顺序与文件列表顺序一致。', '可用上移、下移按钮调整顺序。'] },
  { id: 'extract', label: '页面提取', desc: '从单个 PDF 中提取指定页码范围。', endpoint: '/api/pdf/extract', accept: '.pdf,application/pdf', multiple: false, outputName: 'extracted.pdf', tips: ['可直接点击页面缩略图选择。', '页码从 1 开始。', '页码范围留空表示提取全部页面。'] },
  { id: 'delete-pages', label: '删除页面', desc: '删除指定页面后生成新的 PDF。', endpoint: '/api/pdf/delete-pages', accept: '.pdf,application/pdf', multiple: false, outputName: 'deleted_pages.pdf', tips: ['可直接点击缩略图选择要删除的页面。', '不能删除全部页面。', '也可手工输入：2, 4-6。'] },
  { id: 'split', label: '按页拆分', desc: '把 PDF 按每页拆分，并以 ZIP 打包下载。', endpoint: '/api/pdf/split', accept: '.pdf,application/pdf', multiple: false, outputName: 'split_pages.zip', tips: ['上传一个 PDF。', '每一页会生成一个单独 PDF。', '最终结果会打包为 ZIP。'] },
  { id: 'rotate', label: '页面旋转', desc: '按全部、奇数页、偶数页或指定页码旋转 PDF。', endpoint: '/api/pdf/rotate', accept: '.pdf,application/pdf', multiple: false, outputName: 'rotated.pdf', tips: ['点击缩略图会自动切换为“指定页码”。', '角度支持 90°、180°、270°。', '奇数/偶数按文档页码判断。'] },
  { id: 'to-images', label: 'PDF 转图片', desc: '将 PDF 页面转换为 PNG 或 JPG，并以 ZIP 下载。', endpoint: '/api/pdf/to-images', accept: '.pdf,application/pdf', multiple: false, outputName: 'pdf_images.zip', tips: ['可选择指定页面。', 'DPI 越高越清晰，但耗时和输出体积增长明显。', '网站会限制过高 DPI 以保护稳定性。'] },
  { id: 'images-to-pdf', label: '图片转 PDF', desc: '上传多张图片，按顺序合成为一个 PDF。', endpoint: '/api/pdf/images-to-pdf', accept: '.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,image/*', multiple: true, outputName: 'images.pdf', tips: ['支持 PNG、JPG、BMP、TIFF、WEBP。', '生成顺序与文件列表一致。', '可调整图片顺序。'] },
  { id: 'watermark', label: '文字水印', desc: '给 PDF 添加文字水印，可设置位置、字号和页码范围。', endpoint: '/api/pdf/watermark', accept: '.pdf,application/pdf', multiple: false, outputName: 'watermarked.pdf', tips: ['中文水印已使用 CJK 字体处理。', '可点击缩略图指定页面。', '页码范围留空表示全部页面。'] },
  { id: 'page-number', label: '添加页码', desc: '在页面底部居中添加页码。', endpoint: '/api/pdf/page-number', accept: '.pdf,application/pdf', multiple: false, outputName: 'page_numbered.pdf', tips: ['中文“第…页”格式可正常写入。', '可自定义前缀、后缀和起始页码。', '可只对指定页面添加。'] },
  { id: 'encrypt', label: 'PDF 加密', desc: '设置 PDF 打开密码和权限密码。', endpoint: '/api/pdf/encrypt', accept: '.pdf,application/pdf', multiple: false, outputName: 'encrypted.pdf', tips: ['打开密码不能为空。', '权限密码可选。', '已加密 PDF 请先解密。'] },
  { id: 'decrypt', label: 'PDF 解密', desc: '已知密码时解除 PDF 加密。', endpoint: '/api/pdf/decrypt', accept: '.pdf,application/pdf', multiple: false, outputName: 'decrypted.pdf', tips: ['需要正确的原 PDF 密码。', '此功能不破解密码。', '仅处理你有权限使用的文件。'] },
  { id: 'info', label: '信息查看', desc: '查看文件大小、页数、是否加密、页面尺寸和元数据。', endpoint: '/api/pdf/info', accept: '.pdf,application/pdf', multiple: false, tips: ['上传一个 PDF 后开始读取。', '加密 PDF 可输入密码。', '页面尺寸最多展示前 10 页。'] }
];

const state = {
  activeId: 'merge',
  files: [],
  busy: false,
  config: null,
  jobId: null,
  pollTimer: null,
  previewAbort: null,
  selectedPages: new Set(),
  lastDownloadUrl: null,
  lastFilename: null
};

const $ = id => document.getElementById(id);
const panels = ['pageRangePanel', 'rotatePanel', 'imagePanel', 'watermarkPanel', 'pageNumberPanel', 'passwordPanel'];
const pageRangeTools = new Set(['extract', 'delete-pages', 'rotate', 'to-images', 'watermark', 'page-number']);

function currentTool() { return tools.find(t => t.id === state.activeId) || tools[0]; }
function isPdf(file) { return file && (file.name || '').toLowerCase().endsWith('.pdf'); }
function formatSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB']; let value = bytes;
  for (const unit of units) { if (value < 1024 || unit === 'GB') return unit === 'B' ? `${value} B` : `${value.toFixed(1)} ${unit}`; value /= 1024; }
}

function setStatus(text, isError = false) {
  $('statusText').textContent = text;
  $('statusText').style.color = isError ? 'var(--danger)' : 'var(--muted)';
}

function escapeHtml(text) {
  return String(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
}

function renderNav() {
  const groups = [
    { label: '常用工具', ids: ['merge', 'split', 'extract', 'delete-pages'] },
    { label: '页面处理', ids: ['rotate', 'watermark', 'page-number', 'info'] },
    { label: '格式转换', ids: ['to-images', 'images-to-pdf'] },
    { label: '安全工具', ids: ['encrypt', 'decrypt'] }
  ];

  const nav = $('toolNav');
  nav.innerHTML = '';
  for (const group of groups) {
    const title = document.createElement('div');
    title.className = 'nav-group-title';
    title.textContent = group.label;
    nav.appendChild(title);

    for (const id of group.ids) {
      const tool = tools.find(t => t.id === id);
      if (!tool) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `nav-btn ${tool.id === state.activeId ? 'active' : ''}`;
      btn.innerHTML = `<span class="nav-dot"></span><span class="nav-label">${tool.label}</span>`;
      btn.addEventListener('click', () => switchTool(tool.id));
      nav.appendChild(btn);
    }
  }
}

function fileCompatibleWithTool(file, tool) {
  if (tool.id === 'images-to-pdf') return !isPdf(file);
  return isPdf(file);
}

function switchTool(id) {
  if (state.busy) return;
  const next = tools.find(t => t.id === id) || tools[0];
  state.activeId = next.id;
  state.files = state.files.filter(f => fileCompatibleWithTool(f, next));
  if (!next.multiple && state.files.length > 1) state.files = state.files.slice(0, 1);
  $('fileInput').value = '';
  $('password').value = ''; $('ownerPassword').value = '';
  $('resultCard').classList.add('hidden');
  $('completedCard').classList.add('hidden');
  clearPageSelection(false);
  renderAll();
  if (state.files.length === 1 && isPdf(state.files[0])) loadPreview(state.files[0]);
  setStatus(state.files.length ? '已保留兼容文件，可继续处理。' : '等待上传文件。');
  closeMobileNav();
}

function renderAll() {
  const tool = currentTool();
  $('toolTitle').textContent = tool.label; $('toolDesc').textContent = tool.desc; if ($('breadcrumbTool')) $('breadcrumbTool').textContent = tool.label;
  $('fileInput').accept = tool.accept; $('fileInput').multiple = tool.multiple;
  $('acceptText').textContent = tool.id === 'images-to-pdf' ? '支持 PNG、JPG、BMP、TIFF、WEBP 图片' : '支持 PDF 文件';
  panels.forEach(id => $(id).classList.add('hidden'));
  if (pageRangeTools.has(tool.id)) $('pageRangePanel').classList.remove('hidden');
  if (tool.id === 'rotate') $('rotatePanel').classList.remove('hidden');
  if (tool.id === 'to-images') $('imagePanel').classList.remove('hidden');
  if (tool.id === 'watermark') $('watermarkPanel').classList.remove('hidden');
  if (tool.id === 'page-number') $('pageNumberPanel').classList.remove('hidden');
  if (['encrypt','decrypt','info'].includes(tool.id)) {
    $('passwordPanel').classList.remove('hidden');
    $('passwordLabel').textContent = tool.id === 'encrypt' ? '打开密码' : tool.id === 'decrypt' ? '原 PDF 密码' : 'PDF 密码，可选';
    $('ownerPasswordField').classList.toggle('hidden', tool.id !== 'encrypt');
  }
  renderNav(); renderFiles(); renderTips(); if (!state.busy) $('runBtn').textContent = actionLabel();
}

function renderTips() {
  $('tipsList').innerHTML = '';
  for (const item of currentTool().tips) { const li = document.createElement('li'); li.textContent = item; $('tipsList').appendChild(li); }
}

function renderFiles() {
  const list = $('fileList');
  if (!state.files.length) { list.className = 'file-list empty'; list.textContent = '尚未选择文件。'; return; }
  list.className = 'file-list'; list.innerHTML = '';
  state.files.forEach((file, index) => {
    const row = document.createElement('div'); row.className = 'file-row';
    row.innerHTML = `
      <div class="file-order">${index + 1}</div>
      <div><div class="file-name">${escapeHtml(file.name)}</div><div class="file-meta">${formatSize(file.size)} · ${escapeHtml(file.type || '未知类型')}</div></div>
      <div class="file-actions">
        <button class="icon-btn" data-action="up" data-index="${index}" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button class="icon-btn" data-action="down" data-index="${index}" ${index === state.files.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="icon-btn" data-action="remove" data-index="${index}">删</button>
      </div>`;
    list.appendChild(row);
  });
}

function validateFiles(files) {
  const cfg = state.config;
  if (!cfg) return;
  if (files.length > cfg.max_files_per_request) throw new Error(`单次最多选择 ${cfg.max_files_per_request} 个文件。`);
  let total = 0;
  for (const file of files) {
    if (file.size > cfg.max_file_mb * 1024 * 1024) throw new Error(`${file.name} 超过单文件 ${cfg.max_file_mb} MB 限制。`);
    total += file.size;
  }
  if (total > cfg.max_total_mb * 1024 * 1024) throw new Error(`文件总大小超过 ${cfg.max_total_mb} MB 限制。`);
}

function setFiles(fileList) {
  try {
    const tool = currentTool();
    const files = Array.from(fileList || []);
    validateFiles(files);
    const bad = files.find(f => !fileCompatibleWithTool(f, tool));
    if (bad) throw new Error(`${bad.name} 不是当前工具支持的文件类型。`);
    state.files = tool.multiple ? files : files.slice(0, 1);
    renderFiles(); $('resultCard').classList.add('hidden'); $('completedCard').classList.add('hidden');
    clearPageSelection(false);
    setStatus(state.files.length ? `已选择 ${state.files.length} 个文件。` : '等待上传文件。');
    if (state.files.length === 1 && isPdf(state.files[0])) loadPreview(state.files[0]); else hidePreview();
  } catch (err) {
    setStatus(err.message || String(err), true);
  }
}

function moveFile(index, direction) {
  const target = index + direction; if (target < 0 || target >= state.files.length) return;
  [state.files[index], state.files[target]] = [state.files[target], state.files[index]]; renderFiles();
}
function removeFile(index) {
  state.files.splice(index,1); renderFiles(); clearPageSelection(false);
  if (state.files.length === 1 && isPdf(state.files[0])) loadPreview(state.files[0]); else hidePreview();
}

function compressPages(nums) {
  const arr = [...nums].sort((a,b)=>a-b); if (!arr.length) return '';
  const chunks=[]; let start=arr[0], prev=arr[0];
  for (let i=1;i<=arr.length;i++) {
    const cur=arr[i];
    if (cur === prev + 1) { prev=cur; continue; }
    chunks.push(start === prev ? String(start) : `${start}-${prev}`);
    start=cur; prev=cur;
  }
  return chunks.join(', ');
}

function clearPageSelection(update = true) {
  state.selectedPages.clear();
  document.querySelectorAll('.preview-page.selected').forEach(el => el.classList.remove('selected'));
  if (update && $('rangeText')) $('rangeText').value = '';
}

function applySelection() {
  if (!pageRangeTools.has(state.activeId)) return;
  $('rangeText').value = compressPages(state.selectedPages);
  if (state.activeId === 'rotate' && state.selectedPages.size) $('rotateMode').value = 'range';
}

function hidePreview() { $('previewCard').classList.add('hidden'); $('previewGrid').innerHTML=''; }
async function loadPreview(file) {
  if (state.previewAbort) state.previewAbort.abort();
  const controller = new AbortController(); state.previewAbort = controller;
  $('previewCard').classList.remove('hidden'); $('previewGrid').innerHTML = '<div class="preview-loading">正在生成页面缩略图...</div>';
  $('previewSummary').textContent = '正在读取页面...';
  const fd = new FormData(); fd.append('file', file);
  try {
    const res = await fetch('/api/pdf/preview', {method:'POST', body:fd, signal:controller.signal});
    if (!res.ok) throw new Error(await parseError(res));
    const data = await res.json();
    $('previewGrid').innerHTML = '';
    $('previewSummary').textContent = `共 ${data.page_count} 页 · 已预览 ${data.previewed} 页${data.previewed < data.page_count ? '（为保证性能，仅显示前部分页面）' : ''}`;
    for (const item of data.pages) {
      const btn=document.createElement('button'); btn.type='button'; btn.className='preview-page'; btn.dataset.page=item.page;
      btn.innerHTML=`<img src="${item.image}" alt="第 ${item.page} 页缩略图"><span>第 ${item.page} 页</span>`;
      btn.addEventListener('click', ()=>{
        if (!pageRangeTools.has(state.activeId)) return;
        const n=Number(btn.dataset.page);
        if (state.selectedPages.has(n)) { state.selectedPages.delete(n); btn.classList.remove('selected'); }
        else { state.selectedPages.add(n); btn.classList.add('selected'); }
        applySelection();
      });
      $('previewGrid').appendChild(btn);
    }
  } catch (err) {
    if (err.name === 'AbortError') return;
    $('previewGrid').innerHTML = `<div class="preview-error">${escapeHtml(err.message || String(err))}</div>`;
    $('previewSummary').textContent = '预览不可用';
  }
}

function validateBeforeRun() {
  const tool=currentTool();
  if (!state.files.length) throw new Error('请先上传文件。');
  validateFiles(state.files);
  if (tool.id === 'merge' && state.files.length < 2) throw new Error('PDF 合并至少需要 2 个 PDF。');
  if (tool.id === 'delete-pages' && !$('rangeText').value.trim()) throw new Error('删除页面必须输入页码范围。');
  if (tool.id === 'rotate' && $('rotateMode').value === 'range' && !$('rangeText').value.trim()) throw new Error('指定页码模式下请先选择或输入页码。');
  if (tool.id === 'watermark' && !$('watermarkText').value.trim()) throw new Error('水印文字不能为空。');
  if (['encrypt','decrypt'].includes(tool.id) && !$('password').value.trim()) throw new Error('请输入密码。');
  if (tool.id === 'to-images' && state.config) {
    const dpi=Number($('dpi').value);
    if (dpi < 72 || dpi > state.config.max_dpi) throw new Error(`DPI 必须在 72 到 ${state.config.max_dpi} 之间。`);
  }
}

function buildFormData() {
  const tool=currentTool(), fd=new FormData();
  if (tool.multiple) state.files.forEach(file=>fd.append('files',file)); else fd.append('file',state.files[0]);
  if (pageRangeTools.has(tool.id)) fd.append('range_text',$('rangeText').value.trim());
  if (tool.id==='rotate') { fd.append('degrees',$('degrees').value); fd.append('mode',$('rotateMode').value); }
  if (tool.id==='to-images') { fd.append('dpi',$('dpi').value); fd.append('image_format',$('imageFormat').value); }
  if (tool.id==='watermark') { fd.append('text',$('watermarkText').value); fd.append('font_size',$('fontSize').value); fd.append('position',$('watermarkPosition').value); }
  if (tool.id==='page-number') { fd.append('prefix',$('prefix').value); fd.append('suffix',$('suffix').value); fd.append('start_number',$('startNumber').value); fd.append('font_size',$('pageFontSize').value); }
  if (tool.id==='encrypt') { fd.append('user_password',$('password').value); fd.append('owner_password',$('ownerPassword').value); }
  if (tool.id==='decrypt' || tool.id==='info') fd.append('password',$('password').value);
  return fd;
}

async function parseError(response) {
  let message=`请求失败：${response.status}`;
  try { const data=await response.json(); if (data?.detail) message=Array.isArray(data.detail)?JSON.stringify(data.detail):data.detail; }
  catch { try { const text=await response.text(); if(text) message=text; } catch {} }
  return message;
}

function submitWithUploadProgress(url, fd) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest(); xhr.open('POST',url); xhr.responseType='json';
    xhr.upload.onprogress=e=>{
      if (!e.lengthComputable) return;
      const pct=Math.max(1,Math.min(18,Math.round(e.loaded/e.total*18)));
      updateProgress(pct,`正在上传 ${Math.round(e.loaded/e.total*100)}%`);
    };
    xhr.onload=()=>{
      const data=xhr.response || {};
      if (xhr.status>=200 && xhr.status<300) resolve(data);
      else reject(new Error(data.detail || `请求失败：${xhr.status}`));
    };
    xhr.onerror=()=>reject(new Error('网络连接失败，请检查网络后重试。'));
    xhr.send(fd);
  });
}

function updateProgress(value,label) {
  $('progressPanel').classList.remove('hidden'); $('progressValue').textContent=`${value}%`; $('progressBar').style.width=`${value}%`;
  $('progressLabel').textContent=label || '处理中';
}

function resetJobUi() {
  if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer=null; state.jobId=null;
  $('progressPanel').classList.add('hidden'); $('cancelBtn').classList.add('hidden');
  $('runBtn').disabled=false; $('runBtn').textContent=actionLabel(); state.busy=false;
}

async function pollJob(jobId) {
  try {
    const res=await fetch(`/api/jobs/${jobId}`,{cache:'no-store'});
    if (!res.ok) throw new Error(await parseError(res));
    const data=await res.json();
    updateProgress(Math.max(20,data.progress||0),data.message||'处理中');
    if (data.status==='done') {
      if (data.result) {
        renderInfo(data.result); setStatus('信息读取完成。');
      } else {
        state.lastDownloadUrl=data.download_url; state.lastFilename=data.filename || currentTool().outputName || 'result.pdf';
        $('downloadBtn').href=state.lastDownloadUrl; $('downloadBtn').download=state.lastFilename;
        $('completedText').textContent=`${state.lastFilename} 已准备好，可直接下载或继续处理。`;
        $('continueBtn').classList.toggle('hidden', !state.lastFilename.toLowerCase().endsWith('.pdf'));
        $('completedCard').classList.remove('hidden');
        setStatus('处理完成，结果已准备好。');
      }
      updateProgress(100,'处理完成'); setTimeout(()=>resetJobUi(),500); return;
    }
    if (data.status==='failed') throw new Error(data.error || data.message || '处理失败');
    if (data.status==='cancelled') { setStatus('任务已取消。'); resetJobUi(); return; }
    state.pollTimer=setTimeout(()=>pollJob(jobId),650);
  } catch(err) { setStatus(err.message||String(err),true); resetJobUi(); }
}

function actionLabel() {
  const labels = {
    merge: '合并 PDF',
    extract: '提取页面',
    'delete-pages': '删除页面',
    split: '拆分 PDF',
    rotate: '旋转 PDF',
    'to-images': '转换为图片',
    'images-to-pdf': '生成 PDF',
    watermark: '添加水印',
    'page-number': '添加页码',
    encrypt: '加密 PDF',
    decrypt: '解密 PDF',
    info: '读取信息'
  };
  return labels[state.activeId] || '开始处理';
}

async function runTool() {
  try { validateBeforeRun(); } catch(err) { setStatus(err.message,true); return; }
  state.busy=true; $('runBtn').disabled=true; $('runBtn').textContent='提交中...'; $('cancelBtn').classList.remove('hidden');
  $('completedCard').classList.add('hidden'); $('resultCard').classList.add('hidden'); updateProgress(1,'准备上传');
  try {
    const data=await submitWithUploadProgress(currentTool().endpoint,buildFormData());
    if (!data.job_id) throw new Error('服务器未返回任务编号。');
    state.jobId=data.job_id; $('runBtn').textContent='处理中...'; updateProgress(20,'文件已上传，等待处理'); pollJob(state.jobId);
  } catch(err) { setStatus(err.message||String(err),true); resetJobUi(); }
}

async function cancelCurrentJob() {
  if (!state.jobId) { resetJobUi(); return; }
  try { await fetch(`/api/jobs/${state.jobId}/cancel`,{method:'POST'}); } catch {}
  setStatus('已发送取消请求。'); resetJobUi();
}

async function continueEditingResult() {
  if (!state.lastDownloadUrl || !state.lastFilename) return;
  $('continueBtn').disabled=true; setStatus('正在把结果加入当前工作区...');
  try {
    const res=await fetch(state.lastDownloadUrl);
    if (!res.ok) throw new Error(await parseError(res));
    const blob=await res.blob();
    const file=new File([blob],state.lastFilename,{type:'application/pdf'});
    state.files=[file]; renderFiles(); $('completedCard').classList.add('hidden'); clearPageSelection(false); await loadPreview(file);
    setStatus('结果已加入工作区。现在可直接选择另一个 PDF 工具继续处理。');
    state.lastDownloadUrl=null; state.lastFilename=null;
  } catch(err) { setStatus(err.message||String(err),true); }
  finally { $('continueBtn').disabled=false; }
}

function renderInfo(info) {
  const entries=[['文件名',info.filename],['文件大小',info.size],['页数',info.page_count===-1?'加密，未读取':info.page_count],['是否加密',info.encrypted?'是':'否'],['标题',info.title||'无'],['作者',info.author||'无'],['创建工具',info.creator||'无'],['生产者',info.producer||'无'],['创建时间',info.created_at||'无'],['修改时间',info.modified_at||'无'],['页面尺寸',(info.page_sizes?.length)?info.page_sizes.join('\n'):'无']];
  $('infoResult').innerHTML=entries.map(([k,v])=>`<div class="info-row"><div class="info-key">${escapeHtml(k)}</div><div class="info-value">${escapeHtml(String(v)).replaceAll('\n','<br>')}</div></div>`).join('');
  $('resultCard').classList.remove('hidden');
}

async function checkHealth() {
  try { const r=await fetch('/api/health',{cache:'no-store'}); if(!r.ok) throw new Error(); $('healthDot').className='dot ok'; $('healthText').textContent='服务正常'; }
  catch { $('healthDot').className='dot bad'; $('healthText').textContent='服务暂不可用'; }
}

async function loadConfig() {
  try {
    const r=await fetch('/api/config',{cache:'no-store'}); if(!r.ok) throw new Error(); state.config=await r.json();
    $('dpi').max=state.config.max_dpi;
    $('limitText').textContent=`单文件 ${state.config.max_file_mb} MB · 单次总计 ${state.config.max_total_mb} MB · 最多 ${state.config.max_files_per_request} 个文件 · PDF 最多 ${state.config.max_pages_per_pdf} 页`;
  } catch { $('limitText').textContent='文件仅用于当前处理任务'; }
}

function openMobileNav(){ $('sidebar').classList.add('mobile-open'); $('navBackdrop').classList.remove('hidden'); }
function closeMobileNav(){ $('sidebar').classList.remove('mobile-open'); $('navBackdrop').classList.add('hidden'); }

function bindEvents() {
  $('dropZone').addEventListener('click',()=>!state.busy && $('fileInput').click());
  $('dropZone').addEventListener('keydown',e=>{ if(!state.busy && (e.key==='Enter'||e.key===' ')) $('fileInput').click(); });
  $('fileInput').addEventListener('change',e=>setFiles(e.target.files));
  $('clearFilesBtn').addEventListener('click',()=>{ if(state.busy)return; state.files=[]; $('fileInput').value=''; renderFiles(); hidePreview(); clearPageSelection(); setStatus('已清空文件列表。'); });
  $('clearSelectionBtn').addEventListener('click',()=>clearPageSelection(true));
  $('fileList').addEventListener('click',e=>{ const b=e.target.closest('button[data-action]'); if(!b||state.busy)return; const i=Number(b.dataset.index); if(b.dataset.action==='up')moveFile(i,-1); if(b.dataset.action==='down')moveFile(i,1); if(b.dataset.action==='remove')removeFile(i); });
  $('runBtn').addEventListener('click',runTool); $('cancelBtn').addEventListener('click',cancelCurrentJob); $('continueBtn').addEventListener('click',continueEditingResult);
  $('mobileNavOpen').addEventListener('click',openMobileNav); $('mobileNavClose').addEventListener('click',closeMobileNav); $('navBackdrop').addEventListener('click',closeMobileNav);
  const dz=$('dropZone');
  ['dragenter','dragover'].forEach(n=>dz.addEventListener(n,e=>{e.preventDefault(); if(!state.busy)dz.classList.add('dragging');}));
  ['dragleave','drop'].forEach(n=>dz.addEventListener(n,e=>{e.preventDefault(); dz.classList.remove('dragging');}));
  dz.addEventListener('drop',e=>{ if(!state.busy)setFiles(e.dataTransfer.files); });
}

document.addEventListener('DOMContentLoaded',async()=>{ bindEvents(); renderAll(); await loadConfig(); checkHealth(); });
