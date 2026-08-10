/**
 * BCIEngine — the intelligence orchestrator for IGNITE.
 *
 * Sits between the L2 spike stream and all extension subsystems:
 * - Consumes SpikeSignals from bridge SSE
 * - Enforces trigger mode (pull/push/autonomous) per system/archetype
 * - Drives OnboardingFSM transitions via spike-to-fsm translator
 * - Routes action_space to MCP dispatch + overlay notifications
 * - Captures webRequest network observations
 * - Emits self-trace spans for its own state transitions
 *
 * Lifecycle: created once by service worker, persists state to
 * chrome.storage.local, restores on service worker restart.
 */

import {
  subscribeSpikeStream,
  ingestTrace,
  ingestApiTrace,
  getSystemCredential,
  type SpikeSignal,
} from "./bridge-client"
import type { TriggerMode } from "./vault"
import {
  applyTransition,
  createRecord,
  type OnboardingStore,
  type SystemOnboardingRecord,
} from "./onboarding-fsm"
import {
  spikeToTransitionEvents,
  createFsmTransitionSpan,
  serializeConfidenceStates,
  restoreConfidenceStates,
  type SerializedConfidenceState,
} from "./spike-to-fsm"

// --- Types ---

export interface BCIEngineConfig {
  bridgeUrl: string
}

export type SpikeHandler = (spike: SpikeSignal, systemId: string) => void

interface WebRequestCapture {
  url: string
  method: string
  statusCode: number
  type: string
  tabId: number
  timestamp: string
  system: string | null
}

// --- BCIEngine Class ---

export class BCIEngine {
  private bridgeUrl: string
  private streamActive = false
  private stopStream: (() => void) | null = null
  private spikeHandlers: SpikeHandler[] = []
  private webRequestBuffer: WebRequestCapture[] = []
  private readonly WEB_REQUEST_BUFFER_SIZE = 100

  constructor(config: BCIEngineConfig) {
    this.bridgeUrl = config.bridgeUrl
  }

  // --- Lifecycle ---

  /**
   * Start the BCI engine: restore state, connect to spike stream.
   */
  async start(): Promise<void> {
    if (this.streamActive) return

    // Restore confidence state from storage
    const stored = await chrome.storage.local.get("confidenceStates")
    if (stored.confidenceStates) {
      restoreConfidenceStates(
        stored.confidenceStates as Record<string, SerializedConfidenceState>,
      )
    }

    this.streamActive = true
    this.stopStream = subscribeSpikeStream(
      (spike) => this.handleSpike(spike),
      this.bridgeUrl,
    )

    // Start webRequest observation
    this.startWebRequestCapture()

    console.log("[IGNITE:BCI] Engine started")
  }

  /**
   * Stop the BCI engine: disconnect SSE, flush state.
   */
  async stop(): Promise<void> {
    if (this.stopStream) {
      this.stopStream()
      this.stopStream = null
    }
    this.streamActive = false

    // Persist confidence state before shutdown
    await chrome.storage.local.set({
      confidenceStates: serializeConfidenceStates(),
    })

    console.log("[IGNITE:BCI] Engine stopped")
  }

  get isActive(): boolean {
    return this.streamActive
  }

  /**
   * Update bridge URL (e.g., from user config change).
   */
  async reconfigure(bridgeUrl: string): Promise<void> {
    if (this.bridgeUrl === bridgeUrl) return
    this.bridgeUrl = bridgeUrl
    if (this.streamActive) {
      await this.stop()
      await this.start()
    }
  }

  // --- Spike Handler Registration ---

  /**
   * Register an external handler for spike signals.
   * Used by the service worker to forward spikes to overlay/side panel.
   */
  onSpike(handler: SpikeHandler): void {
    this.spikeHandlers.push(handler)
  }

  // --- Core Spike Processing ---

