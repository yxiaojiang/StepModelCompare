// 展示模型台账、批量上传和审核反馈。后台接口按角色独立实施数据范围校验。
import React, { useState } from "react";

import { platformRequest } from "./api.js";
import { dateLabel, EnterpriseSelect, Pagination, useAction, usePage } from "./PlatformControls.jsx";
import { reviewPayload } from "./platform-forms.js";

const statuses = {
  PENDING: "待审核", INDEXING: "索引中", APPROVED: "已入库",
  PARSE_FAILED: "解析失败", REJECTED: "已驳回",
};
const uploadReasons = {
  ENTERPRISE_STORAGE_QUOTA: "企业存储配额不足",
  INVALID_EXTENSION_OR_HEADER: "扩展名或 STEP 文件头无效",
  FILE_TOO_LARGE: "文件超过大小上限",
};

function UploadBatch({ admin, enterprises, token, onExpired, onUploaded }) {
  const [result, setResult] = useState(null);
  const upload = useAction(onExpired);

  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const body = new FormData();
    for (const file of values.getAll("files[]")) body.append("files[]", file);
    const path = admin ? "/models/batch-upload" : "/client/models/batch-upload";
    const query = admin ? { enterprise_id: values.get("enterprise_id") } : {};
    setResult(null);
    void upload.run((signal) => platformRequest(path, token, { query, body, method: "POST", signal }), (data) => {
      setResult(data);
      form.reset();
      onUploaded();
    });
  }

  return <section className="platform-card">
    <h2>{admin ? "管理员直传" : "企业批量上传"}</h2>
    <form className="platform-form platform-form-inline" onSubmit={submit}>
      {admin && <label>入库企业<EnterpriseSelect name="enterprise_id" required
        enterprises={enterprises.filter((row) => row.status === "ACTIVE")} emptyLabel="选择企业" /></label>}
      <label>STEP 文件<input name="files[]" type="file" accept=".stp,.step" multiple required /></label>
      <button type="submit" className="primary-button" disabled={upload.busy}>
        {upload.busy ? "正在上传…" : admin ? "上传并进入索引" : "上传并提交审核"}
      </button>
    </form>
    {upload.error && <p className="operation-error" role="alert">{upload.error}</p>}
    {result && <details className="platform-feedback" open>
      <summary>已接收 {result.accepted.length} · 重复 {result.skipped_duplicates.length} · 拒收 {result.rejected.length}</summary>
      <ul>
        {result.accepted.map((row) => <li key={row.model_id}>{row.model_name} · {statuses[row.status]}</li>)}
        {result.skipped_duplicates.map((row, index) => <li key={`duplicate-${index}`}>
          {row.model_name} · 已有同 MD5 记录（{statuses[row.existing_status]}）
        </li>)}
        {result.rejected.map((row, index) => <li key={`rejected-${index}`}>
          {row.filename} · {uploadReasons[row.reason] ?? row.reason}
        </li>)}
      </ul>
    </details>}
  </section>;
}

function ReviewBatch({ selected, token, onExpired, onReviewed, disabled }) {
  const [action, setAction] = useState("approve");
  const [result, setResult] = useState(null);
  const review = useAction(onExpired);

  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setResult(null);
    void review.run((signal) => {
      const data = reviewPayload(selected, action, {
        publicDemo: values.get("public_demo") === "on", reason: values.get("reason") ?? "",
      });
      return platformRequest("/admin/models/batch-review", token, { data, signal });
    }, (data) => {
      setResult(data.results);
      form.reset();
      onReviewed();
    });
  }

  return <>
    <form className="platform-form platform-form-inline review-form" onSubmit={submit}>
      <label>审核决定<select value={action} onChange={(event) => setAction(event.target.value)}>
        <option value="approve">通过并进入索引</option>
        <option value="reject">驳回</option>
      </select></label>
      {action === "approve"
        ? <label className="platform-check"><input name="public_demo" type="checkbox" /> 设为公开演示模型</label>
        : <label>驳回原因<input name="reason" maxLength={512} required /></label>}
      <button className="primary-button" type="submit" disabled={review.busy || disabled || selected.size === 0}>
        {review.busy ? "正在审核…" : `提交审核（${selected.size} 件）`}
      </button>
    </form>
    {review.error && <p className="operation-error" role="alert">{review.error}</p>}
    {result && <details className="platform-feedback" open>
      <summary>审核成功 {result.filter((row) => row.status_code === 200).length} / {result.length} 件</summary>
      <ul>{result.map((row) => <li key={row.model_id}>
        模型 #{row.model_id} · {statuses[row.status] ?? "已处理或不存在"}
        {row.warning_code === "MODEL_SOURCE_CLEANUP_FAILED" && " · 原件清理失败"}
      </li>)}</ul>
    </details>}
  </>;
}

