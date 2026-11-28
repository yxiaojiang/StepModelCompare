// 预览加载与查看器控件。每次切换任务或候选都会释放过期的请求和网格。
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { overlayTransform } from "./alignment.js";
import { fetchPreview } from "./api.js";

const empty = { state: "empty" };
const inFlight = { state: "loading" };

function disposeModel(model) {
  const resources = new Set();
  model.traverse((object) => {
    if (!object.isMesh) return;
    resources.add(object.geometry);
    for (const material of [object.material].flat()) resources.add(material);
  });
  for (const resource of resources) resource.dispose();
}

function usePreview(token, job, model, allowed, onExpired, retry) {
  const [result, setResult] = useState(empty);
  useEffect(() => {
    if (!token || !job || !allowed) return;
    const controller = new AbortController();
    const item = { token, job, model, retry, state: "loading" };
    async function load() {
      try {
        const [buffer, { GLTFLoader }] = await Promise.all([
          fetchPreview(token, job, model, controller.signal), import("three/addons/loaders/GLTFLoader.js"),
        ]);
        if (controller.signal.aborted) return;
        const { scene } = await new GLTFLoader().parseAsync(buffer, "");
        if (controller.signal.aborted) {
          disposeModel(scene);
          return;
        }
        setResult(Object.assign(item, { state: "ready", scene }));
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error.status === 401) onExpired();
        else setResult(Object.assign(item, {
          state: "error",
          message: error.status ? error.message : "预览加载失败。",
        }));
      }
    }
    void load();
    return () => {
      controller.abort();
      item.released = true;
      if (item.scene) disposeModel(item.scene);
    };
  }, [token, job, model, allowed, onExpired, retry]);

  if (!token || !job || !allowed) return empty;
  return !result.released && result.token === token && result.job === job && result.model === model &&
    result.retry === retry ? result : inFlight;
}

function PreviewStatus({ preview, emptyText }) {
  const text = { empty: emptyText, loading: "正在加载三维预览…", error: preview.message }[preview.state];
  return text ? <div className="preview-status"><span>{text}</span></div> : null;
}

/** 显示已授权的任务预览。candidate 可为空；onExpired 在服务端拒绝令牌时清空会话。 */
export function Viewer({ token, job, fileName, candidate, onExpired }) {
  const host = useRef(null);
  const canvas = useRef(null);
  const [engine, setEngine] = useState(null);
  const [retry, setRetry] = useState(0);
  const [renderError, setRenderError] = useState(false);
  const [display, setDisplay] = useState({});
  const model = candidate?.model_id ?? null;
  const queryPreview = usePreview(token, job, null, true, onExpired, retry);
  const otherPreview = usePreview(token, job, model, model != null, onExpired, retry);
  const transform = useMemo(() => overlayTransform(candidate), [candidate]);
  const loaded = queryPreview.state === "ready" && otherPreview.state === "ready";
  const canOverlay = Boolean(transform && loaded && engine && !renderError);
  const active = display.job === job && display.model === model ? display : {};
  const overlay = canOverlay && active.overlay === true;
  const queryVisible = active.queryVisible !== false;
  const candidateVisible = active.candidateVisible !== false;
  const disabledReason = candidate?.needs_review ? "此候选待复核，仅支持并排查看。" :
    candidate && !transform ? "此候选没有通过门禁的对齐矩阵。" : null;

  useEffect(() => {
    let closed = false;
    let viewport;
    void import("./viewport.js").then(({ createViewport }) => {
      if (closed) return;
      viewport = createViewport(host.current, canvas.current);
      setEngine(viewport);
    }).catch(() => {
      if (!closed) setRenderError(true);
    });
    return () => {
      closed = true;
      viewport?.dispose();
    };
  }, []);
  useLayoutEffect(() => {
    engine?.setModels(queryPreview.scene, otherPreview.scene);
  }, [engine, queryPreview.scene, otherPreview.scene]);
  useLayoutEffect(() => {
    engine?.setMode(overlay ? transform : null, queryVisible, candidateVisible);
  }, [engine, overlay, transform, queryVisible, candidateVisible]);

  function change(values) {
    setDisplay({ job, model, ...active, ...values });
  }

  return (
    <section className="viewer-panel" aria-labelledby="viewer-title">
      <div className="viewer-toolbar">
        <h2 id="viewer-title">三维对比</h2>
        <div className="viewer-actions">
          <div className="segmented" aria-label="视图模式" role="group">
            <button type="button" aria-pressed={!overlay} onClick={() => change({ overlay: false })}>并排</button>
            <button type="button" aria-pressed={overlay} disabled={!canOverlay}
              onClick={() => change({ overlay: true, queryVisible: true, candidateVisible: true })}>叠加</button>
          </div>
          {(queryPreview.state === "error" || otherPreview.state === "error") &&
            <button type="button" className="text-button" onClick={() => setRetry((value) => value + 1)}>重载预览</button>}
          <button type="button" className="text-button" disabled={!engine || (!queryPreview.scene && !otherPreview.scene)}
            onClick={() => engine?.reset()}>重置视角</button>
        </div>
      </div>
      <div className={"view-canvas" + (overlay ? " overlay" : "")} ref={host} aria-label="STEP 模型三维预览">
        <canvas ref={canvas} aria-hidden="true" />
        <div className="view-labels">
          <div><i className="swatch query" />查询件{fileName && <span title={fileName}>{fileName}</span>}</div>
          <div><i className="swatch candidate" />候选件{candidate && <span title={candidate.model_name}>{candidate.model_name}</span>}</div>
        </div>
        {renderError ? <div className="render-error">当前浏览器无法开启 WebGL。仍可查看结果与分项得分。</div> :
          !overlay && <div className="view-placeholders">
            <PreviewStatus preview={queryPreview} emptyText="提交 STEP 后查看查询模型" />
            <PreviewStatus preview={otherPreview} emptyText="从结果中选择候选模型" />
          </div>}
      </div>
      <div className="viewer-footer">
        {overlay ? <div className="visibility-options">
          <label><input type="checkbox" checked={queryVisible} disabled={queryVisible && !candidateVisible}
            onChange={(event) => change({ queryVisible: event.target.checked })} /> 查询件</label>
          <label><input type="checkbox" checked={candidateVisible} disabled={candidateVisible && !queryVisible}
            onChange={(event) => change({ candidateVisible: event.target.checked })} /> 候选件</label>
        </div> : <span>{disabledReason ?? "拖动旋转，滚轮缩放，右键拖动平移"}</span>}
        <span>{overlay ? "同尺度 · 已应用对齐矩阵" : "双视口同步 · 同尺度"}</span>
      </div>
    </section>
  );
}
