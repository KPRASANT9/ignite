/**
 * IGNITE Service Worker (background script).
 *
 * Manages: BCIEngine, OnboardingFSM, MCP Gateway routing.
 * Persists state to chrome.storage.local (service worker terminates when idle).
 *
 * Receives messages from content scripts via chrome.runtime.onMessage.
 * Relays traces to the Python L2 bridge via REST POST.
 */

import {
  applyTransition,
  createRecord,
  type OnboardingStore,
} from "~/lib/onboarding-fsm"
import { authenticateGitHub, type GitHubOAuthConfig } from "~/lib/github-oauth"
import { storeSystemCredential } from "~/lib/bridge-client"
import { BCIEngine } from "~/lib/bci-engine"

export {}

// --- Bridge URL (configurable via chrome.storage.sync) ---

async function getBridgeUrl(): Promise<string> {
  const result = await chrome.storage.sync.get("bridgeUrl")
  return result.bridgeUrl || "http://localhost:8400"
}

// --- BCIEngine singleton ---

let engine: BCIEngine | null = null

async function getEngine(): Promise<BCIEngine> {
  if (!engine) {
    const bridgeUrl = await getBridgeUrl()
    engine = new BCIEngine({ bridgeUrl })
  }
  return engine
}

// --- Message handling from content scripts ---

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SYSTEM_DETECTED") {
    handleSystemDetected(message.url, message.systemId)
    sendResponse({ ok: true })
  }

  if (message.type === "TRACE_CAPTURED") {
    handleTraceCapture(message.trace)
    sendResponse({ ok: true })
  }

  if (message.type === "OAUTH_GITHUB") {
    handleGitHubOAuth(message.config)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: err.message }))
    return true // async response
  }

  if (message.type === "MCP_INVOKE") {
    handleMcpInvoke(message.system, message.archetype, message.tool, message.params)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: err.message }))
    return true
  }

  // Return true for async response support
  return true
})

async function handleSystemDetected(url: string, systemId: string) {
  const store = await getOnboardingStore()
  if (!store.systems[systemId]) {
    store.systems[systemId] = createRecord()
    await saveOnboardingStore(store)
    console.log(`[IGNITE] New system detected: ${systemId}`)
  }
}

async function handleGitHubOAuth(config: GitHubOAuthConfig) {
  const store = await getOnboardingStore()
  const systemId = "github"

  // Ensure record exists
  if (!store.systems[systemId]) {
    store.systems[systemId] = createRecord()
  }

  // Transition IDLE → CONNECTING
  store.systems[systemId] = applyTransition(
    systemId,
    store.systems[systemId],
    { type: "credential_provided" },
  )
  await saveOnboardingStore(store)

  try {
    const tokenResponse = await authenticateGitHub(config)

    // Store token in vault
    await storeSystemCredential(
      "github",
      "mcp-sync",
      tokenResponse.access_token,
      tokenResponse.scope.split(",").filter(Boolean),
    )

    // Transition CONNECTING → DISCOVERING
    store.systems[systemId] = applyTransition(
      systemId,
      store.systems[systemId],
      { type: "auth_success" },
    )
    await saveOnboardingStore(store)

    console.log(`[IGNITE] GitHub OAuth success — state: ${store.systems[systemId].state}`)
    return { ok: true, state: store.systems[systemId].state }
  } catch (err) {
    // Transition on auth failure
    store.systems[systemId] = applyTransition(
      systemId,
      store.systems[systemId],
      { type: "auth_failure" },
    )
    await saveOnboardingStore(store)

    console.warn(`[IGNITE] GitHub OAuth failed — state: ${store.systems[systemId].state}`)
    throw err
  }
}

async function handleTraceCapture(trace: Record<string, unknown>) {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/traces/web`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spans: [trace],
        system: (trace.system as string) || "unknown",
      }),
    })
    const data = await res.json()
    if (data.spikes?.length > 0) {
      console.log(`[IGNITE] ${data.spikes.length} spike(s) detected`)
    }
  } catch {
    console.warn("[IGNITE] Bridge service not available")
  }
}

async function handleMcpInvoke(
  system: string,
  archetype: string,
  tool: string,
  params: Record<string, unknown>,
) {
  const bridgeUrl = await getBridgeUrl()
  const { getSystemCredential } = await import("~/lib/bridge-client")
  const cred = await getSystemCredential(system, archetype)

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": archetype,
  }
  if (cred) {
    headers["Authorization"] = `Bearer ${cred.token}`
  }

  const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
    method: "POST",
    headers,
    body: JSON.stringify({ tool, arguments: params, system, archetype }),
  })

  if (!res.ok) {
    throw new Error(`MCP invoke failed (${res.status})`)
  }

  return await res.json()
}

// --- Storage helpers ---

async function getOnboardingStore(): Promise<OnboardingStore> {
  const result = await chrome.storage.local.get("onboarding")
  return result.onboarding || { systems: {} }
}

async function saveOnboardingStore(store: OnboardingStore): Promise<void> {
  await chrome.storage.local.set({ onboarding: store })
}

// --- Periodic health check + BCIEngine lifecycle ---

chrome.alarms.create("bridge-health", { periodInMinutes: 1 })

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "bridge-health") {
    try {
      const bridgeUrl = await getBridgeUrl()
      const res = await fetch(`${bridgeUrl}/health`)
      const healthy = res.ok
      await chrome.storage.local.set({ bridgeHealthy: healthy })

      // Start BCIEngine if bridge is healthy
      if (healthy) {
        const eng = await getEngine()
        if (!eng.isActive) {
          await eng.reconfigure(bridgeUrl)
          await eng.start()
        }
      }
    } catch {
      await chrome.storage.local.set({ bridgeHealthy: false })
    }
    return
  }

  // Delegate alarm to BCIEngine (webRequest flush, etc.)
  const eng = await getEngine()
  await eng.handleAlarm(alarm)
})

// --- Startup: attempt BCIEngine connection ---

getBridgeUrl()
  .then(async (url) => {
    const res = await fetch(`${url}/health`)
    if (res.ok) {
      const eng = await getEngine()
      await eng.start()
    }
  })
  .catch(() => {
    // Bridge not available yet — alarm will retry
  })
