const helpModal = document.getElementById("helpModal");
const openHelp = document.getElementById("openHelp");
const closeHelp = document.getElementById("closeHelp");
const copySnippet = document.getElementById("copySnippet");
const consoleSnippet = document.getElementById("consoleSnippet");
const CONSOLE_SNIPPET_TEXT = String.raw`await(async()=>{const t=await new Promise((ok,err)=>{const r=indexedDB.open("localforage");r.onerror=()=>err(r.error);r.onsuccess=()=>{const db=r.result;if(!db.objectStoreNames.contains("keyvaluepairs"))return ok(null);const g=db.transaction("keyvaluepairs","readonly").objectStore("keyvaluepairs").get("home:token");g.onerror=()=>err(g.error);g.onsuccess=()=>ok(g.result||null)}});if(!t){console.error("没有读到 token");return null}let box=document.getElementById("__token_box__");if(!box){box=document.createElement("textarea");box.id="__token_box__";box.style.cssText="position:fixed;z-index:999999;right:20px;top:20px;width:520px;height:160px;padding:12px;font-size:14px;line-height:1.5;background:#fff;color:#000;border:3px solid red;box-shadow:0 0 20px #999;";document.body.appendChild(box)}box.value=t;box.focus();box.select();const copied=document.execCommand("copy");console.log("token =",t);console.log(copied?"已复制 token 到剪贴板":"复制失败，请从页面右上角文本框手动复制");return t})()`;
const requestForm = document.getElementById("requestForm");
const runButton = document.getElementById("runButton");
const pauseButton = document.getElementById("pauseButton");
const logBox = document.getElementById("logBox");
const statusPill = document.getElementById("statusPill");
const summaryGrid = document.getElementById("summaryGrid");
const CLIENT_ID_KEY = "requestTesterClientId";
let pollTimer = null;
let activeJobId = "";
let activeJobTarget = "";

if (consoleSnippet) {
  if ("value" in consoleSnippet) {
    consoleSnippet.value = CONSOLE_SNIPPET_TEXT;
  }
  consoleSnippet.textContent = CONSOLE_SNIPPET_TEXT;
}

function setStatus(text, state) {
  statusPill.textContent = text;
  statusPill.className = `status-pill ${state || ""}`.trim();
}

function getClientId() {
  let clientId = localStorage.getItem(CLIENT_ID_KEY);
  if (!clientId) {
    clientId =
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem(CLIENT_ID_KEY, clientId);
  }
  return clientId;
}

function openHelpModal() {
  helpModal.classList.remove("hidden");
}

function closeHelpModal() {
  helpModal.classList.add("hidden");
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }

  const textArea = document.createElement("textarea");
  textArea.value = text;
  textArea.setAttribute("readonly", "");
  textArea.style.position = "fixed";
  textArea.style.left = "-9999px";
  document.body.appendChild(textArea);
  textArea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textArea);
  return copied;
}

function resetSummary() {
  summaryGrid.innerHTML = `
    <div><span>成功</span><strong>-</strong></div>
    <div><span>失败</span><strong>-</strong></div>
  `;
}

function writeLog(lines) {
  const nextLines = lines && lines.length ? lines : ["无日志"];
  logBox.textContent = nextLines.join("\n");
  logBox.scrollTop = logBox.scrollHeight;
}

function clearLog() {
  logBox.textContent = "";
}

function appendLogLine(line) {
  if (logBox.textContent) {
    logBox.textContent += "\n";
  }
  logBox.textContent += line;
  logBox.scrollTop = logBox.scrollHeight;
}

function renderSummary(data) {
  const successCount = data.success_count ?? 0;
  const failCount = data.fail_count ?? (data.ok ? 0 : 1);
  summaryGrid.innerHTML = `
    <div><span>成功</span><strong>${successCount}</strong></div>
    <div><span>失败</span><strong>${failCount}</strong></div>
  `;
}

function statusState(status) {
  if (status === "completed") return "success";
  if (status === "failed" || status === "interrupted") return "error";
  if (status === "queued" || status === "running" || status === "pause_requested") return "running";
  return "";
}

function isActiveStatus(status) {
  return status === "queued" || status === "running" || status === "pause_requested";
}

function canPauseJob(job) {
  return job && job.target === "smartedu_lmc" && (job.status === "queued" || job.status === "running");
}

function updatePauseButton(job) {
  if (!pauseButton) return;
  const enabled = canPauseJob(job);
  pauseButton.disabled = !enabled;
  pauseButton.textContent = job && job.status === "pause_requested" ? "停止中" : "停止";
}

