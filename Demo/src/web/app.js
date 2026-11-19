import { mountView } from './view-panel.js';
import { Viewer } from './viewer.js';

const $ = selector => document.querySelector(selector);
const container = $('#viewer');
const status = $('#query-status');
const candidates = $('#candidates');
const reviewList = $('#review');
const reviewGroup = $('#review-group');
const progressDialog = $('#comparison-progress');
const progressClose = $('#progress-close');
const scoreDialog = $('#score-dialog');
mountView(container);
let viewer;
try {
  viewer = new Viewer(container);
  window.stepViewer = viewer;
} catch {
  container.querySelector('.empty-view').textContent = '浏览器无法初始化 WebGL';
}
let operation = new AbortController();

const statusLabels = {
  parsing: '解析中', comparing: '比对中', complete: '完成', failed: '失败',
};

progressDialog.addEventListener('cancel', event => {
  if (progressClose.hidden) event.preventDefault();
});

function showProgress(percent, message) {
  $('#progress-meter').value = percent;
  $('#progress-percent').textContent = `${percent}%`;
  const stage = $('#progress-stage');
  if (stage.textContent !== message) stage.textContent = message;
}

function showProgressFailure(message) {
  delete progressDialog.dataset.active;
  progressDialog.dataset.failed = 'true';
  $('#progress-title').textContent = '比对失败';
  $('#progress-stage').textContent = message;
  progressClose.hidden = false;
  progressClose.focus();
}

export function readApiErrorMessage(status, text) {
  const raw = (text || '').trim();
  if (!raw) return `服务器返回错误（${status}），请重试。`;
  try {
    const body = JSON.parse(raw);
    const detail = [body.detail, body.error, body.reason, body.message].find(value => typeof value === 'string' && value.trim());
    if (detail) {
      const cleaned = detail.trim().replace(/[A-Za-z]:\\[^\s]+/g, '').replace(/\/(?:home|Users|tmp|var)[^\s]+/g, '').trim();
      if (cleaned && cleaned.length <= 300) return cleaned;
    }
  } catch {
    /* Non-JSON bodies such as "Internal Server Error" should not be parsed as application errors. */
  }
  return `服务器返回错误（${status}），请重试。`;
}

async function json(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw Error(response.ok ? '服务器返回了无法解析的响应，请重试。' : readApiErrorMessage(response.status, text));
    }
  }
  if (!response.ok) throw Error(readApiErrorMessage(response.status, text));
  return body;
}

function setStatus(state, query, reason) {
  status.dataset.state = state;
  status.textContent = reason ? `${statusLabels[state]} · ${reason}` : statusLabels[state];
  if (query) {
    $('#query-name').textContent = query.file_name;
  }
}

function clearResults() {
  if (scoreDialog.open) scoreDialog.close();
  viewer?.clear();
  candidates.replaceChildren();
  reviewList.replaceChildren();
  reviewGroup.hidden = true;
  reviewGroup.open = false;
  $('#candidate-count').textContent = '';
  $('#review-count').textContent = '';
  $('#result-hint').hidden = false;
  $('#result-hint').textContent = '等待检索结果。';
  delete container.dataset.candidate;
  delete container.dataset.queryId;
}

function textLine(tag, content, className) {
  const line = document.createElement(tag);
  line.textContent = content;
  if (className) line.className = className;
  return line;
}

const scoreLabels = {
  surface_rms: '表面 RMS', surface_p95: '表面 P95', surface_max: '最大表面偏差',
  size: '主轴尺寸', area: '表面积', volume: '体积',
};

function showScore(item, modelLabel) {
  const { score, parts } = item.similarity;
  $('#score-model').textContent = modelLabel;
  $('#score-total').textContent = score.toFixed(1);
  const review = $('#score-review');
  review.textContent = item.alignment.reason ? `待复核 · ${item.alignment.reason}` : '';
  review.hidden = !item.alignment.reason;
  const rows = Object.entries(parts).map(([key, part]) => {
    const surface = key.startsWith('surface_');
    const row = document.createElement('tr');
    const heading = textLine('th', scoreLabels[key]);
    heading.scope = 'row';
    row.append(heading,
      textLine('td', surface ? `${part.error.toFixed(3)} mm` : `${(part.error * 100).toFixed(2)}%`),
      textLine('td', `${part.points.toFixed(2)} / ${part.weight}`),
      textLine('td', surface ? `${part.zero_at} mm` : `${part.zero_at * 100}%`));
    return row;
  });
  $('#score-body').replaceChildren(...rows);
  scoreDialog.showModal();
}