/** 挂载企业或管理员台账。仅管理员界面提供跨企业筛选、直传和审核。 */
export function ModelLedger({ token, onExpired, admin = false, enterprises = [] }) {
  const page = usePage(admin ? "/admin/models" : "/enterprise/models", token, onExpired);
  const [selected, setSelected] = useState(() => new Set());
  const pending = page.rows.filter((row) => row.status === "PENDING");
  const allSelected = pending.length > 0 && pending.every((row) => selected.has(row.id));
  const enterpriseNames = new Map(enterprises.map((row) => [row.id, row.name]));

  function refresh() {
    setSelected(new Set());
    page.refresh();
  }

  function search(event) {
    event.preventDefault();
    setSelected(new Set());
    page.apply(Object.fromEntries(new FormData(event.currentTarget)));
  }

  return <div className="platform-stack">
    <UploadBatch admin={admin} enterprises={enterprises} token={token} onExpired={onExpired} onUploaded={refresh} />
    <section className="platform-card">
      <div className="platform-heading">
        <h2>{admin ? "模型台账与审核" : "企业模型台账"}</h2>
        <button type="button" className="outline-button" onClick={refresh}>刷新台账</button>
      </div>
      <form className="platform-form platform-form-inline" onSubmit={search}>
        <label>状态<select name="status">
          <option value="">全部状态</option>
          {Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        {admin && <label>所属企业<EnterpriseSelect name="enterprise_id" enterprises={enterprises} emptyLabel="全部企业" /></label>}
        <label>模型名称<input name="name" maxLength={255} /></label>
        <label>MD5<input name="md5" pattern="[a-fA-F0-9]{32}" maxLength={32} /></label>
        <label>上传人编号<input name="uploader_id" inputMode="numeric" pattern="[1-9][0-9]{0,18}" /></label>
        <button className="outline-button" type="submit">筛选</button>
      </form>
      {admin && <ReviewBatch selected={selected} token={token} onExpired={onExpired} onReviewed={refresh} disabled={page.loading} />}
      {page.rows.length > 0 && <div className="platform-table-wrap">
        <table className="platform-table" aria-label="模型台账">
          <thead><tr>
            {admin && <th><input type="checkbox" aria-label="选择本页全部待审模型" checked={allSelected}
              disabled={pending.length === 0}
              onChange={(event) => setSelected(new Set(event.target.checked ? pending.map((row) => row.id) : []))} /></th>}
            <th>模型名称</th>{admin && <th>所属企业</th>}<th>状态</th><th>MD5</th><th>上传 / 入库日期</th>
            <th>上传人</th><th>特征摘要</th><th>选中次数</th><th>诊断</th>
          </tr></thead>
          <tbody>{page.rows.map((row) => <tr key={row.id}>
            {admin && <td>{row.status === "PENDING" && <input type="checkbox" aria-label={`选择模型 ${row.model_name} #${row.id}`}
              checked={selected.has(row.id)} onChange={(event) => {
                const next = new Set(selected);
                if (event.target.checked) next.add(row.id);
                else next.delete(row.id);
                setSelected(next);
              }} />}</td>}
            <td><strong>{row.model_name}</strong><small>#{row.id}</small></td>
            {admin && <td>{enterpriseNames.get(row.enterprise_id) ?? row.enterprise_id ?? "—"}</td>}
            <td><span className={`model-status ${row.status.toLowerCase()}`}>{statuses[row.status]}</span>
              {row.is_public_demo && <small>公开演示</small>}</td>
            <td><code>{row.md5}</code></td>
            <td>{dateLabel(row.uploaded_at)}{row.approved_at && <small>入库 {dateLabel(row.approved_at)}</small>}</td>
            <td>{row.uploader_name}<small>#{row.uploader_id}</small></td>
            <td>{row.feature_payload ? <details className="feature-detail">
              <summary>{row.feature_payload.feature_version ?? "查看特征"}</summary>
              <pre>{JSON.stringify(row.feature_payload, null, 2)}</pre>
            </details> : "—"}</td>
            <td>{row.selected_count}</td><td>{row.reject_or_error_reason ?? "—"}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      <Pagination page={{
        ...page,
        next() { setSelected(new Set()); page.next(); },
        back() { setSelected(new Set()); page.back(); },
      }} />
    </section>
  </div>;
}
