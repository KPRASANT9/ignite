/**
 * IGNITE Content Script — runs on every matched page.
 *
 * Responsibilities:
 * 1. System detection (URL pattern + DOM fingerprinting)
 * 2. Trace capture (webRequest observation forwarded from service worker,
 *    DOM event observation directly)
 * 3. Relay detected systems and captured traces to service worker
 *
 * This is the "electrode array" — raw signal capture that feeds
 * into the L2 pipeline via the service worker → bridge transport.
 */

import type { PlasmoCSConfig } from "plasmo"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"],
  run_at: "document_idle",
}

import { detectSystem } from "~/lib/systems"

// --- System Detection (Strategy 1: URL match) ---

const currentSystem = detectSystem(window.location.href)

if (currentSystem) {
  chrome.runtime.sendMessage({
    type: "SYSTEM_DETECTED",
    url: window.location.href,
    systemId: currentSystem.id,
    systemName: currentSystem.name,
  })
}

// --- DOM Fingerprinting (Strategy 2: SPA/MPA detection) ---

function detectRenderingModel(): "spa" | "mpa" | "unknown" {
  // SPA indicators
  if (
    document.querySelector("[data-reactroot]") ||
    document.querySelector("#__next") ||
    document.querySelector("[ng-version]") ||
    document.querySelector("[data-v-]")
  ) {
    return "spa"
  }

  // If there's a <meta name="turbo-visit-control">, it's likely Turbo/MPA
  if (document.querySelector('meta[name="turbo-visit-control"]')) {
    return "mpa"
  }

  return "unknown"
}

// --- Navigation Observation ---

// Listen for SPA navigation (pushState/popState)
const originalPushState = history.pushState.bind(history)
history.pushState = function (...args) {
  originalPushState(...args)
  handleNavigation("pushState")
}

window.addEventListener("popstate", () => {
  handleNavigation("popstate")
})

function handleNavigation(trigger: string) {
  const system = detectSystem(window.location.href)
  if (system) {
    chrome.runtime.sendMessage({
      type: "SYSTEM_DETECTED",
      url: window.location.href,
      systemId: system.id,
      systemName: system.name,
    })
  }
}

// --- DOM Event Capture (Strategy 3: interaction tracing) ---

import { sanitizeWebInput } from "~/lib/sanitizer"

function buildWebExtSpan(event: Event, action: string): Record<string, unknown> {
  const target = event.target as HTMLElement
  const input = target as HTMLInputElement

  return {
    type: "web_ext",
    page_url: window.location.href,
    page_title: document.title,
    selector: cssSelector(target),
    action,
    element_role: target.getAttribute("role") || target.tagName.toLowerCase(),
    element_name: target.getAttribute("name") || target.getAttribute("id") || null,
    element_text: target.textContent?.slice(0, 100) || null,
    input_value: sanitizeWebInput(
      input.value || null,
      target.getAttribute("name") || undefined,
      input.type || undefined,
    ),
    input_type: input.type || null,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    screenshot_ref: null,
    element_state: {
      disabled: (target as HTMLButtonElement).disabled ?? false,
      checked: input.checked ?? null,
      selected: (target as HTMLOptionElement).selected ?? null,
    },
    snapshot_hash: null,
    delta_from: null,
    timestamp: new Date().toISOString(),
    system: currentSystem?.id ?? "unknown",
  }
}

function cssSelector(el: HTMLElement): string {
  if (el.id) return `#${el.id}`
  const tag = el.tagName.toLowerCase()
  const classes = Array.from(el.classList).slice(0, 3).join(".")
  return classes ? `${tag}.${classes}` : tag
}

// Capture clicks
document.addEventListener("click", (e) => {
  const span = buildWebExtSpan(e, "click")
  chrome.runtime.sendMessage({ type: "TRACE_CAPTURED", trace: span })
}, { capture: true, passive: true })

// Capture form submissions
document.addEventListener("submit", (e) => {
  const span = buildWebExtSpan(e, "submit")
  chrome.runtime.sendMessage({ type: "TRACE_CAPTURED", trace: span })
}, { capture: true, passive: true })

// Capture input changes (debounced)
let inputTimer: ReturnType<typeof setTimeout> | null = null
document.addEventListener("input", (e) => {
  if (inputTimer) clearTimeout(inputTimer)
  inputTimer = setTimeout(() => {
    const span = buildWebExtSpan(e, "input")
    chrome.runtime.sendMessage({ type: "TRACE_CAPTURED", trace: span })
  }, 300)
}, { capture: true, passive: true })

// --- Log activation ---

console.log(
  `[IGNITE] Content script active on ${window.location.hostname}` +
    (currentSystem ? ` — detected: ${currentSystem.name}` : " — unknown system"),
)