function candidateRow(item, data, signal) {
  const entry = document.createElement('li');
  entry.className = 'result-card';
  const button = document.createElement('button');
  button.className = 'candidate-select';
  button.type = 'button';
  button.dataset.model = item.id;
  button.setAttribute('aria-pressed', 'false');
  const alignment = item.alignment;
  const modelLabel = item.source === '基础模型' ? item.file_name : item.id;
  const heading = document.createElement('span');
  heading.className = 'candidate-heading-row';
  heading.append(textLine('strong', modelLabel, 'candidate-heading'));
  if (alignment.reason) heading.append(textLine('span', '待复核', 'review-status'));
  button.append(heading);
  if (item.similarity) {
    const score = `${item.similarity.score.toFixed(1)} / 100`;
    const scoreLine = document.createElement('span');
    scoreLine.className = 'score-line';
    scoreLine.append(textLine('span', '相似度', 'similarity-label'), textLine('span', score, 'similarity-score'));
    button.append(scoreLine);
    const details = textLine('button', '详情', 'score-detail-button');
    details.type = 'button';
    details.setAttribute('aria-haspopup', 'dialog');
    details.setAttribute('aria-controls', 'score-dialog');
    details.setAttribute('aria-label', `查看 ${modelLabel} 的评分详情，相似度 ${score}`);
    details.addEventListener('click', () => showScore(item, modelLabel));
    entry.append(button, details);
  } else {
    entry.append(button);
  }
  button.addEventListener('click', async () => {
    container.querySelector('.empty-view').textContent = '正在加载模型…';
    document.querySelectorAll('.model-list button[aria-pressed="true"]').forEach(old => old.setAttribute('aria-pressed', 'false'));
    button.setAttribute('aria-pressed', 'true');
    delete container.dataset.candidate;
    if (!viewer) return;
    try {
      const newModel = { ...data.query.info, glb: data.query.glb, label: data.query.file_name };
      const oldModel = { ...item.info, glb: item.glb, label: modelLabel };
      const ready = await viewer.load(newModel, oldModel, alignment);
      if (ready && !signal.aborted) {
        container.dataset.queryId = data.id;
        container.dataset.candidate = item.id;
      }
    } catch (error) {
      container.querySelector('.empty-view').textContent = `模型加载失败 · ${error.message}`;
    }
  });
  entry.addEventListener('click', event => {
    if (event.target === entry) button.click();
  });
  return entry;
}

function showResponse(data, signal) {
  candidates.replaceChildren(...data.candidates.map(item => candidateRow(item, data, signal)));
  reviewList.replaceChildren(...data.review.map(item => candidateRow(item, data, signal)));
  $('#candidate-count').textContent = data.candidates.length;
  $('#review-count').textContent = data.review.length;
  reviewGroup.hidden = data.review.length === 0;
  const hint = $('#result-hint');
  hint.hidden = data.candidates.length > 0;
  hint.textContent = '没有满足对齐检查的候选。';
  if (!data.candidates.length && data.review.length) reviewGroup.open = true;
  (candidates.querySelector('.candidate-select') || reviewList.querySelector('.candidate-select'))?.click();
}

function waitForPoll(signal) {
  return new Promise(resolve => {
    if (signal.aborted) return resolve();
    const timeout = setTimeout(() => {
      signal.removeEventListener('abort', cancelled);
      resolve();
    }, 350);
    function cancelled() {
      clearTimeout(timeout);
      resolve();
    }
    signal.addEventListener('abort', cancelled, { once: true });
  });
}

$('#upload').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';
  operation.abort();
  operation = new AbortController();
  const { signal } = operation;
  clearResults();
  setStatus('parsing', { file_name: file.name });
  delete progressDialog.dataset.failed;
  progressDialog.dataset.active = 'true';
  progressClose.hidden = true;
  $('#progress-title').textContent = '正在比对 STEP';
  $('#progress-file').textContent = file.name;
  showProgress(0, '正在提交文件');
  if (!progressDialog.open) progressDialog.showModal();
  const form = new FormData();
  form.append('file', file);
  try {
    const accepted = await json('/api/queries', { method: 'POST', body: form, signal });
    while (!signal.aborted) {
      const data = await json(`/api/queries/${accepted.id}`, { signal });
      if (signal.aborted) return;
      setStatus(data.state, data.query, data.reason);
      showProgress(data.progress ?? 0, data.progress_text ?? statusLabels[data.state]);
      if (data.state === 'complete') {
        delete progressDialog.dataset.active;
        showResponse(data, signal);
        // Let the completed bar paint before dismissing the modal.
        await new Promise(resolve => setTimeout(resolve, 300));
        if (!signal.aborted) progressDialog.close();
        return;
      }
      if (data.state === 'failed') {
        $('#result-hint').textContent = '请重新选择 STEP 文件。';
        showProgressFailure(data.reason);
        return;
      }
      await waitForPoll(signal);
    }
  } catch (error) {
    if (signal.aborted) return;
    setStatus('failed', null, error.message);
    $('#result-hint').textContent = '请重新选择 STEP 文件。';
    showProgressFailure(error.message);
  }
});

