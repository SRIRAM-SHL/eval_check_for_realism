const $ = (id) => document.getElementById(id);

const state = {
  file: null,
  jdFile: null,
  role: null,
  results: [],
  aggregate: null,
  filenameStem: "evaluation_25",
};

const dropzone = $("dropzone");
const fileInput = $("fileInput");
const fileList = $("fileList");
const parsedCard = $("parsedCard");
const evaluateBtn = $("evaluateBtn");
const progressWrap = $("progressWrap");
const progressFill = $("progressFill");
const progressText = $("progressText");
const errorBox = $("errorBox");
const emptyState = $("emptyState");
const summary = $("summary");
const qaList = $("qaList");
const resultsTitle = $("resultsTitle");
const resultsMeta = $("resultsMeta");
const exportJsonBtn = $("exportJsonBtn");
const exportCsvBtn = $("exportCsvBtn");

// ---------- health ----------
(async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    $("modelName").textContent = (d.model || "").replace(/^openai\//, "");
    const pill = $("llmStatus");
    if (d.llm_configured) { pill.textContent = "ready"; pill.className = "pill ok"; }
    else { pill.textContent = "no API key"; pill.className = "pill bad"; }
  } catch {
    $("llmStatus").textContent = "offline"; $("llmStatus").className = "pill bad";
  }
})();

function setStep(n) {
  document.querySelectorAll(".step").forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
  });
}
setStep(1);

// ---------- file handling (single file) ----------
dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { setFile(fileInput.files[0]); fileInput.value = ""; });
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
dropzone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

function setFile(f) {
  if (!f) return;
  if (!/\.(txt|md|csv)$/i.test(f.name)) { showError("Please upload a .txt, .md or .csv file."); return; }
  hideError();
  state.file = f;
  state.filenameStem = f.name.replace(/\.[^.]+$/, "").replace(/[^\w\- ]/g, "").trim() || "evaluation_25";
  renderFileList();
  previewCount(f);
}

function renderFileList() {
  fileList.innerHTML = "";
  if (!state.file) return;
  const li = document.createElement("li");
  li.className = "file-chip";
  const icon = document.createElement("span"); icon.className = "fc-icon"; icon.textContent = "TXT";
  const name = document.createElement("span"); name.className = "fc-name"; name.textContent = state.file.name;
  const size = document.createElement("span"); size.className = "fc-size"; size.textContent = `${(state.file.size/1024).toFixed(1)} KB`;
  const clear = document.createElement("button"); clear.className = "fc-clear"; clear.textContent = "X";
  clear.addEventListener("click", (e) => { e.stopPropagation(); state.file = null; renderFileList(); parsedCard.classList.add("hidden"); evaluateBtn.disabled = true; });
  li.append(icon, name, size, clear);
  fileList.appendChild(li);
  evaluateBtn.disabled = false;
  setStep(2);
}

// ---------- JD upload (optional, role-aware) ----------
const jdDropzone = $("jdDropzone");
const jdInput = $("jdInput");
const jdList = $("jdList");
const roleCard = $("roleCard");

