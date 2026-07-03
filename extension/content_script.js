/**
 * WCAG Audit Agent — Content Script (WS-1, Anirudh)
 * ---------------------------------------------------
 * Runs in the page context. Extracts everything Design Doc §2.2.1 requires
 * and produces the IP-1 DOM Payload contract (Strategy Doc §4.1):
 *
 *   { url, timestamp, dom_html, computed_styles, meta }
 *
 * Never talks to the backend directly — it replies to messages from
 * background.js, which owns network access.
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants & guardrails (Design §5.2.1)
// ---------------------------------------------------------------------------

const INTERACTIVE_SELECTOR =
  "a, button, input, select, textarea, [role], [tabindex]";

const DOM_SIZE_LIMIT_BYTES = 500 * 1024; // 500KB — above this we warn + chunk
const MAX_FOCUS_PROBES = 60;             // cap focus-ring probing so we don't jank the page

// PII patterns for form field values (client-side data-minimisation guardrail)
const PII_PATTERNS = [
  /\b(?:\d[ -]*?){13,16}\b/,          // credit-card-like digit runs
  /\b\d{3}-\d{2}-\d{4}\b/,            // SSN
  /\b(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3,5}\)?[ -]?)\d{3}[ -]?\d{4}\b/ // phone
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a reasonably-unique CSS selector for an element. */
function cssPath(el) {
  if (!(el instanceof Element)) return "";
  const parts = [];
  let node = el;
  while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
    let part = node.nodeName.toLowerCase();
    if (node.id) {
      parts.unshift(`${part}#${CSS.escape(node.id)}`);
      break;
    }
    const cls = [...node.classList].slice(0, 2).map((c) => CSS.escape(c));
    if (cls.length) part += "." + cls.join(".");
    const parent = node.parentElement;
    if (parent) {
      const siblings = [...parent.children].filter(
        (s) => s.nodeName === node.nodeName
      );
      if (siblings.length > 1) {
        part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
    }
    parts.unshift(part);
    node = node.parentElement;
  }
  return parts.join(" > ");
}

/** Resolve any CSS colour string to rgb()/rgba() via a probe element. */
function resolveColour(value) {
  if (!value) return value;
  const probe = document.createElement("span");
  probe.style.color = value;
  probe.style.display = "none";
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  return resolved || value;
}

/** Redact PII-looking strings from form field values (Design §5.2.1). */
function redactPII(value) {
  if (typeof value !== "string" || !value) return value;
  return PII_PATTERNS.some((re) => re.test(value)) ? "[REDACTED]" : value;
}

// ---------------------------------------------------------------------------
// Extraction steps (Design §2.2.1, bullet by bullet)
// ---------------------------------------------------------------------------

/** 1. Full serialised DOM: body innerHTML, script/style stripped, ARIA kept. */
function serialiseDOM() {
  const clone = document.body.cloneNode(true);

  // Strip script/style/noscript but preserve every ARIA / semantic attribute.
  clone.querySelectorAll("script, style, noscript, template").forEach((n) =>
    n.remove()
  );

  // Redact any typed-in form values before serialisation (GDPR guardrail).
  clone.querySelectorAll("input, textarea").forEach((n) => {
    if (n.value) n.setAttribute("value", redactPII(n.value));
  });

  return clone.innerHTML;
}