const dialog = $('#library');
const libraryLoadDialog = $('#library-load-progress');
const libraryLoadClose = $('#library-load-close');
let libraryLoad = new AbortController();

export function shouldShowLibraryLoadProgress(data) {
  return data.state === 'waiting' || data.state === 'scanning' || data.state === 'indexing' || data.state === 'failed';
}

function renderLibraryRows(entries) {
  $('#library-body').replaceChildren();
  for (const row of entries) {
    const cells = [row.id, row.file_name, row.part_number || '未知', row.error || '有效'];
    const tr = document.createElement('tr');
    cells.forEach(value => tr.append(textLine('td', value)));
    $('#library-body').append(tr);
  }
  $('#library-count').textContent = entries.length;
}

function openLibraryTable(data) {
  closeLibraryLoadProgress();
  renderLibraryRows(data.entries || []);
  $('#library-status').textContent = (data.entries || []).length ? '' : '基础模型库中暂无 STEP 模型。';
  dialog.setAttribute('aria-busy', 'false');
  if (!dialog.open) dialog.showModal();
}

function closeLibraryLoadProgress() {
  libraryLoadDialog.removeAttribute('data-active');
  libraryLoadDialog.removeAttribute('data-failed');
  if (libraryLoadDialog.open) libraryLoadDialog.close();
}

function showLibraryLoadProgress(data) {
  const percent = $('#library-load-percent');
  const file = $('#library-load-file');
  const meter = $('#library-load-meter');
  const active = data.state === 'waiting' || data.state === 'scanning' || data.state === 'indexing';
  const failed = data.state === 'failed';
  libraryLoadDialog.toggleAttribute('data-active', active);
  libraryLoadDialog.toggleAttribute('data-failed', failed);
  libraryLoadClose.hidden = !failed;
  if (data.total == null) {
    percent.textContent = '--';
    meter.removeAttribute('value');
  } else {
    const value = data.percent == null ? (data.total === 0 ? 100 : Math.round(((data.done ?? 0) / data.total) * 100)) : data.percent;
    percent.textContent = `${value}%`;
    meter.max = 100;
    meter.value = value;
  }
  if (failed) {
    file.textContent = data.reason
      ? `基础模型库读取失败 · ${data.reason}`
      : (data.text || '基础模型库读取失败');
  } else {
    file.textContent = data.current || (data.total == null ? '正在扫描' : '');
  }
  if (!libraryLoadDialog.open) libraryLoadDialog.showModal();
}

libraryLoadDialog.addEventListener('cancel', event => {
  if (libraryLoadClose.hidden) event.preventDefault();
});
libraryLoadDialog.addEventListener('close', () => libraryLoad.abort());
dialog.addEventListener('close', () => libraryLoad.abort());

$('#open-library').addEventListener('click', async () => {
  libraryLoad.abort();
  libraryLoad = new AbortController();
  const { signal } = libraryLoad;
  try {
    let data = await json('/api/library/refresh', { method: 'POST', signal });
    while (!signal.aborted) {
      if (data.state === 'complete') {
        openLibraryTable(data);
        return;
      }
      if (shouldShowLibraryLoadProgress(data)) showLibraryLoadProgress(data);
      if (data.state === 'failed') return;
      await waitForPoll(signal);
      if (signal.aborted) return;
      data = await json('/api/library', { signal });
    }
  } catch (error) {
    if (signal.aborted) return;
    showLibraryLoadProgress({ state: 'failed', done: 0, total: null, reason: error.message, text: `基础模型库读取失败 · ${error.message}` });
  }
});

$('#open-library-folder').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await json('/api/library-folder', { method: 'POST' });
    $('#library-status').textContent = '';
  } catch (error) {
    $('#library-status').textContent = `无法打开基础模型库文件夹 · ${error.message}`;
  } finally {
    button.disabled = false;
  }
});

json('/api/library-folder').then(({ available }) => {
  $('#open-library-folder').hidden = !available;
}).catch(() => {
  $('#open-library-folder').hidden = true;
});