jdDropzone.addEventListener("click", () => jdInput.click());
jdInput.addEventListener("change", () => { setJd(jdInput.files[0]); jdInput.value = ""; });
["dragover", "dragenter"].forEach((ev) =>
  jdDropzone.addEventListener(ev, (e) => { e.preventDefault(); jdDropzone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((ev) =>
  jdDropzone.addEventListener(ev, (e) => { e.preventDefault(); jdDropzone.classList.remove("dragover"); }));
jdDropzone.addEventListener("drop", (e) => setJd(e.dataTransfer.files[0]));

function setJd(f) {
  if (!f) return;
  if (!/\.(docx|pdf|txt|md)$/i.test(f.name)) { showError("JD must be .docx, .pdf, .txt or .md."); return; }
  hideError();
  state.jdFile = f;
  renderJdList();
  detectRole(f);
}

function renderJdList() {
  jdList.innerHTML = "";
  if (!state.jdFile) return;
  const li = document.createElement("li");
  li.className = "file-chip";
  const icon = document.createElement("span"); icon.className = "fc-icon"; icon.textContent = "JD";
  const name = document.createElement("span"); name.className = "fc-name"; name.textContent = state.jdFile.name;
  const clear = document.createElement("button"); clear.className = "fc-clear"; clear.textContent = "X";
  clear.addEventListener("click", (e) => {
    e.stopPropagation(); state.jdFile = null; state.role = null;
    renderJdList(); roleCard.classList.add("hidden");
  });
  li.append(icon, name, clear);
  jdList.appendChild(li);
}

async function detectRole(f) {
  $("roleName").textContent = "detecting";
  $("roleFocus").textContent = "";
  roleCard.classList.remove("hidden");
  const fd = new FormData();
  fd.append("jd_file", f);
  try {
    const r = await fetch("/api/extract_role", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "Role detection failed.");
    state.role = d.role;
    if (d.role && d.role.role) {
      const sen = d.role.seniority ? `  ${d.role.seniority}` : "";
      $("roleName").textContent = d.role.role + sen;
      $("roleFocus").textContent = d.role.focus || "";
      toast(`Role detected: ${d.role.role}`);
    } else {
      $("roleName").textContent = "no clear role found";
      $("roleFocus").textContent = "Judging will stay generic.";
    }
  } catch (err) {
    $("roleName").textContent = "detection failed";
    $("roleFocus").textContent = "";
    showError(err.message);
  }
}

// Client-side quick count so the user sees how many questions were detected.
async function previewCount(f) {
  try {
    const text = await f.text();
    const n = countQuestions(text);
    $("parsedCount").textContent = n;
    parsedCard.classList.remove("hidden");
  } catch { /* ignore */ }
}

function countQuestions(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const numbered = lines.filter((l) => /^\s*\d+\s*[\.\):\-]\s+/.test(l) && !/^\s*(evidence|source|answer)\s*[:\-]/i.test(l));
  if (numbered.length) return numbered.length;
  const bullets = lines.filter((l) => /^\s*[\-\*\u2022]\s+/.test(l));
  if (bullets.length) return bullets.length;
  return lines.filter((l) => l.trim() && !/^\s*(evidence|source|answer)\s*[:\-]/i.test(l)).length;
}

// ---------- evaluate (SSE) ----------
evaluateBtn.addEventListener("click", evaluate);

function setLoading(on) {
  evaluateBtn.disabled = on || !state.file;
  evaluateBtn.querySelector(".btn-label").textContent = on ? "Evaluating..." : "Evaluate questions";
  evaluateBtn.querySelector(".spinner").classList.toggle("hidden", !on);
}

const INTENT_NONE = "Other";

async function evaluate() {
  if (!state.file) return;
  hideError();
  setLoading(true);
  emptyState.classList.add("hidden");
  summary.classList.add("hidden");
  qaList.innerHTML = "";
  exportJsonBtn.classList.add("hidden");
  exportCsvBtn.classList.add("hidden");
  progressWrap.classList.remove("hidden");
  progressFill.style.width = "0%";
  progressText.textContent = "Starting...";
  resultsMeta.textContent = "Judging each question...";

  const fd = new FormData();
  fd.append("file", state.file);
  if (state.jdFile) fd.append("jd_file", state.jdFile);

  try {
    const resp = await fetch("/api/evaluate", { method: "POST", body: fd });
    if (!resp.ok || !resp.body) {
      let detail = "Evaluation failed.";
      try { detail = (await resp.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let total = 0;
    let finished = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sep).trim();
        buffer = buffer.slice(sep + 2);
        if (!chunk.startsWith("data:")) continue;
        const ev = JSON.parse(chunk.slice(5).trim());

        if (ev.stage === "start") {
          total = ev.total;
          renderPending(ev.questions);
        } else if (ev.stage === "result") {
          fillResult(ev.index, ev.question, ev.verdict);
          const pct = Math.round((ev.done / total) * 100);
          progressFill.style.width = pct + "%";
          progressText.textContent = `Judged ${ev.done} / ${total}`;
        } else if (ev.stage === "error") {
          throw new Error(ev.detail || "Evaluation failed.");
        } else if (ev.stage === "done") {
          finished = true;
          state.results = ev.results;
          state.aggregate = ev.aggregate;
          renderSummary(ev.aggregate, ev.count);
          resultsTitle.textContent = "Evaluation";
          const roleTxt = (ev.role && ev.role.role) ? ` · role-aware: ${ev.role.role}` : "";
          resultsMeta.textContent = `${ev.count} questions judged${roleTxt}`;
          exportJsonBtn.classList.remove("hidden");
          exportCsvBtn.classList.remove("hidden");
          setStep(3);
          toast(`Evaluated ${ev.count} questions`);
        }
      }
    }
    if (!finished) throw new Error("Stream ended before completion.");
  } catch (err) {
    showError(err.message);
    resultsMeta.textContent = "Your evaluation will appear here.";
    if (!state.results.length) emptyState.classList.remove("hidden");
  } finally {
    setLoading(false);
    progressWrap.classList.add("hidden");
  }
}

function renderPending(questions) {
  qaList.innerHTML = "";
  questions.forEach((q) => {
    const li = document.createElement("li");
    li.className = "qa-item pending";
    li.id = `q-${q.index}`;
    li.innerHTML = `
      <div class="q"><span class="num">${q.index + 1}</span><span class="qtext"></span></div>
      <div class="score-row"><span class="mini-spinner"></span></div>`;
    li.querySelector(".qtext").textContent = q.question;
    qaList.appendChild(li);
  });
}

function scoreClass(v) { return v >= 0.7 ? "good" : v >= 0.4 ? "mid" : "bad"; }

function fillResult(index, question, v) {
  const li = $(`q-${index}`);
  if (!li) return;
  li.classList.remove("pending");
  const realism = typeof v.realism === "number" ? v.realism : null;
  const fit = typeof v.intent_fit === "number" ? v.intent_fit : null;

  const flags = (v.flags || []).map((f) => `<span class="flag-tag">${f}</span>`).join(" ");
  const badge = realism === null
    ? `<span class="score-badge bad">error</span>`
    : `<span class="score-badge ${scoreClass(realism)}">realism ${realism.toFixed(2)}</span>`;
  const intent = `<span class="intent-tag">${v.best_intent || INTENT_NONE}</span>`;
  const fitNote = fit === null ? "" : `<span class="fit-note">fit ${fit.toFixed(2)}</span>`;

  li.innerHTML = `
    <div class="q"><span class="num">${index + 1}</span><span class="qtext"></span></div>
    <div class="score-row">${badge}${intent}${fitNote}${flags}</div>
    <div class="reason"></div>`;
  li.querySelector(".qtext").textContent = question;
  li.querySelector(".reason").textContent = v.reason || "";
}

function renderSummary(agg, count) {
  if (!agg) return;
  const buckets = agg.realism_buckets || {};
  const intents = agg.intent_distribution || {};
  const flags = agg.flag_counts || {};

  const intentChips = Object.entries(intents)
    .map(([k, n]) => `<span class="chip">${k}: ${n}</span>`).join(" ");
  const flagChips = Object.keys(flags).length
    ? Object.entries(flags).map(([k, n]) => `<span class="chip flag">${k}: ${n}</span>`).join(" ")
    : `<span class="chip">none</span>`;

  summary.innerHTML = `
    <div class="metric"><span class="m-num">${count}</span><span class="m-label">questions</span></div>
    <div class="metric"><span class="m-num">${fmt(agg.avg_realism)}</span><span class="m-label">avg realism</span></div>
    <div class="metric"><span class="m-num">${fmt(agg.avg_intent_fit)}</span><span class="m-label">avg intent fit</span></div>
    <div class="metric"><span class="m-num">${agg.flagged_questions || 0}</span><span class="m-label">flagged</span></div>
    <div class="metric wide">
      <span class="m-label">Realism spread</span>
      <div class="chips">
        <span class="chip">high &ge;0.7: ${buckets["high_>=0.7"] || 0}</span>
        <span class="chip">mid: ${buckets["mid_0.4-0.7"] || 0}</span>
        <span class="chip flag">low &lt;0.4: ${buckets["low_<0.4"] || 0}</span>
      </div>
    </div>
    <div class="metric wide"><span class="m-label">Intent distribution</span><div class="chips">${intentChips}</div></div>
    <div class="metric wide"><span class="m-label">Flags</span><div class="chips">${flagChips}</div></div>`;
  summary.classList.remove("hidden");
}

function fmt(v) { return (typeof v === "number") ? v.toFixed(2) : ""; }

// ---------- export ----------
exportJsonBtn.addEventListener("click", () => doExport("json"));
exportCsvBtn.addEventListener("click", () => doExport("csv"));
async function doExport(format) {
  const r = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: `eval_${state.filenameStem}`, format, results: state.results, aggregate: state.aggregate }),
  });
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `eval_${state.filenameStem}.${format}`;
  a.click(); URL.revokeObjectURL(url);
  toast(`Downloaded .${format}`);
}

// ---------- helpers ----------
function showError(msg) { errorBox.textContent = msg; errorBox.classList.remove("hidden"); }
function hideError() { errorBox.classList.add("hidden"); }
let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}
