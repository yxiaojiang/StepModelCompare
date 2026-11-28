// 编辑规则草稿、保存不可变版本并激活。加载旧版时同时保留不在页面编辑的安全门禁参数。
import React, { useState } from "react";

import { platformRequest } from "./api.js";
import { EnterpriseSelect, Pagination, useAction, usePage } from "./PlatformControls.jsx";
import { checkWeights, dimensions } from "./platform-forms.js";

const dimensionLabels = ["基本信息", "三维体积", "三维标注 PMI", "曲面特性", "表面吻合度", "主轴 OBB", "孔系拓扑"];
const thresholdDefaults = { D1: 1, D2: 0.15, D3: 1, D4: 0.15, D6: 0.10, D7: 1, rms_mm: 2, p95_mm: 2, max_mm: 8 };
const thresholdLabels = { rms_mm: "D5 RMS（mm）", p95_mm: "D5 P95（mm）", max_mm: "D5 最大距离（mm）" };
const defaultRule = {
  weights: { D1: 10, D2: 10, D3: 10, D4: 10, D5: 40, D6: 10, D7: 10 },
  thresholds: thresholdDefaults,
  both_missing: "REDISTRIBUTE",
  one_side_missing: "ZERO",
  neutral_score: 50,
  max_refine_k: 50,
};

/** 展示并修改规则的作用域、七维权重、零分阈值及缺项策略。 */
export function RulesPage({ token, onExpired, enterprises }) {
  const page = usePage("/admin/rules", token, onExpired);
  const action = useAction(onExpired);
  const [draft, setDraft] = useState(defaultRule);
  const [scope, setScope] = useState("");
  const [name, setName] = useState("");
  const [recent, setRecent] = useState(null);
  const { total, valid } = checkWeights(draft.weights);
  const names = new Map(enterprises.map((row) => [row.id, row.name]));

  function edit(key, value) {
    setDraft((previous) => ({ ...previous, [key]: value }));
  }

  function editValue(group, key, value) {
    setDraft((previous) => ({ ...previous, [group]: { ...previous[group], [key]: value } }));
  }

  function save(event) {
    event.preventDefault();
    if (!valid || !name.trim()) return;
    const numeric = (values) => Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)]));
    const data = {
      name: name.trim(), enterprise_id: scope || null,
      rule: { ...draft, weights: numeric(draft.weights), thresholds: numeric(draft.thresholds),
        max_refine_k: Number(draft.max_refine_k), neutral_score: Number(draft.neutral_score) },
    };
    void action.run((signal) => platformRequest("/admin/rules", token, { data, signal }), (record) => {
      setRecent(record);
      page.refresh();
    });
  }

  function activate(id) {
    void action.run((signal) => platformRequest(`/admin/rules/${id}/activate`, token, { method: "POST", signal }), (record) => {
      setRecent(record);
      page.refresh();
    });
  }

  return <div className="platform-stack">
    <section className="platform-card">
      <h2>相似度规则配置</h2>
      <form className="platform-form" onSubmit={save}>
        <div className="platform-fields">
          <label>规则名称<input value={name} maxLength={150} required onChange={(event) => setName(event.target.value)} /></label>
          <label>作用域<EnterpriseSelect enterprises={enterprises} emptyLabel="全局"
            value={scope} onChange={(event) => setScope(event.target.value)} /></label>
          <label>精排上限 K<input type="number" min="1" max="2147483647" step="1" required value={draft.max_refine_k}
            onChange={(event) => edit("max_refine_k", event.target.value)} /></label>
        </div>
        <fieldset>
          <legend>维度权重</legend>
          <div className="platform-fields">
            {dimensions.map((key, index) => <label key={key}>{key} {dimensionLabels[index]}（%）
              <input type="number" min="0" max="100" step="any" required value={draft.weights[key]}
                onChange={(event) => editValue("weights", key, event.target.value)} />
            </label>)}
          </div>
          <output className={valid ? "weight-valid" : "operation-error"} aria-live="polite">
            合计 {Number.isFinite(total) ? Number(total.toFixed(8)) + "%" : "—"}{valid ? "" : "，权重须合计 100%"}
          </output>
        </fieldset>
        <fieldset>
          <legend>零分阈值</legend>
          <div className="platform-fields">
            {Object.keys(thresholdDefaults).map((key) => <label key={key}>{thresholdLabels[key] ?? key}
              <input type="number" min="0" step="any" required value={draft.thresholds[key]}
                onChange={(event) => editValue("thresholds", key, event.target.value)} />
            </label>)}
          </div>
        </fieldset>
        <div className="platform-fields">
          <label>双方缺失<select value={draft.both_missing} onChange={(event) => edit("both_missing", event.target.value)}>
            <option value="REDISTRIBUTE">重分配权重</option><option value="D3_TO_D7">D3 优先转给 D7</option>
          </select></label>
          <label>单方缺失<select value={draft.one_side_missing} onChange={(event) => edit("one_side_missing", event.target.value)}>
            <option value="ZERO">零分</option><option value="NEUTRAL">中性分</option><option value="REALLOCATE">重分配权重</option>
          </select></label>
          {draft.one_side_missing === "NEUTRAL" && <label>中性分<input type="number" min="0" max="100" step="any"
            value={draft.neutral_score} required onChange={(event) => edit("neutral_score", event.target.value)} /></label>}
        </div>
        <div className="platform-heading">
          <button className="primary-button" type="submit"
            disabled={action.busy || !valid || !name.trim() || Object.values(draft.thresholds).some((value) => !(Number(value) > 0))}>
            保存新版本
          </button>
          {recent && <span className="body-muted" role="status">{recent.name} · {recent.is_active ? "已激活" : "版本已保存"}</span>}
          {recent && !recent.is_active && <button className="outline-button" type="button"
            disabled={action.busy} onClick={() => activate(recent.rule_id)}>激活刚保存的版本</button>}
        </div>
      </form>
      {action.error && <p className="operation-error" role="alert">{action.error}</p>}
    </section>
    <section className="platform-card">
      <h2>规则版本</h2>
      {page.rows.length > 0 && <div className="platform-table-wrap">
        <table className="platform-table" aria-label="规则版本">
          <thead><tr><th>规则名称</th><th>版本</th><th>作用域</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{page.rows.map((row) => <tr key={row.rule_id}>
            <td>{row.name}</td><td><code>{row.version ?? "—"}</code></td>
            <td>{row.error_code ? "—" : row.enterprise_id === null ? "全局" : names.get(row.enterprise_id) ?? row.enterprise_id}</td>
            <td>{row.error_code ? "无效配置" : row.is_active ? "已生效" : "未生效"}</td>
            <td className="row-actions">
              <button type="button" className="outline-button" disabled={!row.rule} onClick={() => {
                setDraft(row.rule);
                setScope(row.enterprise_id ?? "");
                setName(row.name);
              }}>载入编辑</button>
              <button type="button" className="outline-button" disabled={action.busy || row.is_active || !row.rule}
                onClick={() => activate(row.rule_id)}>激活</button>
            </td>
          </tr>)}</tbody>
        </table>
      </div>}
      <Pagination page={page} />
    </section>
  </div>;
}