  private async handleSpike(spike: SpikeSignal): Promise<void> {
    const systemId = spike.source_system
    if (!systemId || systemId === "unknown") return

    // Check trigger mode — respect per-system configuration
    const shouldProcess = await this.checkTriggerMode(systemId, spike)
    if (!shouldProcess) return

    // Drive FSM transitions
    await this.driveFsm(systemId, spike)

    // Notify registered handlers (overlay, side panel, etc.)
    for (const handler of this.spikeHandlers) {
      try {
        handler(spike, systemId)
      } catch {
        // Don't let handler errors break the pipeline
      }
    }

    // Broadcast to content script overlays on all tabs
    this.broadcastToOverlays(spike)
  }

  /**
   * Check if this spike should be processed based on the system's trigger mode.
   *
   * - pull: Only process when explicitly requested (skip SSE-driven spikes)
   * - push: Process all spikes from the stream (default)
   * - autonomous: Process all spikes AND auto-execute action_space
   */
  private async checkTriggerMode(
    systemId: string,
    spike: SpikeSignal,
  ): Promise<boolean> {
    // Check all archetypes for this system — any "pull" mode blocks processing
    // Default to "push" if no credential/mode found
    const archetypes = ["mcp-sync", "mcp-ratelimit", "mcp-fsm", "mcp-async", "mcp-scoring", "mcp-orchestration"]

    for (const archetype of archetypes) {
      const cred = await getSystemCredential(systemId, archetype)
      if (cred && cred.mode === "pull") {
        // In pull mode, spikes are displayed but don't drive FSM transitions
        // Still notify handlers so the side panel can show them
        for (const handler of this.spikeHandlers) {
          try {
            handler(spike, systemId)
          } catch { /* ignore */ }
        }
        return false
      }
    }

    return true
  }

