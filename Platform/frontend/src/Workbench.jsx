// 协调登录、上传、任务轮询、查看结果与选中回传。令牌和结果只保留在当前页面内存中。
import React, { useCallback, useEffect, useRef, useState } from "react";

import { login, pollJob, selectModel, uploadStep } from "./api.js";
import { Breakdown, Modal, Results } from "./results.jsx";
import { Viewer } from "./Viewer.jsx";

const idle = { phase: "idle" };
const boundary = "相似度评分与排名仅供检索与几何/属性对比参考；能否用于实际工程采用或替代，须由工程负责人结合有效图纸与工况资料确认。";
const roles = { SYS_ADMIN: "系统管理员", ENT_ADMIN: "企业管理员", ENT_USER: "企业用户", TRIAL_USER: "试用用户" };

function interval(ms, signal) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", stop);
      resolve();
    }, ms);
    function stop() {
      clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener("abort", stop, { once: true });
  });
}

function SignIn({ onSession, onClose }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(null);
  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event) {
    event.preventDefault();
    if (pending.current) return;
    const values = new FormData(event.currentTarget);
    const request = new AbortController();
    pending.current = request;
    setBusy(true);
    setError("");
    try {
      const data = await login(values.get("username").trim(), values.get("password"), request.signal);
      if (!request.signal.aborted) onSession(data);
    } catch (failure) {
      if (!request.signal.aborted) setError(failure.message);
    } finally {
      if (!request.signal.aborted) setBusy(false);
      pending.current = null;
    }
  }

  return <Modal title="登录工作台" onClose={onClose}>
    <form className="signin-form" onSubmit={submit}>
      <label>用户名<input name="username" required maxLength={150} autoComplete="username" autoFocus /></label>
      <label>密码<input name="password" type="password" required maxLength={1024} autoComplete="current-password" /></label>
      {error && <p className="operation-error" role="alert">{error}</p>}
      <button className="primary-button" type="submit" disabled={busy}>{busy ? "正在登录…" : "登录"}</button>
    </form>
  </Modal>;
}

function describeFile(file) {
  const megabytes = file.size / 1024 ** 2;
  return megabytes >= 1 ? megabytes.toFixed(1) + " MB" : Math.max(1, Math.round(file.size / 1024)) + " KB";
}

function Upload({ file, run, signedIn, busy, onFile, onSubmit, onResume }) {
  return <section className="side-card" aria-labelledby="upload-title">
    <div className="section-heading"><h2 id="upload-title">提交查询件</h2></div>
    <form className="upload-form" onSubmit={onSubmit}>
      <label className="file-drop">
        <input type="file" accept=".stp,.step" disabled={busy} aria-label="选择 STEP 文件"
          onChange={(event) => {
            if (event.target.files.length) onFile(event.target.files[0]);
            event.target.value = "";
          }} />
        <span className="file-action">选择 STEP 文件</span>
        <span className="file-caption">{file ? "更换查询件" : "支持 .stp 与 .step"}</span>
      </label>
      {file && <div className="file-chip"><span title={file.name}>{file.name}</span><span>{describeFile(file)}</span></div>}
      <button className="primary-button" type="submit" disabled={!file || busy}>
        {busy ? "对比进行中…" : signedIn ? "开始对比" : "登录以继续"}
      </button>
    </form>
    {busy && <div className="job-status" role="status">
      <progress aria-label="对比任务进行中" />
      <span>{{ uploading: "正在上传查询件…", QUEUED: "任务已入队，等待计算", RUNNING: "正在对比模型" }[run.phase]}</span>
    </div>}
    {run.phase === "error" && <div className="job-status">
      <p className="operation-error" role="alert">{run.message}</p>
      {run.resume && <button type="button" className="outline-button" onClick={onResume}>继续查询此任务</button>}
    </div>}
    {run.phase === "DONE" && <p className="job-complete" role="status">对比完成<span>任务 {run.job}</span></p>}
  </section>;
}

