/**
 * Onboarding FSM — 5-state machine for per-system connection lifecycle.
 *
 * IDLE → CONNECTING → DISCOVERING → PROFILING → ACTIVE
 *
 * Persisted in chrome.storage.local. Service worker evaluates
 * guards on chrome.storage.onChanged events.
 *
 * Regression paths (non-linear):
 *   ACTIVE → CONNECTING (credential expired)
 *   ACTIVE → PROFILING (schema drift, confidence < 0.6)
 *   ACTIVE → DISCOVERING (new modality detected)
 *   PROFILING → DISCOVERING (new system detected)
 *   CONNECTING → IDLE (auth fails 3x)
 */

export type OnboardingState =
  | "IDLE"
  | "CONNECTING"
  | "DISCOVERING"
  | "PROFILING"
  | "ACTIVE"

export interface SystemOnboardingRecord {
  state: OnboardingState
  traces: number
  threshold: number
  confidence: number
  lastTransition: string // ISO timestamp
  authFailures: number
}

export interface OnboardingStore {
  systems: Record<string, SystemOnboardingRecord>
}

const DEFAULT_THRESHOLD = 50
const MAX_AUTH_FAILURES = 3
const ACTIVE_CONFIDENCE = 0.8
const REPROFILE_CONFIDENCE = 0.6

/**
 * Transition guard: returns the new state if the transition is allowed,
 * or null if the guard fails.
 */
export function evaluateTransition(
  systemId: string,
  current: SystemOnboardingRecord,
  event: TransitionEvent,
): OnboardingState | null {
  switch (event.type) {
    case "credential_provided":
      if (current.state === "IDLE") return "CONNECTING"
      return null

    case "auth_success":
      if (current.state === "CONNECTING") return "DISCOVERING"
      return null

    case "auth_failure":
      if (current.state === "CONNECTING") {
        if (current.authFailures + 1 >= MAX_AUTH_FAILURES) return "IDLE"
      }
      if (current.state === "ACTIVE") return "CONNECTING"
      return null

    case "discovery_complete":
      if (current.state === "DISCOVERING") return "PROFILING"
      return null

    case "discovery_timeout":
      if (current.state === "DISCOVERING") return "CONNECTING"
      return null

    case "baseline_complete":
      if (current.state === "PROFILING" && current.confidence >= ACTIVE_CONFIDENCE) {
        return "ACTIVE"
      }
      return null

    case "trace_captured":
      // No state change, but updates trace count
      return null

    case "confidence_drop":
      if (current.state === "ACTIVE" && current.confidence < REPROFILE_CONFIDENCE) {
        return "PROFILING"
      }
      return null

    case "new_modality_detected":
      if (current.state === "ACTIVE") return "DISCOVERING"
      if (current.state === "PROFILING") return "DISCOVERING"
      return null

    case "user_disconnect":
      return "IDLE"

    default:
      return null
  }
}

export interface TransitionEvent {
  type:
    | "credential_provided"
    | "auth_success"
    | "auth_failure"
    | "discovery_complete"
    | "discovery_timeout"
    | "baseline_complete"
    | "trace_captured"
    | "confidence_drop"
    | "new_modality_detected"
    | "user_disconnect"
  payload?: Record<string, unknown>
}

/**
 * Apply a transition event to a system's onboarding record.
 * Returns the updated record (or the same record if no transition).
 */
export function applyTransition(
  systemId: string,
  current: SystemOnboardingRecord,
  event: TransitionEvent,
): SystemOnboardingRecord {
  const newState = evaluateTransition(systemId, current, event)

  const updated = { ...current }

  if (event.type === "trace_captured") {
    updated.traces += 1
    if (updated.traces >= updated.threshold && updated.state === "PROFILING") {
      updated.state = "ACTIVE"
      updated.lastTransition = new Date().toISOString()
    }
    return updated
  }

  if (event.type === "auth_failure") {
    updated.authFailures += 1
  }

  if (event.type === "confidence_drop" && event.payload?.confidence !== undefined) {
    updated.confidence = event.payload.confidence as number
  }

  if (newState !== null) {
    updated.state = newState
    updated.lastTransition = new Date().toISOString()
    if (newState === "IDLE") {
      updated.authFailures = 0
    }
  }

  return updated
}

/**
 * Create a fresh onboarding record for a newly detected system.
 */
export function createRecord(): SystemOnboardingRecord {
  return {
    state: "IDLE",
    traces: 0,
    threshold: DEFAULT_THRESHOLD,
    confidence: 0,
    lastTransition: new Date().toISOString(),
    authFailures: 0,
  }
}
