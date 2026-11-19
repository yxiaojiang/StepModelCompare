export function mountView(container) {
  container.innerHTML = `
    <div class="view-header">
      <h2>三维对比</h2>
      <div class="view-tools">
        <label class="sr-only" for="view-mode">比较方式</label>
        <select id="view-mode" disabled><option value="side">联动并排</option><option value="overlay">双色叠加</option></select>
        <button id="reset-view" type="button">重置视角</button>
      </div>
    </div>
    <div class="panes">
      <div id="new-pane" class="pane">
        <div class="pane-title"><i class="swatch blue"></i><span class="pane-kind">查询件</span><span class="model-name"></span></div>
        <div class="canvas"></div>
      </div>
      <div id="old-pane" class="pane">
        <div class="pane-title"><i class="swatch orange"></i><span class="pane-kind">历史件</span><span class="model-name"></span></div>
        <div class="canvas"></div>
      </div>
      <div class="empty-view">选择候选以加载模型</div>
    </div>
    <div class="view-footer">
      <div class="legend"><span><i class="swatch blue"></i>查询件</span><span><i class="swatch orange"></i>历史件</span></div>
      <label>显示 <select id="visibility" disabled><option value="both">两件</option><option value="new">仅查询件</option><option value="old">仅历史件</option></select></label>
      <label class="alpha">历史件透明度 <input id="opacity" type="range" min="0.15" max="0.95" step="0.05" value="0.55" disabled></label>
      <button id="hole-view" type="button" hidden>样例标注 · 定位孔区</button>
      <output id="hole-value" hidden></output>
    </div>`;
}
