// 组合系统管理子页面，管理企业、邀请码和审计记录。邀请码明文仅从表单发送。
import React, { useState } from "react";

import { platformRequest } from "./api.js";
import { ModelLedger } from "./ModelLedger.jsx";
import { dateLabel, EnterpriseSelect, Pagination, useAction, useEnterprises, usePage } from "./PlatformControls.jsx";
import { RulesPage } from "./RulesPage.jsx";

const tabs = [
  ["models", "模型与审核"], ["rules", "规则配置"], ["enterprises", "企业"], ["invites", "邀请码"], ["audit", "审计日志"],
];

function Enterprises({ token, onExpired, onCreated }) {
  const page = usePage("/admin/enterprises", token, onExpired);
  const action = useAction(onExpired);
  const [notice, setNotice] = useState("");

  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const data = { name: values.get("name").trim(), code: values.get("code").trim(), storage_quota_mb: values.get("quota") };
    setNotice("");
    void action.run((signal) => platformRequest("/admin/enterprises", token, { data, signal }), (record) => {
      setNotice(`企业已创建 · #${record.enterprise_id}`);
      form.reset();
      page.refresh();
      onCreated();
    });
  }

  return <section className="platform-card">
    <h2>企业</h2>
    <form className="platform-form platform-form-inline" onSubmit={submit}>
      <label>企业名称<input name="name" maxLength={200} required /></label>
      <label>企业编码<input name="code" maxLength={100} required /></label>
      <label>容量配额（MB）<input name="quota" inputMode="numeric" pattern="[1-9][0-9]{0,18}" defaultValue="10240" required /></label>
      <button type="submit" className="primary-button" disabled={action.busy}>创建企业</button>
    </form>
    {action.error && <p className="operation-error" role="alert">{action.error}</p>}
    {notice && <p className="platform-feedback" role="status">{notice}</p>}
    {page.rows.length > 0 && <div className="platform-table-wrap">
      <table className="platform-table" aria-label="企业列表">
        <thead><tr><th>编号</th><th>名称</th><th>编码</th><th>容量配额（MB）</th><th>状态</th></tr></thead>
        <tbody>{page.rows.map((row) => <tr key={row.id}>
          <td>{row.id}</td><td>{row.name}</td><td>{row.code}</td><td>{row.storage_quota_mb}</td>
          <td>{row.status === "ACTIVE" ? "启用" : row.status}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    <Pagination page={page} />
  </section>;
}

function Invites({ token, onExpired, enterprises }) {
  const page = usePage("/admin/invite-codes", token, onExpired);
  const action = useAction(onExpired);
  const [notice, setNotice] = useState("");
  const names = new Map(enterprises.map((row) => [row.id, row.name]));

  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const expiry = new Date(values.get("expires_at"));
    setNotice("");
    void action.run((signal) => {
      if (!Number.isFinite(expiry.getTime()) || expiry <= new Date()) throw new Error("请选择未来的过期时间。");
      const data = {
        enterprise_id: values.get("enterprise_id"), code: values.get("code"),
        max_uses: Number(values.get("max_uses")), expires_at: expiry.toISOString(),
      };
      return platformRequest("/admin/invite-codes", token, { data, signal });
    }, (record) => {
      setNotice(`邀请码已创建 · #${record.invite_id}`);
      form.reset();
      page.refresh();
    });
  }

  function revoke(id) {
    setNotice("");
    void action.run((signal) => platformRequest(`/admin/invite-codes/${id}`, token, { method: "DELETE", signal }), (record) => {
      setNotice(record.revoked ? `已停用邀请码 #${id}` : `邀请码 #${id} 已停用或不存在`);
      page.refresh();
    });
  }

  return <section className="platform-card">
    <h2>邀请码</h2>
    <form className="platform-form platform-form-inline" onSubmit={submit}>
      <label>归属企业<EnterpriseSelect name="enterprise_id" emptyLabel="选择企业" required
        enterprises={enterprises.filter((row) => row.status === "ACTIVE")} /></label>
      <label>邀请码<input name="code" type="password" autoComplete="new-password" maxLength={128} required /></label>
      <label>可用次数<input name="max_uses" type="number" min="1" max="2147483647" defaultValue="1" required /></label>
      <label>过期时间（本地）<input name="expires_at" type="datetime-local" required /></label>
      <button className="primary-button" type="submit" disabled={action.busy}>创建邀请码</button>
    </form>
    {action.error && <p className="operation-error" role="alert">{action.error}</p>}
    {notice && <p className="platform-feedback" role="status">{notice}</p>}
    {page.rows.length > 0 && <div className="platform-table-wrap">
      <table className="platform-table" aria-label="邀请码列表">
        <thead><tr><th>编号</th><th>企业</th><th>已用 / 可用总次数</th><th>过期时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{page.rows.map((row) => {
          const state = !row.is_active ? "已停用" : new Date(row.expires_at) <= new Date()
            ? "已过期" : row.used_count >= row.max_uses ? "已用尽" : "启用";
          return <tr key={row.id}>
            <td>{row.id}</td><td>{names.get(row.enterprise_id) ?? row.enterprise_id}</td>
            <td>{row.used_count} / {row.max_uses}</td><td>{dateLabel(row.expires_at)}</td><td>{state}</td>
            <td><button className="outline-button" type="button" aria-label={`停用邀请码 #${row.id}`}
              disabled={!row.is_active || action.busy} onClick={() => revoke(row.id)}>停用</button></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
    <Pagination page={page} />
  </section>;
}

function AuditLogs({ token, onExpired }) {
  const page = usePage("/admin/audit-logs", token, onExpired);
  return <section className="platform-card">
    <div className="platform-heading">
      <h2>审计日志</h2>
      <button type="button" className="outline-button" onClick={page.refresh}>刷新日志</button>
    </div>
    {page.rows.length > 0 && <div className="platform-table-wrap">
      <table className="platform-table" aria-label="只读审计日志">
        <thead><tr><th>事件编号</th><th>发生时间</th><th>操作者编号</th><th>操作</th><th>目标类型</th><th>目标编号</th></tr></thead>
        <tbody>{page.rows.map((row) => <tr key={row.id}>
          <td>{row.id}</td><td>{dateLabel(row.created_at)}</td><td>{row.actor_id ?? "—"}</td>
          <td><code>{row.action}</code></td><td>{row.target_type}</td><td>{row.target_id ?? "—"}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    <Pagination page={page} />
  </section>;
}

/** 挂载系统管理员的模型、规则、企业、邀请码与审计页面。 */
export function Administration({ token, onExpired }) {
  const [tab, setTab] = useState("models");
  const [revision, setRevision] = useState(0);
  const enterprise = useEnterprises(token, onExpired, revision);
  const props = { token, onExpired, enterprises: enterprise.rows };
  return <div className="platform-stack">
    <nav className="admin-tabs" aria-label="管理后台导航">
      {tabs.map(([key, label]) => <button key={key} type="button" aria-current={tab === key ? "page" : undefined}
        onClick={() => setTab(key)}>{label}</button>)}
    </nav>
    {enterprise.error && <p className="operation-error" role="alert">企业选项加载失败。{enterprise.error}</p>}
    {tab === "models" && <ModelLedger admin {...props} />}
    {tab === "rules" && <RulesPage {...props} />}
    {tab === "enterprises" && <Enterprises {...props} onCreated={() => setRevision((value) => value + 1)} />}
    {tab === "invites" && <Invites {...props} />}
    {tab === "audit" && <AuditLogs {...props} />}
  </div>;
}

/** 允许试用账号使用邀请码完成绑定。成功后由上层作废当前页面登录态。 */
export function BindEnterprise({ token, onExpired, onBound }) {
  const action = useAction(onExpired);
  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = { code: new FormData(form).get("code") };
    void action.run((signal) => platformRequest("/auth/bind-invite", token, { data, signal }), () => {
      form.reset();
      onBound();
    });
  }

  return <section className="platform-card">
    <h2>绑定企业</h2>
    <form className="platform-form platform-form-inline" onSubmit={submit}>
      <label>企业邀请码<input name="code" type="password" autoComplete="off" maxLength={128} required /></label>
      <button type="submit" className="primary-button" disabled={action.busy}>确认绑定</button>
    </form>
    {action.error && <p className="operation-error" role="alert">{action.error}</p>}
  </section>;
}
