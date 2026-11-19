import { mountView } from './view-panel.js';
import { Viewer } from './viewer.js';

const container = document.querySelector('#viewer');
mountView(container);
const viewer = new Viewer(container);
const selector = document.querySelector('#case');
const state = document.querySelector('#state');
const response = await fetch('./development/results.json');
if (!response.ok) throw Error('缺少开发样例实测结果');
const { models, cases } = await response.json();
const labels = { pose: '同件换姿态', hole: '安装板孔位改动', symmetric: '对齐歧义' };
for (const item of cases) selector.add(new Option(labels[item.id], item.id));

async function load() {
  const item = cases.find(candidate => candidate.id === selector.value);
  state.textContent = '正在加载模型…';
  try {
    const ready = await viewer.load(...['new', 'old'].map(key => ({
      ...models[item[key]], label: item[key],
    })), item.result);
    if (ready) {
      state.textContent = item.result.state === 'aligned' ? '对齐通过' : `待复核 · ${item.result.reason}`;
    }
  } catch (error) {
    state.textContent = `模型加载失败 · ${error.message}`;
    throw error;
  }
}

selector.addEventListener('change', load);
window.stepViewer = viewer;
await load();
