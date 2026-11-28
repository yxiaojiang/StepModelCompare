// 共享台账分页、请求生命周期和企业选择控件。请求只在已登录视图内运行。
import React, { useEffect, useRef, useState } from "react";

import { platformRequest } from "./api.js";

const pageSize = 100;

/** 读取 path 的游标分页。切换筛选或页面时取消旧请求，401 交给登录态处理。 */
export function usePage(path, token, onExpired) {
  const [filters, setFilters] = useState({});
  const [history, setHistory] = useState(["0"]);
  const [revision, setRevision] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const afterId = history.at(-1);
  const current = snapshot?.filters === filters && snapshot.afterId === afterId && snapshot.revision === revision;
  const rows = current ? snapshot.rows : [];
  const [error, loading] = current ? [snapshot.error, false] : ["", true];

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    platformRequest(path, token, { query: { ...filters, after_id: afterId }, signal })
      .then((records) => {
        if (!signal.aborted) setSnapshot({ filters, afterId, revision, rows: records, error: "" });
      })
      .catch((failure) => {
        if (signal.aborted) return;
        if (failure.status === 401) onExpired();
        setSnapshot({ filters, afterId, revision, rows: [], error: failure.message });
      });
    return () => controller.abort();
  }, [path, token, filters, afterId, revision, onExpired]);

  return {
    rows, error, loading, number: history.length, hasNext: rows.length === pageSize,
    apply(query) { setFilters(query); setHistory(["0"]); },
    refresh() { setRevision((value) => value + 1); },
    back() { setHistory((previous) => previous.length > 1 ? previous.slice(0, -1) : previous); },
    next() {
      const cursor = rows.at(-1)?.id ?? rows.at(-1)?.rule_id;
      if (rows.length === pageSize) setHistory((previous) => [...previous, cursor]);
    },
  };
}

/** 执行一次表单或行操作，屏蔽重复提交，返回进行中状态、错误和操作入口。 */
export function useAction(onExpired) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const action = useRef(null);
  useEffect(() => () => action.current?.abort(), []);

  async function run(execute, onSuccess) {
    if (action.current) return;
    const controller = new AbortController();
    const { signal } = controller;
    action.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await execute(signal);
      if (!signal.aborted) onSuccess(result);
    } catch (failure) {
      if (!signal.aborted) {
        if (failure.status === 401) onExpired();
        else setError(failure.message);
      }
    } finally {
      action.current = null;
      if (!signal.aborted) setBusy(false);
    }
  }

  return { busy, error, run };
}

/** 按后台游标加载企业选项，刷新版本变化时重读；未就绪时返回空列表与状态。 */
export function useEnterprises(token, onExpired, revision = 0) {
  const [snapshot, setSnapshot] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      const rows = [];
      let afterId = "0";
      while (true) {
        const page = await platformRequest("/admin/enterprises", token, { query: { after_id: afterId }, signal });
        signal.throwIfAborted();
        rows.push(...page);
        if (page.length < pageSize) break;
        afterId = page.at(-1).id;
      }
      setSnapshot({ revision, rows, error: "" });
    }
    load().catch((failure) => {
      if (signal.aborted) return;
      if (failure.status === 401) onExpired();
      setSnapshot({ revision, rows: [], error: failure.message });
    });
    return () => controller.abort();
  }, [token, onExpired, revision]);
  return snapshot?.revision === revision ? snapshot : { rows: [], error: "", loading: true };
}

/** 渲染查询结果的错误、空状态或分页按钮。 */
export function Pagination({ page }) {
  return <>
    {page.error && <p className="operation-error" role="alert">{page.error}</p>}
    {page.loading && <p className="body-muted" role="status">正在加载…</p>}
    {!page.error && !page.loading && page.rows.length === 0 && <p className="body-muted">暂无记录。</p>}
    <div className="platform-pager">
      <button className="outline-button" type="button" onClick={page.back} disabled={page.number === 1}>上一页</button>
      <span>第 {page.number} 页</span>
      <button className="outline-button" type="button" onClick={page.next} disabled={!page.hasNext}>下一页</button>
    </div>
  </>;
}

/** 使用管理端企业记录创建选择器，传入空选项名称和标准 select 属性。 */
export function EnterpriseSelect({ enterprises, emptyLabel, ...props }) {
  return <select {...props}>
    <option value="">{emptyLabel}</option>
    {enterprises.map((row) => <option key={row.id} value={row.id}>{row.name} · {row.code}</option>)}
  </select>;
}

/** 格式化服务端 ISO 日期，空值和无效值显示占位。 */
export function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}
