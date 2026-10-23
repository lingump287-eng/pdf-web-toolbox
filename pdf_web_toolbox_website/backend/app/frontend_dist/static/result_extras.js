/* Completed-job result browsing and non-destructive retry UX (v5.4).
 * No uploaded files, PDFs, images, or document text are kept in browser storage.
 */
const resultUI = (() => {
  let renderVersion = 0;
  let failedMode = 'resubmit';
  let pendingJobId = null;

  function clear() {
    renderVersion += 1;
    $('recoveryCard').classList.add('hidden');
    $('resultPreviewCard').classList.add('hidden');
    $('resultPreviewItems').replaceChildren();
    $('resultPreviewImages').replaceChildren();
  }

  function hintForError(message) {
    if (/加密|密码/.test(message)) return '请先使用解密工具处理有权限访问的文件，再重新提交。';
    if (/页码|范围|无效|无法读取|损坏|格式/.test(message)) return '请检查上传的文件及页码配置；文件列表已保留，修改后可重新提交。';
    if (/大|超过|限制|内存|超时/.test(message)) return '可以减少文件数量、降低文件大小，或将任务拆成两批后重试。';
    if (/网络|连接|状态|请求/.test(message)) return '可先继续查询原任务状态，避免重复上传和处理。';
    return '当前文件与参数仍保留在工作区，可检查设置后重新提交；不会修改原始文件。';
  }

  function showRecovery(message, mode = 'resubmit', jobId = null) {
    failedMode = mode;
    pendingJobId = mode === 'recheck' ? jobId : null;
    $('recoveryMessage').textContent = message || '处理未完成。';
    $('recoveryHint').textContent = hintForError(message || '');
    $('recoveryActionBtn').textContent = mode === 'recheck' ? '继续查询原任务' : '重新上传并处理';
    $('recoveryCard').classList.remove('hidden');
  }

  function imagePreview(jobId, page, fileIndex, label) {
    const fig = document.createElement('figure');
    fig.className = 'result-preview-figure';
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = label;
    img.src = '/api/jobs/' + encodeURIComponent(jobId) + '/result-preview?page=' +
      encodeURIComponent(page) + '&file_index=' + encodeURIComponent(fileIndex);
    const cap = document.createElement('figcaption');
    cap.textContent = label;
    fig.append(img, cap);
    return fig;
  }

  function renderPdfSummary(jobId, summary) {
    const count = summary.page_count || 0;
    $('resultPreviewSummary').textContent = 'PDF 共 ' + count + ' 页 · ' +
      formatSize(summary.size_bytes) + '。以下为前 ' + Math.min(count, 6) + ' 页缩略图。';
    const grid = $('resultPreviewImages');
    for (let i = 1; i <= Math.min(count, 6); i++) {
      grid.appendChild(imagePreview(jobId, i, 0, '第 ' + i + ' 页'));
    }
    if (count > 6) {
      const helper = document.createElement('p');
      helper.className = 'workflow-help';
      helper.textContent = '预览最多展示前6页。完整内容请下载 PDF 核查。';
      $('resultPreviewItems').appendChild(helper);
    }
  }

  function renderZipSummary(jobId, summary) {
    const files = summary.files || [];
    const count = summary.total_files || files.length;
    $('resultPreviewSummary').textContent = 'ZIP 文件 · ' + formatSize(summary.size_bytes) +
      ' · 包含 ' + count + ' 份 PDF。选择文件查看第一页。';
    if (!count) {
      $('resultPreviewSummary').textContent = '结果已生成，但此 ZIP 中没有可在线预览的 PDF，可下载查看全部文件。';
      return;
    }
    const list = $('resultPreviewItems');
    const grid = $('resultPreviewImages');
    files.forEach((file, i) => {
      const button = document.createElement('button');
      button.className = 'result-preview-file-btn';
      button.type = 'button';
      button.disabled = !file.previewable;
      button.textContent = file.name + ' · ' + formatSize(file.size_bytes) +
        (file.previewable ? '' : ' · 文件较大，请下载查看');
      button.addEventListener('click', () => {
        [...list.querySelectorAll('button')].forEach(el => el.classList.remove('active'));
        button.classList.add('active');
        grid.replaceChildren();
        grid.appendChild(imagePreview(jobId, 1, file.index, file.name + ' · 第1页'));
      });
      list.appendChild(button);
      if (i === 0 && file.previewable) button.click();
    });
    if (summary.truncated) {
      const notice = document.createElement('p');
      notice.textContent = '列表只显示前100份PDF；完整文件请下载ZIP查看。';
      list.appendChild(notice);
    }
  }

  async function showPreview(jobId) {
    const version = ++renderVersion;
    $('resultPreviewCard').classList.remove('hidden');
    $('resultPreviewSummary').textContent = '正在读取输出文件摘要……';
    $('resultPreviewItems').replaceChildren();
    $('resultPreviewImages').replaceChildren();
    try {
      const response = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/result-summary', {cache:'no-store'});
      if (!response.ok) throw new Error(response.status === 404 ? '结果文件已过期。' : '当前结果暂不支持在线预览。');
      const summary = await response.json();
      if (renderVersion !== version) return;
      if (summary.kind === 'pdf') renderPdfSummary(jobId, summary);
      else if (summary.kind === 'zip') renderZipSummary(jobId, summary);
      else $('resultPreviewSummary').textContent = '此格式暂不支持在线预览，请下载查看。';
    } catch (err) {
      if (renderVersion === version) {
        $('resultPreviewSummary').textContent = (err.message || '无法加载预览') +
          ' 原始下载不受影响。';
      }
    }
  }

  function bindEvents() {
    $('recoveryDismissBtn').addEventListener('click', () => {
      $('recoveryCard').classList.add('hidden');
    });
    $('recoveryActionBtn').addEventListener('click', () => {
      $('recoveryCard').classList.add('hidden');
      if (failedMode === 'recheck' && pendingJobId) {
        const id = pendingJobId;
        state.busy = true;
        state.jobId = id;
        state.pollTransientErrors = 0;
        $('runBtn').disabled = true;
        $('runBtn').textContent = '正在查询任务...';
        $('cancelBtn').classList.remove('hidden');
        updateProgress(20, '正在重新查询任务状态');
        pollJob(id);
      } else {
        runTool();
      }
    });
  }

  return {clear, showPreview, showRecovery, bindEvents};
})();
window.resultUI = resultUI;
document.addEventListener('DOMContentLoaded', resultUI.bindEvents);
