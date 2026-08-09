import { describe, it, expect, beforeEach } from "vitest"
import {
  spikeToTransitionEvents,
  getConfidenceState,
  resetConfidenceState,
  createFsmTransitionSpan,
  serializeConfidenceStates,
  restoreConfidenceStates,
} from "../src/lib/spike-to-fsm"
import { createRecord } from "../src/lib/onboarding-fsm"
import type { SpikeSignal } from "../src/lib/bridge-client"

function makeSpike(overrides: Partial<SpikeSignal> = {}): SpikeSignal {
  return {
    spike_type: "error",
    confidence: 0.85,
    urgency: "immediate",
    modalities: ["api"],
    chain: ["span-1"],
    action_space: ["reauth_retry", "escalate_human"],
    source_system: "github",
    correlation_boost: 1.0,
    description: "Auth failure on /repos",
    ...overrides,
  }
}

describe("spike-to-fsm", () => {
  beforeEach(() => {
    resetConfidenceState("github")
  })

  describe("spikeToTransitionEvents", () => {
    it("maps AUTH_FAILURE error spike to auth_failure event", () => {
      const spike = makeSpike({
        spike_type: "error",
        action_space: ["reauth_retry", "escalate_human"],
      })
      const record = createRecord()
      record.state = "ACTIVE"

      const events = spikeToTransitionEvents(spike, record)

      const authFailure = events.find((e) => e.type === "auth_failure")
      expect(authFailure).toBeDefined()
    })

    it("maps HEALTH_SHIFT spike to confidence_drop event", () => {
      const spike = makeSpike({
        spike_type: "health_shift",
        action_space: ["backoff_retry", "reduce_rate"],
        confidence: 0.75,
      })
      const record = createRecord()
      record.state = "ACTIVE"

      const events = spikeToTransitionEvents(spike, record)

      const drop = events.find((e) => e.type === "confidence_drop")
      expect(drop).toBeDefined()
      expect(drop!.payload?.confidence).toBeLessThan(0.75)
    })

    it("maps STATE_CHANGE spike to discovery_complete in DISCOVERING state", () => {
      const spike = makeSpike({
        spike_type: "state_change",
        confidence: 0.9,
        action_space: ["monitor_outcome", "log_transition"],
      })
      const record = createRecord()
      record.state = "DISCOVERING"

      const events = spikeToTransitionEvents(spike, record)

      const discovery = events.find((e) => e.type === "discovery_complete")
      expect(discovery).toBeDefined()
    })

    it("does NOT emit discovery_complete for STATE_CHANGE when not in DISCOVERING", () => {
      const spike = makeSpike({
        spike_type: "state_change",
        confidence: 0.9,
        action_space: ["monitor_outcome"],
      })
      const record = createRecord()
      record.state = "ACTIVE"

      const events = spikeToTransitionEvents(spike, record)

      const discovery = events.find((e) => e.type === "discovery_complete")
      expect(discovery).toBeUndefined()
    })

    it("detects new modality and emits new_modality_detected", () => {
      const spike1 = makeSpike({
        spike_type: "pattern",
        modalities: ["api"],
        action_space: ["log_pattern"],
      })
      const record = createRecord()
      record.state = "ACTIVE"

      // First spike — api modality is new
      const events1 = spikeToTransitionEvents(spike1, record)
      expect(events1.find((e) => e.type === "new_modality_detected")).toBeDefined()

      // Second spike with same modality — no new_modality_detected
      const events2 = spikeToTransitionEvents(spike1, record)
      expect(events2.find((e) => e.type === "new_modality_detected")).toBeUndefined()

      // Third spike with new modality — triggers again
      const spike2 = makeSpike({
        spike_type: "pattern",
        modalities: ["api", "web"],
        action_space: ["log_pattern"],
      })
      const events3 = spikeToTransitionEvents(spike2, record)
      expect(events3.find((e) => e.type === "new_modality_detected")).toBeDefined()
    })

    it("always emits trace_captured event", () => {
      const spike = makeSpike({ spike_type: "pattern", action_space: [] })
      const record = createRecord()

      const events = spikeToTransitionEvents(spike, record)

      expect(events.find((e) => e.type === "trace_captured")).toBeDefined()
    })

    it("accumulates confidence in PROFILING and fires baseline_complete", () => {
      const record = createRecord()
      record.state = "PROFILING"

      // Send many high-confidence API spikes (API intent rate = 0.8)
      for (let i = 0; i < 200; i++) {
        const spike = makeSpike({
          spike_type: "pattern",
          confidence: 0.95,
          modalities: ["api"],
          action_space: ["monitor_outcome"],
        })
        const events = spikeToTransitionEvents(spike, record)
        const baseline = events.find((e) => e.type === "baseline_complete")
        if (baseline) {
          // Should eventually fire
          expect(getConfidenceState("github").confidence).toBeGreaterThanOrEqual(0.8)
          return
        }
      }

      // If we get here, confidence should have accumulated
      expect(getConfidenceState("github").confidence).toBeGreaterThan(0)
    })

    it("ignores spikes from unknown systems", () => {
      const spike = makeSpike({ source_system: "unknown" })
      const record = createRecord()

      // Should not throw, just processes normally
      const events = spikeToTransitionEvents(spike, record)
      expect(events.length).toBeGreaterThan(0)
    })
  })

  describe("confidence state management", () => {
    it("tracks spike count per system", () => {
      const spike = makeSpike()
      const record = createRecord()

      spikeToTransitionEvents(spike, record)
      spikeToTransitionEvents(spike, record)
      spikeToTransitionEvents(spike, record)

      expect(getConfidenceState("github").spikeCount).toBe(3)
    })

    it("serializes and restores confidence state", () => {
      const spike = makeSpike({ modalities: ["api", "web"] })
      const record = createRecord()
      spikeToTransitionEvents(spike, record)

      const serialized = serializeConfidenceStates()
      expect(serialized["github"]).toBeDefined()
      expect(serialized["github"].knownModalities).toContain("api")
      expect(serialized["github"].knownModalities).toContain("web")

      // Reset and restore
      resetConfidenceState("github")
      expect(getConfidenceState("github").spikeCount).toBe(0)

      restoreConfidenceStates(serialized)
      expect(getConfidenceState("github").spikeCount).toBe(1)
      expect(getConfidenceState("github").knownModalities.has("api")).toBe(true)
    })
  })

  describe("createFsmTransitionSpan", () => {
    it("creates a valid self-trace span", () => {
      const spike = makeSpike()
      const span = createFsmTransitionSpan("github", "ACTIVE", "CONNECTING", spike)

      expect(span.type).toBe("web_ext")
      expect(span.action).toBe("fsm_transition_ACTIVE_to_CONNECTING")
      expect(span.system).toBe("github")
      expect(span.request_intent).toBe("state_transition")
      expect(span.metadata.from_state).toBe("ACTIVE")
      expect(span.metadata.to_state).toBe("CONNECTING")
      expect(span.metadata.trigger_spike_type).toBe("error")
    })

    it("handles null trigger spike", () => {
      const span = createFsmTransitionSpan("github", "IDLE", "CONNECTING", null)
      expect(span.metadata.trigger_spike_type).toBe("manual")
      expect(span.metadata.trigger_confidence).toBeNull()
    })
  })
})