/** 2 & 3. Computed CSS for every interactive element, colours resolved to RGB. */
function extractComputedStyles() {
  const styles = {};
  const PROPS = [
    "color",
    "background-color",
    "font-size",
    "font-weight",
    "line-height",
    "outline",
    "outline-color",
    "outline-width",
    "border",
    "display",
    "visibility",
    "opacity",
    "cursor",
    "width",
    "height",
  ];

  document.querySelectorAll(INTERACTIVE_SELECTOR).forEach((el) => {
    const selector = cssPath(el);
    if (!selector || styles[selector]) return;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const entry = {};
    PROPS.forEach((p) => {
      let v = cs.getPropertyValue(p);
      if (p.includes("color")) v = resolveColour(v);
      entry[p] = v;
    });
    // Touch target size for the motor agent (WCAG 2.5.5 / 2.5.8).
    entry["bounding_rect"] = {
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
    styles[selector] = entry;
  });

  return styles;
}

/** 4. Tab order: interactive elements sorted by tabIndex, then DOM order. */
function extractTabOrder() {
  const els = [
    ...document.querySelectorAll(
      ":is(a,button,input,select,textarea,[tabindex])"
    ),
  ];
  return els
    .map((el, domIndex) => ({
      selector: cssPath(el),
      tabindex: el.tabIndex,
      domIndex,
    }))
    .sort((a, b) => {
      const ta = a.tabindex > 0 ? a.tabindex : Number.MAX_SAFE_INTEGER;
      const tb = b.tabindex > 0 ? b.tabindex : Number.MAX_SAFE_INTEGER;
      return ta - tb || a.domIndex - b.domIndex;
    })
    .map(({ selector, tabindex }) => ({ selector, tabindex }));
}

/** 5. Every img with accessibility-relevant attributes. */
function extractImages() {
  return [...document.querySelectorAll("img")].map((img) => ({
    selector: cssPath(img),
    src: (img.getAttribute("src") || "").slice(0, 300),
    alt: img.getAttribute("alt"),
    role: img.getAttribute("role"),
    aria_hidden: img.getAttribute("aria-hidden"),
    aria_label: img.getAttribute("aria-label"),
    width: img.width,
    height: img.height,
  }));
}

/** 6. video/audio elements with their track children. */
function extractMedia() {
  return [...document.querySelectorAll("video, audio")].map((m) => ({
    selector: cssPath(m),
    tag: m.tagName.toLowerCase(),
    autoplay: m.hasAttribute("autoplay"),
    controls: m.hasAttribute("controls"),
    muted: m.hasAttribute("muted"),
    tracks: [...m.querySelectorAll("track")].map((t) => ({
      kind: t.getAttribute("kind"),
      srclang: t.getAttribute("srclang"),
      label: t.getAttribute("label"),
    })),
    sources: [...m.querySelectorAll("source")].map((s) =>
      s.getAttribute("type")
    ),
  }));
}

/** 7. Form elements with label association analysis. */
function extractForms() {
  return [...document.querySelectorAll("input, select, textarea")].map(
    (field) => {
      const id = field.getAttribute("id");
      let labelFor = null;
      if (id) {
        const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        labelFor = lbl ? lbl.textContent.trim().slice(0, 120) : null;
      }
      const wrapped = field.closest("label");
      return {
        selector: cssPath(field),
        type: field.getAttribute("type") || field.tagName.toLowerCase(),
        id,
        name: field.getAttribute("name"),
        label_for_text: labelFor,
        wrapped_in_label: !!wrapped,
        aria_label: field.getAttribute("aria-label"),
        aria_labelledby: field.getAttribute("aria-labelledby"),
        aria_describedby: field.getAttribute("aria-describedby"),
        required: field.hasAttribute("required"),
      };
    }
  );
}

/**
 * 8. Dynamic focus-ring capture: focus each interactive element, record the
 * resulting outline style + bounding rect (WCAG 2.4.7 / 2.4.11 input data).
 * Capped and restored so we don't wreck the user's page state.
 */
function captureFocusRings() {
  const previouslyFocused = document.activeElement;
  const results = [];
  const els = [...document.querySelectorAll(INTERACTIVE_SELECTOR)].slice(
    0,
    MAX_FOCUS_PROBES
  );

  els.forEach((el) => {
    try {
      el.focus({ preventScroll: true });
      if (document.activeElement !== el) return; // not focusable — itself a data point
      const cs = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      results.push({
        selector: cssPath(el),
        outline: cs.outline,
        outline_color: resolveColour(cs.outlineColor),
        outline_width: cs.outlineWidth,
        box_shadow: cs.boxShadow,
        focus_rect: {
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      });
    } catch (_) {
      /* some elements throw on focus — skip */
    }
  });

  try {
    if (previouslyFocused && previouslyFocused.focus) {
      previouslyFocused.focus({ preventScroll: true });
    } else if (document.activeElement) {
      document.activeElement.blur();
    }
  } catch (_) {}

  return results;
}

/** 9. SPA detection (Design §2.2.1 last bullet). */
function detectSPA() {
  const w = window;
  const react =
    !!w.React ||
    !!w.__REACT_DEVTOOLS_GLOBAL_HOOK__ ||
    !!document.querySelector("[data-reactroot], #root, #__next");
  const vue = !!w.Vue || !!w.__VUE__ || !!document.querySelector("[data-v-app]");
  const angular = !!w.angular || !!w.ng || !!document.querySelector("[ng-version]");
  return react || vue || angular;
}

// ---------------------------------------------------------------------------
// Payload assembly — IP-1 contract (Strategy Doc §4.1)
// ---------------------------------------------------------------------------

function buildDOMPayload() {
  const dom_html = serialiseDOM();
  const dom_size_bytes = new Blob([dom_html]).size;

  const payload = {
    url: location.href,
    timestamp: new Date().toISOString(),
    dom_html,
    computed_styles: extractComputedStyles(),
    meta: {
      spa_detected: detectSPA(),
      dom_size_bytes,
      page_title: document.title,
      lang_attribute: document.documentElement.getAttribute("lang") || null,
      // Extra extraction blocks the agents consume (kept inside meta so the
      // top-level IP-1 shape stays exactly as contracted):
      tab_order: extractTabOrder(),
      images: extractImages(),
      media: extractMedia(),
      forms: extractForms(),
      focus_rings: captureFocusRings(),
      oversize_warning: dom_size_bytes > DOM_SIZE_LIMIT_BYTES,
    },
  };

  return payload;
}

// ---------------------------------------------------------------------------
// Messaging — background.js asks us for the payload
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "EXTRACT_DOM") {
    // Block internal pages (Design §5.2.1 URL scope validation).
    if (/^(chrome|about|edge|chrome-extension):/i.test(location.href)) {
      sendResponse({ ok: false, error: "Internal browser pages cannot be audited." });
      return true;
    }
    try {
      const payload = buildDOMPayload();
      sendResponse({ ok: true, payload });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
    }
    return true; // keep channel open for async safety
  }
  return false;
});