  /**
   * Drive FSM transitions from a spike signal.
   */
  private async driveFsm(
    systemId: string,
    spike: SpikeSignal,
  ): Promise<void> {
    const store = await this.getOnboardingStore()
    if (!store.systems[systemId]) {
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
    await this.saveOnboardingStore(store)

    // Persist confidence state
    await chrome.storage.local.set({
      confidenceStates: serializeConfidenceStates(),
    })

    // Self-tracing on state change
    if (record.state !== previousState) {
      console.log(
        `[IGNITE:BCI] FSM transition: ${systemId} ${previousState} → ${record.state}`,
      )
      await this.emitSelfTrace(systemId, previousState, record.state, spike)
    }

    // Autonomous mode: auto-execute actions for critical/immediate spikes
    if (spike.urgency === "critical" || spike.urgency === "immediate") {
      await this.checkAutoExecute(systemId, spike)
    }
  }

  /**
   * In autonomous mode, automatically execute spike action_space.
   */
  private async checkAutoExecute(
    systemId: string,
    spike: SpikeSignal,
  ): Promise<void> {
    // Check if ANY archetype for this system is in autonomous mode
    const cred = await getSystemCredential(systemId, "mcp-sync")
    if (!cred || cred.mode !== "autonomous") return

    // Auto-execute: send MCP_INVOKE messages for actionable items
    for (const action of spike.action_space) {
      if (action === "reauth_retry" || action === "backoff_retry") {
        // These are safe to auto-execute
        try {
          await chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system: systemId,
            archetype: "mcp-sync",
            tool: action === "reauth_retry" ? "invoke" : "calculate_backoff",
            params: { system: systemId, auto: true },
          })
          console.log(`[IGNITE:BCI] Auto-executed: ${action} for ${systemId}`)
        } catch {
          // Best-effort auto-execution
        }
      }
      // escalate_human, investigate, etc. are never auto-executed
    }
  }

  // --- webRequest Observation ---

  /**
   * Start capturing network requests via chrome.webRequest.
   * Converts HTTP observations into trace spans for the L2 pipeline.
   */
  private startWebRequestCapture(): void {
    if (!chrome.webRequest) return

    chrome.webRequest.onCompleted.addListener(
      (details) => {
        const capture: WebRequestCapture = {
          url: details.url,
          method: details.method,
          statusCode: details.statusCode,
          type: details.type,
          tabId: details.tabId,
          timestamp: new Date().toISOString(),
          system: this.inferSystemFromUrl(details.url),
        }

        // Buffer captures — flush to bridge periodically
        this.webRequestBuffer.push(capture)
        if (this.webRequestBuffer.length >= this.WEB_REQUEST_BUFFER_SIZE) {
          this.flushWebRequests()
        }
      },
      { urls: ["<all_urls>"] },
    )

    // Flush buffered requests every 10 seconds
    chrome.alarms.create("bci-webrequest-flush", { periodInMinutes: 1 / 6 })
  }

  /**
   * Flush buffered webRequest captures to the bridge as trace spans.
   */
  async flushWebRequests(): Promise<void> {
    if (this.webRequestBuffer.length === 0) return

    const batch = this.webRequestBuffer.splice(0)
    const spans = batch
      .map((c) => ({
        type: "api_ext",
        target: c.url,
        method: c.method,
        status_code: c.statusCode,
        request_type: c.type,
        system: c.system ?? "unknown",
        timestamp: c.timestamp,
        modality: "api",
        request_intent: this.classifyRequestIntent(c.method),
      }))

    if (spans.length === 0) return

    try {
      await ingestApiTrace(
        { spans, source: "webRequest", batch: true },
        this.bridgeUrl,
      )
    } catch {
      // Bridge unavailable — drop this batch (ephemeral data)
    }
  }

  /**
   * Handle the webRequest flush alarm.
   */
  async handleAlarm(alarm: chrome.alarms.Alarm): Promise<boolean> {
    if (alarm.name === "bci-webrequest-flush") {
      await this.flushWebRequests()
      return true
    }
    return false
  }

  // --- Overlay Broadcasting ---

  /**
   * Broadcast spike signals to content script overlays on all tabs.
   * The ActionOverlay CSUI listens for OVERLAY_SPIKE messages.
   */
  private broadcastToOverlays(spike: SpikeSignal): void {
    chrome.tabs.query({}, (tabs) => {
      for (const tab of tabs) {
        if (tab.id) {
          chrome.tabs.sendMessage(tab.id, {
            type: "OVERLAY_SPIKE",
            spike,
          }).catch(() => {
            // Tab may not have content script — ignore
          })
        }
      }
    })
  }

  // --- Helpers ---

  private inferSystemFromUrl(url: string): string | null {
    if (url.includes("github.com") || url.includes("api.github.com")) return "github"
    if (url.includes("atlassian.net") || url.includes("jira.")) return "jira"
    if (url.includes("databricks.com") || url.includes("azuredatabricks.net")) return "databricks"
    if (url.includes("console.aws.amazon.com") || url.includes("amazonaws.com")) return "aws-console"
    if (url.includes("gitlab.com") || url.includes("gitlab.")) return "gitlab"
    return null
  }

  private classifyRequestIntent(method: string): string {
    switch (method.toUpperCase()) {
      case "GET": return "query"
      case "POST": return "mutation"
      case "PUT": return "mutation"
      case "PATCH": return "mutation"
      case "DELETE": return "mutation"
      default: return "query"
    }
  }

  private async emitSelfTrace(
    systemId: string,
    fromState: string,
    toState: string,
    triggerSpike: SpikeSignal,
  ): Promise<void> {
    const span = createFsmTransitionSpan(systemId, fromState, toState, triggerSpike)
    try {
      await ingestTrace(span, this.bridgeUrl)
    } catch {
      // Self-tracing is best-effort
    }
  }

  private async getOnboardingStore(): Promise<OnboardingStore> {
    const result = await chrome.storage.local.get("onboarding")
    return result.onboarding || { systems: {} }
  }

  private async saveOnboardingStore(store: OnboardingStore): Promise<void> {
    await chrome.storage.local.set({ onboarding: store })
  }
}
