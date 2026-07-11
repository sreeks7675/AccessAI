/**
 * WCAG Audit Agent — Background Service Worker (WS-1, Anirudh)
 * -------------------------------------------------------------
 * The traffic controller. Three jobs:
 *   1. Open the side panel when the toolbar icon is clicked.
 *   2. On "RUN_AUDIT" from the panel: ask the content script for the IP-1
 *      DOM payload, POST it to Mahesh's FastAPI /audit endpoint, and return
 *      the IP-3 Report JSON to the panel.
 *   3. Support MOCK mode so the UI can be built before the backend exists
 *      (backend integration lands Day 6 per the master plan).
 */

"use strict";

const DEFAULT_BACKEND = "http://localhost:8017"; // swap to GPU cluster IP via panel Settings

// Open side panel on toolbar click.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch(() => {});

async function getBackendURL() {
  const { backend_url } = await chrome.storage.local.get("backend_url");
  return backend_url || DEFAULT_BACKEND;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

/** Ask the content script in the active tab for the IP-1 payload. */
async function extractFromActiveTab() {
  const tab = await getActiveTab();
  if (!tab || !tab.id) throw new Error("No active tab found.");
  if (/^(chrome|about|edge|chrome-extension):/i.test(tab.url || "")) {
    throw new Error("Internal browser pages cannot be audited.");
  }

  try {
    return await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_DOM" });
  } catch (_) {
    // Content script may not be injected yet (e.g. extension reloaded after
    // page load). Inject on demand, then retry once.
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content_script.js"],
    });
    return await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_DOM" });
  }
}

/** POST the IP-1 payload to the backend, get IP-3 Report JSON back. */
async function runBackendAudit(payload) {
  const base = await getBackendURL();
  const res = await fetch(`${base}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  }
  return await res.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "RUN_AUDIT") {
    (async () => {
      try {
        const extraction = await extractFromActiveTab();
        if (!extraction || !extraction.ok) {
          throw new Error(extraction ? extraction.error : "Extraction failed.");
        }

        if (msg.mock) {
          // Mock mode: skip backend, return the bundled sample report so the
          // UI is buildable/testable before IP-3 merges on Day 6.
          const url = chrome.runtime.getURL("mock/sample_report.json");
          const report = await (await fetch(url)).json();
          report.audit_metadata.url = extraction.payload.url;
          report.audit_metadata.timestamp = extraction.payload.timestamp;
          sendResponse({ ok: true, report, payload_meta: extraction.payload.meta });
          return;
        }

        const report = await runBackendAudit(extraction.payload);
        sendResponse({ ok: true, report, payload_meta: extraction.payload.meta });
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
      }
    })();
    return true; // async response
  }

  if (msg.type === "GET_ACTIVE_TAB_URL") {
    (async () => {
      const tab = await getActiveTab();
      sendResponse({ url: tab ? tab.url : null });
    })();
    return true;
  }

  return false;
});