/** 挂载可登录并执行 STEP 检索的工作台。后台 API 通过同源 /api 访问。 */
export function Workbench() {
  const [session, setSession] = useState(null);
  const [signingIn, setSigningIn] = useState(false);
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState(null);
  const [run, setRun] = useState(idle);
  const [active, setActive] = useState(null);
  const [details, setDetails] = useState(null);
  const [recorded, setRecorded] = useState({});
  const [selectionError, setSelectionError] = useState("");
  const activity = useRef(null);
  const busy = ["uploading", "QUEUED", "RUNNING"].includes(run.phase);
  const result = run.phase === "DONE" ? run.result : null;

  const signOut = useCallback((message) => {
    activity.current?.abort();
    activity.current = null;
    setSession(null);
    setFile(null);
    setRun(idle);
    setActive(null);
    setDetails(null);
    setRecorded({});
    setSelectionError("");
    setNotice(message);
  }, []);
  const expire = useCallback(() => signOut("登录已失效，请重新登录。"), [signOut]);

  useEffect(() => () => activity.current?.abort(), []);
  useEffect(() => {
    if (!Number.isFinite(session?.expires_in)) return;
    const clock = setTimeout(expire, session.expires_in * 1000);
    return () => clearTimeout(clock);
  }, [session, expire]);

  async function compare(resumingJob, filename) {
    activity.current?.abort();
    const controller = new AbortController();
    activity.current = controller;
    const { signal } = controller;
    const token = session.access_token;
    let job = resumingJob;
    setActive(null);
    setDetails(null);
    setRecorded({});
    setSelectionError("");
    setRun({ phase: job ? "QUEUED" : "uploading", filename });
    try {
      let snapshot = job ? await pollJob(token, job, signal) : await uploadStep(token, file, signal);
      job = snapshot.job_id;
      while (snapshot.status === "QUEUED" || snapshot.status === "RUNNING") {
        if (!job) throw new Error("服务未返回任务编号。");
        signal.throwIfAborted();
        setRun({ phase: snapshot.status, job, filename });
        await interval(snapshot.delay, signal);
        snapshot = await pollJob(token, job, signal);
      }
      signal.throwIfAborted();
      if (snapshot.status !== "DONE" || !job) throw new Error("服务返回了未知任务状态。");
      setRun({ phase: "DONE", job, filename, result: snapshot });
      setActive(snapshot.candidates?.[0] ?? null);
    } catch (error) {
      if (signal.aborted) return;
      if (error.status === 401) expire();
      else setRun({ phase: "error", job, filename, message: error.message, resume: Boolean(job && !error.terminal) });
    }
  }

  function begin(event) {
    event.preventDefault();
    if (busy || !file) return;
    if (!session) {
      setSigningIn(true);
      return;
    }
    if (!/\.(stp|step)$/i.test(file.name) || !file.size) {
      setRun({ phase: "error", message: "请选择一个非空 .stp 或 .step 文件。" });
      return;
    }
    void compare(null, file.name);
  }

  async function choose(item) {
    setActive(item);
    setSelectionError("");
    if (recorded[item.model_id]) return;
    const signal = activity.current?.signal;
    if (!signal || signal.aborted || !session) return;
    try {
      await selectModel(session.access_token, run.job, item.model_id, signal);
      if (!signal.aborted) setRecorded((previous) => ({ ...previous, [item.model_id]: true }));
    } catch (error) {
      if (signal.aborted) return;
      if (error.status === 401) expire();
      else setSelectionError("选中记录失败。" + error.message);
    }
  }

  return <>
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand"><div className="brand-mark" aria-hidden="true"><i /><i /><i /></div>
          <h1>STEP 模型对比工作台</h1>
        </div>
        <div className="session-actions">
          {session && <span className="role">{roles[session.role] ?? "已登录"}</span>}
          <button type="button" className="header-button"
            onClick={() => session ? signOut("") : setSigningIn(true)}>{session ? "退出登录" : "登录"}</button>
        </div>
      </div>
    </header>
    <div className="boundary"><div className="boundary-inner"><strong>工程边界</strong><span>{boundary}</span></div></div>
    <main className="page">
      {notice && <p className="session-notice" role="status">{notice}</p>}
      <div className="layout">
        <aside className="sidebar" aria-label="查询和结果">
          <Upload file={file} run={run} busy={busy} signedIn={Boolean(session)} onFile={setFile} onSubmit={begin}
            onResume={() => void compare(run.job, run.filename)} />
          <Results result={result} active={active} selected={recorded} onSelect={choose} onDetails={setDetails} />
          {selectionError && <p className="operation-error" role="alert">{selectionError}</p>}
        </aside>
        <Viewer token={session?.access_token} job={result?.job_id}
          fileName={run.filename} candidate={active} onExpired={expire} />
      </div>
    </main>
    {signingIn && <SignIn onClose={() => setSigningIn(false)} onSession={(data) => {
      setSession(data);
      setNotice("");
      setSigningIn(false);
    }} />}
    {details && <Breakdown candidate={details} onClose={() => setDetails(null)} />}
  </>;
}
