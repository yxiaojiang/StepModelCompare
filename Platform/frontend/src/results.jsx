// 把服务端分项、缺失诊断和审核原因呈现在结果区及对话框。
import React, { useLayoutEffect, useRef } from "react";

const dimensions = {
  D1: "名称与属性",
  D2: "体积",
  D3: "语义 PMI",
  D4: "表面与面片",
  D5: "双向表面误差",
  D6: "主轴包围盒",
  D7: "孔特征",
};

const statuses = {
  OK: "已计算",
  DISABLED: "未启用",
  PENDING_D5: "未完成精排",
  ONE_SIDE_MISSING_UNVERIFIED: "单方缺失（未核实）",
  BOTH_MISSING: "双方缺失",
  BOTH_MISSING_TRANSFERRED_TO_D7: "双方缺失，权重转入 D7",
};

const reviews = {
  SYMMETRY_AMBIGUITY: "存在多个可接受的对齐姿态",
  HANDEDNESS_MISMATCH: "存在镜像风险",
  INSUFFICIENT_FEATURES: "可用特征不足",
  CANDIDATE_FEATURE_UNAVAILABLE: "缺少该版本的特征",
  ALIGNMENT_FAILED: "未通过对齐检查",
  ERR_COMPARE_CANDIDATE_MESH: "候选网格不可用",
  ERR_COMPARE_CANDIDATE_GEOMETRY: "候选几何数据不可用",
};

const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 5 });
const points = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 将待复核 reason 转为中文，未知诊断保留服务端标识供核对。 */
export function reviewReason(reason) {
  return reviews[reason] ?? reason ?? "需人工复核";
}

function warningLabel(warning) {
  const [dimension, reason] = warning.split(":");
  return dimension + " " + (reason === "ONE_SIDE_MISSING_UNVERIFIED" ? "单方缺失（未核实）" : "部分数据未核实");
}

function value(numberValue, suffix = "") {
  return Number.isFinite(numberValue) ? number.format(numberValue) + suffix : "—";
}

/** 打开可用 Esc 关闭的模态对话框，关闭后回调 onClose，焦点由浏览器恢复。 */
export function Modal({ title, children, onClose, wide = false }) {
  const dialog = useRef(null);
  useLayoutEffect(() => {
    dialog.current.showModal();
  }, []);
  return (
    <dialog ref={dialog} className={wide ? "detail-dialog wide" : "detail-dialog"} onClose={onClose}
      aria-labelledby="dialog-title">
      <div className="dialog-heading">
        <h2 id="dialog-title">{title}</h2>
        <button type="button" className="icon-button" aria-label="关闭" onClick={() => dialog.current.close()}>×</button>
      </div>
      {children}
    </dialog>
  );
}

function Measurement({ row }) {
  if (row.measurements_mm) {
    return <>{Object.entries({ rms_mm: "RMS", p95_mm: "P95", max_mm: "最大" }).map(([id, label]) =>
      <span key={id} className="measurement">{label} {value(row.measurements_mm[id], " mm")}</span>)}</>;
  }
  return value(row.error);
}

/** 展开 candidate 的权重、实测误差、分数和数据状态，关闭时调用 onClose。 */
export function Breakdown({ candidate, onClose }) {
  const rows = candidate.breakdown ?? [];
  return <Modal title="分项评分" wide onClose={onClose}>
    <p className="detail-name">{candidate.model_name}</p>
    {candidate.needs_review && <p className="inline-note">{reviewReason(candidate.review_reason)}</p>}
    {!rows.length ? <p className="body-muted">此项没有可展示的分项评分。</p> :
      <div className="table-wrap"><table>
        <thead><tr>
          <th scope="col">维度</th><th scope="col">实测误差</th><th scope="col">原权重</th>
          <th scope="col">有效权重</th><th scope="col">子分</th><th scope="col">加权分</th><th scope="col">数据状态</th>
        </tr></thead>
        <tbody>{rows.map((row) => <tr key={row.dim}>
          <th scope="row">{row.dim} <span>{dimensions[row.dim]}</span></th>
          <td><Measurement row={row} /></td>
          <td>{value(row.raw_weight, "%")}</td><td>{value(row.effective_weight, "%")}</td>
          <td>{value(row.subscore)}</td><td>{value(row.weighted_score)}</td>
          <td>{statuses[row.status] ?? row.status}{row.unverified && row.status !== "ONE_SIDE_MISSING_UNVERIFIED" &&
            <span className="measurement">部分数据未核实</span>}</td>
        </tr>)}</tbody>
      </table></div>}
  </Modal>;
}

function CandidateRows({ rows, active, selected, onSelect, onDetails }) {
  return <ul className="candidate-list">{rows.map((item) => {
    const isActive = active?.model_id === item.model_id;
    return <li key={item.model_id} className={"candidate-row" + (isActive ? " current" : "")}>
      <button type="button" className="candidate-select" aria-pressed={isActive} onClick={() => onSelect(item)}>
        <span className="candidate-name" title={item.model_name}>{item.model_name}</span>
        <span className="candidate-meta">
          {item.needs_review ? reviewReason(item.review_reason) :
            Number.isFinite(item.similarity_score) ? points.format(item.similarity_score) + " 分" : "无可用分数"}
          {selected[item.model_id] && <span className="recorded"> · 已记录选中</span>}
        </span>
        {item.missing_warnings?.length > 0 &&
          <span className="missing-warnings">{item.missing_warnings.map(warningLabel).join("、")}</span>}
      </button>
      <button type="button" className="text-button" aria-label={item.model_name + " 的评分详情"}
        onClick={() => onDetails(item)}>详情</button>
    </li>;
  })}</ul>;
}

/** 展示任务 result 的可靠榜和折叠的待复核项，用户操作通过参数中的回调交给工作台。 */
export function Results({ result, active, selected, onSelect, onDetails }) {
  const candidates = result?.candidates ?? [];
  const review = result?.needs_review_items ?? [];
  const rowProps = { active, selected, onSelect, onDetails };
  return <section className="side-card" aria-labelledby="results-title">
    <div className="section-heading"><h2 id="results-title">检索结果</h2>{result && <span>{candidates.length} 个候选</span>}</div>
    {!result && <p className="body-muted">对比完成后显示候选模型。</p>}
    {result?.termination === "truncated_by_k" &&
      <p className="inline-note">已达到精排上限，部分潜在候选未完成精排。</p>}
    {Number.isFinite(result?.trial_remaining) && <p className="body-muted">剩余试用 {result.trial_remaining} 次</p>}
    {result && !candidates.length && <p className="body-muted">暂无通过门禁的候选。</p>}
    {candidates.length > 0 && <CandidateRows rows={candidates} {...rowProps} />}
    {review.length > 0 && <details className="review-list">
      <summary>待复核 <span>{review.length}</span></summary>
      <CandidateRows rows={review} {...rowProps} />
    </details>}
  </section>;
}
