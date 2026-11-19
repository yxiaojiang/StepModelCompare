import { mountView } from './view-panel.js';
import { Viewer } from './viewer.js';

const $ = selector => document.querySelector(selector);
const container = $('#viewer');
const status = $('#query-status');
const candidates = $('#candidates');
const reviewList = $('#review');
const reviewGroup = $('#review-group');
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

async function json(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  return body;
}

function setStatus(state, query, reason) {
  status.dataset.state = state;
  status.textContent = reason ? `${statusLabels[state]} · ${reason}` : statusLabels[state];
  if (query) {
    $('#query-name').textContent = query.file_name;
    const source = $('#query-source');
    source.textContent = query.source;
    source.hidden = query.source !== '演示样例';
  }
}

function clearResults() {
  viewer?.clear();
  candidates.replaceChildren();
  reviewList.replaceChildren();
  reviewGroup.hidden = true;
  reviewGroup.open = false;
  $('#candidate-count').textContent = '';
  $('#review-count').textContent = '';
  $('#result-hint').hidden = false;
  $('#result-hint').textContent = '等待检索结果。';
  $('#query-source').hidden = true;
  delete container.dataset.candidate;
  delete container.dataset.queryId;
}

function textLine(tag, content, className) {
  const line = document.createElement(tag);
  line.textContent = content;
  if (className) line.className = className;
  return line;
}

function candidateRow(item, data, signal) {
  const entry = document.createElement('li');
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.model = item.id;
  button.setAttribute('aria-pressed', 'false');
  const alignment = item.alignment;
  const { old_to_new, new_to_old } = alignment.overall;
  const delta = alignment.size_delta_mm.map(value => value.toFixed(2)).join(' / ');
  button.append(textLine('strong', item.id), textLine('span', `修订 ${item.revision} · ${item.source}`, 'info'),
    textLine('span', `主轴尺寸差 ${delta} mm`, 'info'),
    textLine('span', `整体 RMS 新→旧 ${new_to_old.rms_mm.toFixed(3)} / 旧→新 ${old_to_new.rms_mm.toFixed(3)} mm`, 'info'));
  if (alignment.reason) button.append(textLine('span', alignment.reason, 'review-reason'));
  button.addEventListener('click', async () => {
    container.querySelector('.empty-view').textContent = '正在加载模型…';
    document.querySelectorAll('.model-list button[aria-pressed="true"]').forEach(old => old.setAttribute('aria-pressed', 'false'));
    button.setAttribute('aria-pressed', 'true');
    delete container.dataset.candidate;
    if (!viewer) return;
    try {
      const newModel = { ...data.query.info, glb: data.query.glb, label: data.query.file_name };
      const oldModel = { ...item.info, glb: item.glb, label: `${item.id} · 修订 ${item.revision}` };
      const ready = await viewer.load(newModel, oldModel, alignment);
      if (ready && !signal.aborted) {
        container.dataset.queryId = data.id;
        container.dataset.candidate = item.id;
      }
    } catch (error) {
      container.querySelector('.empty-view').textContent = `模型加载失败 · ${error.message}`;
    }
  });
  entry.append(button);
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
  (candidates.querySelector('button') || reviewList.querySelector('button'))?.click();
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
  const form = new FormData();
  form.append('file', file);
  try {
    const accepted = await json('/api/queries', { method: 'POST', body: form, signal });
    while (!signal.aborted) {
      const data = await json(`/api/queries/${accepted.id}`, { signal });
      if (signal.aborted) return;
      setStatus(data.state, data.query, data.reason);
      if (data.state === 'complete') {
        showResponse(data, signal);
        return;
      }
      if (data.state === 'failed') {
        $('#result-hint').textContent = '请重新选择 STEP 文件。';
        return;
      }
      await waitForPoll(signal);
    }
  } catch (error) {
    if (signal.aborted) return;
    setStatus('failed', null, error.message);
    $('#result-hint').textContent = '请重新选择 STEP 文件。';
  }
});

const dialog = $('#library');
$('#open-library').addEventListener('click', async () => {
  dialog.showModal();
  $('#library-count').textContent = '';
  $('#library-status').textContent = '正在读取…';
  $('#library-body').replaceChildren();
  try {
    const entries = await json('/api/library');
    for (const row of entries) {
      const cells = [row.id, row.file_name, row.part_number || '未知', row.revision,
        row.source, row.error || '有效'];
      const tr = document.createElement('tr');
      cells.forEach((value, index) => tr.append(textLine('td', value, index === 4 ? 'source-cell' : '')));
      $('#library-body').append(tr);
    }
    $('#library-count').textContent = entries.length;
    $('#library-status').textContent = '';
  } catch (error) {
    $('#library-status').textContent = `历史库读取失败 · ${error.message}`;
  }
});
