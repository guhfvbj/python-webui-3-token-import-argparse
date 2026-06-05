const helpModal = document.getElementById("helpModal");
const helpBackdrop = document.getElementById("helpBackdrop");
const openHelp = document.getElementById("openHelp");
const closeHelp = document.getElementById("closeHelp");
const copySnippet = document.getElementById("copySnippet");
const consoleSnippet = document.getElementById("consoleSnippet");
const confettiCanvas = document.getElementById("confettiCanvas");
const CONSOLE_SNIPPET_TEXT = String.raw`await(async()=>{const t=await new Promise((ok,err)=>{const r=indexedDB.open("localforage");r.onerror=()=>err(r.error);r.onsuccess=()=>{const db=r.result;if(!db.objectStoreNames.contains("keyvaluepairs"))return ok(null);const g=db.transaction("keyvaluepairs","readonly").objectStore("keyvaluepairs").get("home:token");g.onerror=()=>err(g.error);g.onsuccess=()=>ok(g.result||null)}});if(!t){console.error("没有读到 token");return null}let box=document.getElementById("__token_box__");if(!box){box=document.createElement("textarea");box.id="__token_box__";box.style.cssText="position:fixed;z-index:999999;right:20px;top:20px;width:520px;height:160px;padding:12px;font-size:14px;line-height:1.5;background:#fff;color:#000;border:3px solid red;box-shadow:0 0 20px #999;";document.body.appendChild(box)}box.value=t;box.focus();box.select();const copied=document.execCommand("copy");console.log("token =",t);console.log(copied?"已复制 token 到剪贴板":"复制失败，请从页面右上角文本框手动复制");return t})()`;
const requestForm = document.getElementById("requestForm");
const runButton = document.getElementById("runButton");
const pauseButton = document.getElementById("pauseButton");
const pauseButtonLabel = pauseButton ? pauseButton.querySelector("span") : null;
const logBox = document.getElementById("logBox");
const statusPill = document.getElementById("statusPill");
const summaryGrid = document.getElementById("summaryGrid");
const CLIENT_ID_KEY = "requestTesterClientId";
let pollTimer = null;
let activeJobId = "";
let activeJobTarget = "";
let lastRunPointer = null;

const confetti = (() => {
  if (!confettiCanvas) return null;
  const context = confettiCanvas.getContext("2d");
  if (!context) return null;

  const colors = ["#1a73e8", "#ea4335", "#fbbc04", "#4285f4"];
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let particles = [];
  let animationId = 0;

  function resize() {
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    confettiCanvas.width = Math.floor(window.innerWidth * dpr);
    confettiCanvas.height = Math.floor(window.innerHeight * dpr);
    confettiCanvas.style.width = `${window.innerWidth}px`;
    confettiCanvas.style.height = `${window.innerHeight}px`;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function makeParticle(x, y) {
    const angle = Math.random() * Math.PI * 2;
    const speed = Math.random() * 3.3 + 1.4;
    return {
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 1.5,
      size: Math.random() * 5 + 3,
      color: colors[Math.floor(Math.random() * colors.length)],
      opacity: 1,
      rotation: Math.random() * Math.PI,
      spin: Math.random() * 0.16 - 0.08,
      gravity: 0.075,
      friction: 0.965,
      shape: Math.random() > 0.72 ? "circle" : "rect",
    };
  }

  function drawParticle(particle) {
    context.save();
    context.globalAlpha = Math.max(0, particle.opacity);
    context.translate(particle.x, particle.y);
    context.rotate(particle.rotation);
    context.fillStyle = particle.color;
    if (particle.shape === "circle") {
      context.beginPath();
      context.arc(0, 0, particle.size * 0.42, 0, Math.PI * 2);
      context.fill();
    } else {
      context.fillRect(-particle.size / 2, -particle.size / 2, particle.size * 1.35, particle.size * 0.72);
    }
    context.restore();
  }

  function tick() {
    context.clearRect(0, 0, window.innerWidth, window.innerHeight);
    particles = particles.filter((particle) => particle.opacity > 0 && particle.y < window.innerHeight + 80);
    for (const particle of particles) {
      particle.vx *= particle.friction;
      particle.vy *= particle.friction;
      particle.vy += particle.gravity;
      particle.x += particle.vx;
      particle.y += particle.vy;
      particle.rotation += particle.spin;
      particle.opacity -= 0.012;
      drawParticle(particle);
    }
    if (particles.length) {
      animationId = window.requestAnimationFrame(tick);
    } else {
      animationId = 0;
    }
  }

  function burst(x, y) {
    if (prefersReducedMotion.matches) return;
    resize();
    for (let i = 0; i < 72; i += 1) {
      particles.push(makeParticle(x, y));
    }
    if (!animationId) {
      animationId = window.requestAnimationFrame(tick);
    }
  }

  resize();
  window.addEventListener("resize", resize);
  return { burst };
})();

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
  if (!helpModal) return;
  helpModal.classList.remove("hidden");
  helpBackdrop?.classList.remove("hidden");
  document.body.classList.add("help-open");
  openHelp?.setAttribute("aria-expanded", "true");
}

function closeHelpModal() {
  if (!helpModal) return;
  helpModal.classList.add("hidden");
  helpBackdrop?.classList.add("hidden");
  document.body.classList.remove("help-open");
  openHelp?.setAttribute("aria-expanded", "false");
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
  const label = job && job.status === "pause_requested" ? "停止中" : "停止";
  if (pauseButtonLabel) {
    pauseButtonLabel.textContent = label;
  } else {
    pauseButton.textContent = label;
  }
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

openHelp?.addEventListener("click", () => {
  if (!helpModal) return;
  if (helpModal.classList.contains("hidden")) {
    openHelpModal();
  } else {
    closeHelpModal();
  }
});
closeHelp?.addEventListener("click", closeHelpModal);
helpBackdrop?.addEventListener("click", closeHelpModal);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && helpModal && !helpModal.classList.contains("hidden")) {
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
    if (pauseButtonLabel) {
      pauseButtonLabel.textContent = "停止中";
    } else {
      pauseButton.textContent = "停止中";
    }
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

if (runButton) {
  runButton.addEventListener("pointerdown", (event) => {
    lastRunPointer = { x: event.clientX, y: event.clientY };
  });
}

requestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const buttonRect = runButton.getBoundingClientRect();
  const burstX = lastRunPointer ? lastRunPointer.x : buttonRect.left + buttonRect.width / 2;
  const burstY = lastRunPointer ? lastRunPointer.y : buttonRect.top + buttonRect.height / 2;
  confetti?.burst(burstX, burstY);
  lastRunPointer = null;

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
