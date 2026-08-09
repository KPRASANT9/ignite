/**
 * IGNITE Service Worker (background script).
 *
 * Manages: OnboardingFSM, ProfileManager, BCI Engine, MCP Gateway routing.
 * Persists state to chrome.storage.local (service worker terminates when idle).
 *
 * Receives messages from content scripts via chrome.runtime.onMessage.
 * Relays traces to the Python L2 bridge via REST POST.
 */

import { detectSystem } from "~/lib/systems"
import {
  applyTransition,
  createRecord,
  type OnboardingStore,
  type TransitionEvent,
} from "~/lib/onboarding-fsm"
import { authenticateGitHub, type GitHubOAuthConfig } from "~/lib/github-oauth"
import { storeSystemCredential, subscribeSpikeStream, type SpikeSignal } from "~/lib/bridge-client"
import { ingestTrace } from "~/lib/bridge-client"
import {
  spikeToTransitionEvents,
  createFsmTransitionSpan,
  serializeConfidenceStates,
  restoreConfidenceStates,
  type SerializedConfidenceState,
} from "~/lib/spike-to-fsm"

export {}

// --- Bridge URL (configurable via chrome.storage.sync) ---

async function getBridgeUrl(): Promise<string> {
  const result = await chrome.storage.sync.get("bridgeUrl")
  return result.bridgeUrl || "http://localhost:8400"
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

// --- Periodic health check ---

chrome.alarms.create("bridge-health", { periodInMinutes: 1 })

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "bridge-health") {
    try {
      const bridgeUrl = await getBridgeUrl()
      const res = await fetch(`${bridgeUrl}/health`)
      const healthy = res.ok
      await chrome.storage.local.set({ bridgeHealthy: healthy })
      // Start spike stream if bridge is healthy and not already connected
      if (healthy && !spikeStreamActive) {
        startSpikeStream()
      }
    } catch {
      await chrome.storage.local.set({ bridgeHealthy: false })
    }
  }
})

// --- BCI Spike Stream: subscribe to bridge SSE and drive FSM ---

let spikeStreamActive = false
let stopSpikeStream: (() => void) | null = null

async function startSpikeStream() {
  if (spikeStreamActive) return

  // Restore confidence state from storage
  const stored = await chrome.storage.local.get("confidenceStates")
  if (stored.confidenceStates) {
    restoreConfidenceStates(
      stored.confidenceStates as Record<string, SerializedConfidenceState>,
    )
  }

  const bridgeUrl = await getBridgeUrl()
  spikeStreamActive = true

  stopSpikeStream = subscribeSpikeStream(
    async (spike: SpikeSignal) => {
      await handleSpikeSignal(spike)
    },
    bridgeUrl,
  )

  console.log("[IGNITE] BCI spike stream connected")
}

async function handleSpikeSignal(spike: SpikeSignal) {
  const systemId = spike.source_system
  if (!systemId || systemId === "unknown") return

  const store = await getOnboardingStore()
  if (!store.systems[systemId]) {
    // Auto-create record for newly detected system via spike
    store.systems[systemId] = createRecord()
  }

  const currentRecord = store.systems[systemId]
  const previousState = currentRecord.state

  // Translate spike into FSM transition events
  const events = spikeToTransitionEvents(spike, currentRecord)

  // Apply each event sequentially
  let record = currentRecord
  for (const event of events) {
    record = applyTransition(systemId, record, event)
  }

  store.systems[systemId] = record
  await saveOnboardingStore(store)

  // Persist confidence state
  await chrome.storage.local.set({
    confidenceStates: serializeConfidenceStates(),
  })

  // Self-tracing: if state changed, emit FSM transition span back through bridge
  if (record.state !== previousState) {
    console.log(
      `[IGNITE] FSM transition: ${systemId} ${previousState} → ${record.state}`,
    )
    const selfSpan = createFsmTransitionSpan(
      systemId,
      previousState,
      record.state,
      spike,
    )
    const bridgeUrl = await getBridgeUrl()
    try {
      await ingestTrace(selfSpan, bridgeUrl)
    } catch {
      // Self-tracing is best-effort — don't block FSM on bridge failure
    }
  }
}

// Start spike stream on service worker startup
getBridgeUrl()
  .then((url) => fetch(`${url}/health`))
  .then((res) => {
    if (res.ok) startSpikeStream()
  })
  .catch(() => {
    // Bridge not available yet — alarm will retry
  })
