/**
 * WCAG Audit Agent — Side Panel logic (WS-1, Anirudh)
 * ----------------------------------------------------
 * Consumes the IP-3 Report JSON (Strategy Doc §4.3) and renders all 5 tabs:
 * Audit, Fix Studio, Benchmark, News, Timeline.
 *
 * Diff rendering: a built-in token-level differ is included so the panel
 * works with zero dependencies. To upgrade to diff2html (Design §4.3),
 * download diff2html.min.js + css into extension/assets/vendor/ and load it
 * from panel.html — MV3 forbids loading it from a CDN.
 */

"use strict";

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

const tabs = document.querySelectorAll(".tab");
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", t === tab ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
    document.getElementById(`tab-${tab.dataset.tab}`).classList.remove("hidden");
  });
});

// ---------------------------------------------------------------------------
// State + status helpers
// ---------------------------------------------------------------------------

let currentReport = null;

const $ = (id) => document.getElementById(id);
const statusBar = $("status-bar");

function setStatus(text, isError = false) {
  if (!text) {
    statusBar.classList.add("hidden");
    return;
  }
  statusBar.textContent = text;
  statusBar.classList.toggle("error", isError);
  statusBar.classList.remove("hidden");
}

function escapeHTML(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Run audit
// ---------------------------------------------------------------------------

$("run-audit").addEventListener("click", async () => {
  const btn = $("run-audit");
  btn.disabled = true;
  setStatus("Extracting page and running audit… this can take a minute on first run.");

  try {
    const res = await chrome.runtime.sendMessage({
      type: "RUN_AUDIT",
      mock: $("mock-mode").checked,
    });
    if (!res || !res.ok) throw new Error(res ? res.error : "No response from background.");

    currentReport = res.report;
    setStatus("");
    renderReport(currentReport);
    await saveToTimeline(currentReport);
    await renderTimeline(currentReport.audit_metadata.url);
  } catch (e) {
    setStatus(`Audit failed: ${e.message}. Check that the backend URL is reachable, or switch on Mock.`, true);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Tab 1 — Audit Report
// ---------------------------------------------------------------------------

function impactClass(score) {
  if (score >= 70) return "high";
  if (score >= 40) return "med";
  return "low";
}

function renderReport(report) {
  $("audited-url").textContent = report.audit_metadata.url || "";
  $("report-disclaimer").textContent = report.disclaimer || "";

  const findings = report.findings || [];

  // Summary chips per disability class
  const counts = {};
  findings.forEach((f) => (counts[f.disability_class] = (counts[f.disability_class] || 0) + 1));
  const summary = $("audit-summary");
  summary.innerHTML =
    `<div class="summary-chip"><strong>${findings.length}</strong> findings</div>` +
    Object.entries(counts)
      .map(([cls, n]) => `<div class="summary-chip"><strong>${n}</strong> ${escapeHTML(cls.replace("_", " "))}</div>`)
      .join("");
  summary.classList.remove("hidden");

  // Findings list, highest impact first
  const list = $("findings-list");
  if (!findings.length) {
    list.innerHTML = `<div class="empty-state"><p><strong>No confirmed findings.</strong></p><p>The critique agent rejected all candidates, or the page passed every check in scope.</p></div>`;
    return;
  }

  list.innerHTML = "";
  [...findings]
    .sort((a, b) => (b.impact_score || 0) - (a.impact_score || 0))
    .forEach((f) => {
      const el = document.createElement("details");
      el.className = "finding";
      const needsReview = f.status === "needs_context";
      el.innerHTML = `
        <summary>
          <div class="finding-head">
            <span class="badge ${escapeHTML(f.disability_class)}">${escapeHTML(f.disability_class.replace("_", " "))}</span>
            <span class="badge neutral">WCAG ${escapeHTML(f.criterion_number)} · ${escapeHTML(f.criterion_level)}</span>
            ${needsReview ? '<span class="badge warn">Manual review</span>' : ""}
            <span class="impact ${impactClass(f.impact_score)}" title="Impact score">${f.impact_score ?? "—"}</span>
          </div>
          <span class="finding-title">${escapeHTML(f.finding_description)}</span>
          <span class="finding-criterion">${escapeHTML(f.element_selector || "")}</span>
        </summary>
        <div class="finding-body">
          <dl>
            <dt>Who this blocks</dt><dd>${escapeHTML(f.disability_impact || "—")}</dd>
            <dt>WCAG criterion (verbatim)</dt><dd>${escapeHTML(f.criterion_text || "—")}</dd>
            <dt>Element</dt><dd><code>${escapeHTML(f.element_selector || "—")}</code></dd>
            <dt>Confidence · Critique verdict</dt>
            <dd>${((f.confidence ?? 0) * 100).toFixed(0)}% · ${escapeHTML(f.critique_verdict || "—")}</dd>
          </dl>
          ${
            (f.legal_regulations || []).length
              ? `<div class="legal-tags">${f.legal_regulations.map((r) => `<span class="legal-tag">${escapeHTML(r)}</span>`).join("")}</div>`
              : ""
          }
          ${f.fix ? `<button class="btn-fix" data-id="${escapeHTML(f.id)}">Open in Fix Studio</button>` : ""}
        </div>`;
      list.appendChild(el);
    });

  list.querySelectorAll(".btn-fix").forEach((btn) =>
    btn.addEventListener("click", () => openFixStudio(btn.dataset.id))
  );

  renderBenchmark(report.benchmark);
  renderNews(report.news_preview);
}

// ---------------------------------------------------------------------------
// Tab 2 — Fix Studio (3-pane + diff + srcdoc preview)
// ---------------------------------------------------------------------------

/** Token-level HTML diff (built-in fallback for diff2html/jsdiff). */
function tokenDiff(a, b) {
  const tok = (s) => s.match(/<[^>]+>|[^<\s]+|\s+/g) || [];
  const A = tok(a), B = tok(b);
  // Simple LCS on tokens — fine at snippet scale.
  const m = A.length, n = B.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, out = "";
  while (i < m && j < n) {
    if (A[i] === B[j]) { out += escapeHTML(A[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out += `<span class="diff-del">${escapeHTML(A[i])}</span>`; i++; }
    else { out += `<span class="diff-add">${escapeHTML(B[j])}</span>`; j++; }
  }
  while (i < m) out += `<span class="diff-del">${escapeHTML(A[i++])}</span>`;
  while (j < n) out += `<span class="diff-add">${escapeHTML(B[j++])}</span>`;
  return out;
}

function openFixStudio(findingId) {
  const f = (currentReport?.findings || []).find((x) => x.id === findingId);
  if (!f || !f.fix) return;

  // Switch to the Fix tab
  document.querySelector('.tab[data-tab="fix"]').click();
  $("fix-empty").classList.add("hidden");
  $("fix-studio").classList.remove("hidden");

  $("fix-title").textContent = `${f.criterion_number} — ${f.finding_description}`;
  $("fix-original").textContent = f.fix.original_html || "(original snippet not provided)";
  $("fix-patched").textContent = f.fix.patch_html || "";

  const badge = $("fix-validated-badge");
  badge.textContent = f.fix.patch_validated ? "validated by axe-core" : "not validated";
  badge.className = `badge ${f.fix.patch_validated ? "ok" : "warn"}`;

  // Prefer the backend's diff2html output if present; else built-in differ.
  $("fix-diff").innerHTML = f.fix.diff_html
    ? f.fix.diff_html
    : tokenDiff(f.fix.original_html || "", f.fix.patch_html || "");

  // Sandboxed srcdoc preview (Design §4.4 + MV3 CSP risk note).
  $("fix-preview").srcdoc =
    f.fix.preview_srcdoc ||
    `<!DOCTYPE html><html><body style="font-family:sans-serif;padding:12px">${f.fix.patch_html || ""}</body></html>`;

  const note = $("fix-review-note");
  if (f.fix.requires_human_review) {
    note.textContent = `Human review required: ${f.fix.review_reason || "AI-generated content — confirm intent before applying."}`;
    note.classList.remove("hidden");
  } else {
    note.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// Tab 3 — Benchmark
// ---------------------------------------------------------------------------

function renderBenchmark(b) {
  const box = $("benchmark-content");
  if (!b) return;
  box.classList.remove("empty-state");
  box.innerHTML = `
    <table class="bench-table">
      <caption class="hidden">Finding counts by tool</caption>
      <tr><th scope="col">Tool</th><th scope="col">Findings</th></tr>
      <tr><td class="bench-ours">WCAG Audit Agent (ours)</td><td class="bench-ours">${b.our_findings ?? "—"}</td></tr>
      <tr><td>axe-core</td><td>${b.axe_findings ?? "—"}</td></tr>
      <tr><td>WAVE</td><td>${b.wave_findings ?? "—"}</td></tr>
      <tr><td>Unique to us</td><td>${b.unique ?? "—"}</td></tr>
    </table>
    <p style="color:var(--text-dim);font-size:11px;margin-top:8px">Raw counts alone are misleading — more findings is not better if false positives are high. See Devanshi's evaluation harness for precision/recall.</p>`;
}

// ---------------------------------------------------------------------------
// Tab 4 — News & Cases
// ---------------------------------------------------------------------------

function renderNews(items) {
  const box = $("news-content");
  if (!items || !items.length) return;
  box.classList.remove("empty-state");
  box.innerHTML = items
    .map(
      (n) => `
      <article class="news-card">
        <h3>${escapeHTML(n.headline)}</h3>
        <p>${escapeHTML(n.summary)}</p>
        <div class="news-meta">
          <span>${escapeHTML(n.date || "")}</span>
          ${(n.wcag_tags || []).map((t) => `<span class="wcag-tag">${escapeHTML(t)}</span>`).join("")}
        </div>
      </article>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// Tab 5 — Timeline (per-domain audit history in chrome.storage.local)
// ---------------------------------------------------------------------------

function domainOf(url) {
  try { return new URL(url).hostname; } catch { return url; }
}

async function saveToTimeline(report) {
  const domain = domainOf(report.audit_metadata.url);
  const key = `timeline:${domain}`;
  const store = await chrome.storage.local.get(key);
  const history = store[key] || [];
  history.push({
    timestamp: report.audit_metadata.timestamp || new Date().toISOString(),
    finding_count: (report.findings || []).length,
  });
  await chrome.storage.local.set({ [key]: history.slice(-50) });
}

async function renderTimeline(url) {
  const domain = domainOf(url);
  const key = `timeline:${domain}`;
  const store = await chrome.storage.local.get(key);
  const history = store[key] || [];
  const box = $("timeline-content");
  if (!history.length) return;

  box.classList.remove("empty-state");

  // Inline SVG sparkline of finding counts over time.
  const counts = history.map((h) => h.finding_count);
  const max = Math.max(...counts, 1);
  const w = 320, h = 60, step = counts.length > 1 ? w / (counts.length - 1) : 0;
  const points = counts
    .map((c, idx) => `${(idx * step).toFixed(1)},${(h - (c / max) * (h - 6) - 3).toFixed(1)}`)
    .join(" ");

  box.innerHTML = `
    <h3 style="font-size:12px;margin:0">${escapeHTML(domain)} — findings over ${history.length} audit${history.length > 1 ? "s" : ""}</h3>
    <svg class="sparkline" viewBox="0 0 ${w} ${h}" role="img" aria-label="Finding count trend: ${counts.join(", ")}">
      <polyline points="${points}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    </svg>
    ${history
      .slice(-8)
      .reverse()
      .map(
        (hst) =>
          `<div class="timeline-row"><span>${new Date(hst.timestamp).toLocaleString()}</span><span>${hst.finding_count} findings</span></div>`
      )
      .join("")}`;
}

// Restore last audited URL context on open.
chrome.runtime.sendMessage({ type: "GET_ACTIVE_TAB_URL" }, (res) => {
  if (res && res.url) renderTimeline(res.url);
});
