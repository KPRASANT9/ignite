/**
 * Spike-to-FSM Translation Layer — maps L2 SpikeSignals to FSM TransitionEvents.
 *
 * This is the BCI feedback loop: spike.py detects neural signals (errors, state
 * changes, rate limits, patterns), and this module translates them into typed
 * FSM transition events that drive the onboarding state machine.
 *
 * Neural coding paradigm mapping:
 *   - Feedback coding: ERROR spikes → auth_failure regression
 *   - Rate coding: HEALTH_SHIFT spikes → confidence_drop
 *   - Place coding: STATE_CHANGE spikes → discovery_complete
 *   - Population coding: new modalities → new_modality_detected
 *   - Temporal coding: accumulated confidence → baseline_complete
 */

import type { SpikeSignal } from "./bridge-client"
import type { TransitionEvent, SystemOnboardingRecord } from "./onboarding-fsm"

// --- Noise profiles (mirroring spike.py NOISE_PROFILES) ---

const NOISE_PROFILES: Record<string, number> = {
  cli: 0.9,
  api: 0.8,
  db: 0.7,
  message: 0.95,
  web: 0.015,
}

// --- Confidence tracking per system ---

export interface ConfidenceState {
  /** Accumulated confidence from intent-carrying spikes */
  confidence: number
  /** Known modalities seen for this system */
  knownModalities: Set<string>
  /** Timestamp of last spike processed */
  lastSpikeAt: string
  /** Count of spikes processed since entering current state */
  spikeCount: number
}

const confidenceStates = new Map<string, ConfidenceState>()

export function getConfidenceState(systemId: string): ConfidenceState {
  if (!confidenceStates.has(systemId)) {
    confidenceStates.set(systemId, {
      confidence: 0,
      knownModalities: new Set(),
      lastSpikeAt: new Date().toISOString(),
      spikeCount: 0,
    })
  }
  return confidenceStates.get(systemId)!
}

export function resetConfidenceState(systemId: string): void {
  confidenceStates.delete(systemId)
}

// --- Serialization helpers for chrome.storage persistence ---

export interface SerializedConfidenceState {
  confidence: number
  knownModalities: string[]
  lastSpikeAt: string
  spikeCount: number
}

export function serializeConfidenceStates(): Record<string, SerializedConfidenceState> {
  const result: Record<string, SerializedConfidenceState> = {}
  for (const [key, state] of confidenceStates) {
    result[key] = {
      confidence: state.confidence,
      knownModalities: Array.from(state.knownModalities),
      lastSpikeAt: state.lastSpikeAt,
      spikeCount: state.spikeCount,
    }
  }
  return result
}

export function restoreConfidenceStates(
  data: Record<string, SerializedConfidenceState>,
): void {
  confidenceStates.clear()
  for (const [key, state] of Object.entries(data)) {
    confidenceStates.set(key, {
      confidence: state.confidence,
      knownModalities: new Set(state.knownModalities),
      lastSpikeAt: state.lastSpikeAt,
      spikeCount: state.spikeCount,
    })
  }
}

// --- Spike → FSM Transition Event Translation ---

/**
 * Translate a SpikeSignal into zero or more FSM TransitionEvents.
 *
 * A single spike can produce multiple events:
 * 1. A direct mapping (e.g., ERROR → auth_failure)
 * 2. A confidence update that may trigger baseline_complete
 * 3. A new modality detection
 */
export function spikeToTransitionEvents(
  spike: SpikeSignal,
  currentRecord: SystemOnboardingRecord,
): TransitionEvent[] {
  const events: TransitionEvent[] = []
  const systemId = spike.source_system
  const state = getConfidenceState(systemId)

  state.lastSpikeAt = new Date().toISOString()
  state.spikeCount += 1

  // --- Feedback coding: error spikes drive regression ---
  if (spike.spike_type === "error") {
    if (spike.action_space.includes("reauth_retry")) {
      events.push({ type: "auth_failure" })
    }
  }

  // --- Rate coding: health_shift degrades confidence ---
  if (spike.spike_type === "health_shift") {
    const degradedConfidence = state.confidence * 0.5
    state.confidence = degradedConfidence
    events.push({
      type: "confidence_drop",
      payload: { confidence: degradedConfidence },
    })
  }

  // --- Place coding: state transitions = system discovery ---
  if (
    spike.spike_type === "state_change" &&
    spike.confidence >= 0.8 &&
    currentRecord.state === "DISCOVERING"
  ) {
    events.push({ type: "discovery_complete" })
  }

  // --- Population coding: new modality detection ---
  const previousModalityCount = state.knownModalities.size
  for (const mod of spike.modalities) {
    state.knownModalities.add(mod)
  }
  if (state.knownModalities.size > previousModalityCount) {
    events.push({ type: "new_modality_detected" })
  }

  // --- Temporal coding: confidence accumulation toward baseline ---
  if (
    currentRecord.state === "PROFILING" ||
    currentRecord.state === "DISCOVERING"
  ) {
    const primaryModality = spike.modalities[0] || "web"
    const intentRate = NOISE_PROFILES[primaryModality] ?? 0.5
    const confidenceDelta = spike.confidence * intentRate * 0.01
    state.confidence = Math.min(1.0, state.confidence + confidenceDelta)

    if (state.confidence >= 0.8 && currentRecord.state === "PROFILING") {
      events.push({ type: "baseline_complete" })
    }
  }

  // --- Always emit trace_captured for spike count tracking ---
  events.push({ type: "trace_captured" })

  return events
}

// --- Self-tracing: emit FSM transition spans back through bridge ---

export interface SelfTraceSpan {
  type: "web_ext"
  page_url: string
  action: string
  element_role: string
  request_intent: string
  state_transition_subtype: string
  system: string
  timestamp: string
  metadata: Record<string, unknown>
}

/**
 * Create a self-trace span for an FSM transition.
 * This implements the "eat your own dog food" principle — the onboarding
 * FSM is itself a system that IGNITE monitors through its own BCI.
 */
export function createFsmTransitionSpan(
  systemId: string,
  fromState: string,
  toState: string,
  triggerSpike: SpikeSignal | null,
): SelfTraceSpan {
  return {
    type: "web_ext",
    page_url: "chrome-extension://ignite/onboarding",
    action: `fsm_transition_${fromState}_to_${toState}`,
    element_role: "system",
    request_intent: "state_transition",
    state_transition_subtype: "state_change",
    system: systemId,
    timestamp: new Date().toISOString(),
    metadata: {
      from_state: fromState,
      to_state: toState,
      trigger_spike_type: triggerSpike?.spike_type ?? "manual",
      trigger_confidence: triggerSpike?.confidence ?? null,
      trigger_action_space: triggerSpike?.action_space ?? [],
    },
  }
}