function renderJob(job, mode) {
  const logs = Array.isArray(job.logs) ? job.logs : [];
  const statusText = job.status_label || job.status || "未知";
  const leadingLine =
    mode === "attached"
      ? "[HISTORY] 已载入历史日志，并连接到正在运行的任务。"
      : mode === "query"
        ? "[HISTORY] 已载入历史记录。"
        : "[PROCESS] 已连接当前任务。";
  const header = [
    leadingLine,
    `[JOB] id=${job.id}`,
    `[STATUS] ${statusText}`,
  ];
  if (job.course_url) header.push(`[COURSE] ${job.course_url}`);
  if (job.input_preview) header.push("[INPUT]\n" + job.input_preview);
  header.push("");
  writeLog(header.concat(logs.length ? logs : ["暂无日志。"]));
  renderSummary(job);
  setStatus(job.status_label || "运行中", statusState(job.status));
  activeJobTarget = job.target || "";
  updatePauseButton(job);
}

function stopPolling(clearJob = true) {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (!clearJob) return;
  activeJobId = "";
  activeJobTarget = "";
  updatePauseButton(null);
}

async function fetchJob(jobId, mode) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  const data = await response.json();
  if (!response.ok || !data.job) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  renderJob(data.job, mode || "process");
  if (!isActiveStatus(data.job.status)) {
    stopPolling();
  }
}

function startPolling(jobId, mode, target) {
  stopPolling(false);
  activeJobId = jobId;
  activeJobTarget = target || "";
  pollTimer = window.setInterval(() => {
    fetchJob(activeJobId, mode).catch((error) => {
      appendLogLine(`[ERROR] ${error.message}`);
      stopPolling();
      setStatus("失败", "error");
    });
  }, 4000);
}

function readForm() {
  return {
    token_or_link: document.getElementById("tokenOrLink").value.trim(),
    client_id: getClientId(),
  };
}

function validate(payload) {
  if (!payload.token_or_link) return "请输入链接";
  return "";
}

openHelp.addEventListener("click", openHelpModal);
closeHelp.addEventListener("click", closeHelpModal);
helpModal.addEventListener("click", (event) => {
  if (event.target === helpModal) closeHelpModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !helpModal.classList.contains("hidden")) {
    closeHelpModal();
  }
});

if (copySnippet && consoleSnippet) {
  copySnippet.addEventListener("click", async () => {
    const originalText = copySnippet.textContent;
    try {
      const snippetText = "value" in consoleSnippet ? consoleSnippet.value : consoleSnippet.textContent;
      const copied = await copyText(snippetText.trim());
      copySnippet.textContent = copied ? "已复制" : "复制失败";
      copySnippet.classList.toggle("copied", copied);
    } catch (error) {
      copySnippet.textContent = "复制失败";
      copySnippet.classList.remove("copied");
    }

    window.setTimeout(() => {
      copySnippet.textContent = originalText;
      copySnippet.classList.remove("copied");
    }, 1200);
  });
}

if (pauseButton) {
  pauseButton.addEventListener("click", async () => {
    if (!activeJobId || activeJobTarget !== "smartedu_lmc") {
      appendLogLine("[STOP] 当前没有可停止的第三门课程任务。");
      return;
    }

    pauseButton.disabled = true;
    pauseButton.textContent = "停止中";
    appendLogLine("[STOP] 正在发送停止请求...");

    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(activeJobId)}/stop`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok || !data.job) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      renderJob(data.job, "process");
      if (isActiveStatus(data.job.status)) {
        startPolling(data.job.id, "process", data.job.target);
      }
    } catch (error) {
      appendLogLine(`[ERROR] ${error.message}`);
      fetchJob(activeJobId, "process").catch(() => updatePauseButton(null));
    }
  });
}

requestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = readForm();
  const validationMessage = validate(payload);
  if (validationMessage) {
    setStatus("待补全", "error");
    writeLog([validationMessage]);
    return;
  }

  stopPolling();
  runButton.disabled = true;
  setStatus("运行中", "running");
  resetSummary();
  clearLog();
  appendLogLine("正在解析链接并连接后台任务...");

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (data.job) {
      renderJob(data.job, data.mode);
      if (isActiveStatus(data.job.status)) {
        startPolling(data.job.id, data.mode, data.job.target);
      }
      return;
    }

    const logs = data.logs && data.logs.length ? data.logs : [`[ERROR] ${data.error || `HTTP ${response.status}`}`];
    writeLog(logs);
    renderSummary(data);
    setStatus("失败", "error");
  } catch (error) {
    resetSummary();
    writeLog([`[ERROR] ${error.message}`]);
    setStatus("失败", "error");
  } finally {
    runButton.disabled = false;
  }
});
