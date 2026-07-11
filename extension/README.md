# WS-1: Extension Frontend — Anirudh

Chrome Manifest V3 extension. Pure JavaScript, **no npm install, no build step**.
Owns the entire `extension/` folder. Nobody else touches these files.

## Load the extension (do this once)

1. Chrome → `chrome://extensions`
2. Toggle **Developer mode** ON (top right)
3. **Load unpacked** → select this `extension/` folder
4. Pin the extension, click its icon — the side panel opens

## Dev loop

- Edit a file → go to `chrome://extensions` → click the reload icon on the card
- Content script logs: DevTools on the **web page** (F12 → Console)
- Background logs: `chrome://extensions` → card → **"service worker"** link
- Panel logs: right-click inside the side panel → Inspect

## Mock mode (build UI before the backend exists)

The **Mock** toggle in the panel top bar makes the audit return
`mock/sample_report.json` instead of calling the backend. Keep it ON until
Mahesh's `/audit` endpoint is live (IP-3 merges Day 6), then turn it OFF and
set the backend URL:

```js
// In the panel's DevTools console:
chrome.storage.local.set({ backend_url: "http://GPU_CLUSTER_IP:8000" });
```

## File map

| File | What it does | Day built |
|---|---|---|
| `manifest.json` | MV3 config, side_panel API, permissions | 1 |
| `content_script.js` | DOM serialisation, computed styles, colour resolution, tab order, focus rings, SPA detection, PII redaction → IP-1 payload | 2 |
| `background.js` | Message passing, backend POST /audit, mock mode | 3 |
| `side_panel/panel.html` + `panel.css` | 5-tab skeleton UI | 4 |
| `side_panel/panel.js` | Report rendering, Fix Studio, diff, preview, timeline | 5–8 |

## Upgrading the diff renderer (optional, Day 6)

A dependency-free token differ is built into `panel.js`. To use diff2html as
the design doc suggests: download `diff2html.min.js` + `diff2html.min.css`
into `assets/vendor/` and reference them from `panel.html` with local paths.
MV3 blocks loading them from a CDN.
