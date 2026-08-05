import { describe, it, expect } from "vitest"
import {
  evaluateTransition,
  applyTransition,
  createRecord,
} from "../src/lib/onboarding-fsm"

describe("OnboardingFSM", () => {
  it("creates a fresh record in IDLE state", () => {
    const record = createRecord()
    expect(record.state).toBe("IDLE")
    expect(record.traces).toBe(0)
    expect(record.authFailures).toBe(0)
  })

  it("transitions IDLE → CONNECTING on credential_provided", () => {
    const record = createRecord()
    const updated = applyTransition("github", record, { type: "credential_provided" })
    expect(updated.state).toBe("CONNECTING")
  })

  it("transitions CONNECTING → DISCOVERING on auth_success", () => {
    const record = createRecord()
    const connecting = applyTransition("github", record, { type: "credential_provided" })
    const discovering = applyTransition("github", connecting, { type: "auth_success" })
    expect(discovering.state).toBe("DISCOVERING")
  })

  it("transitions CONNECTING → IDLE after 3 auth failures", () => {
    let record = createRecord()
    record = applyTransition("github", record, { type: "credential_provided" })
    expect(record.state).toBe("CONNECTING")

    record = applyTransition("github", record, { type: "auth_failure" })
    expect(record.state).toBe("CONNECTING")
    expect(record.authFailures).toBe(1)

    record = applyTransition("github", record, { type: "auth_failure" })
    expect(record.state).toBe("CONNECTING")
    expect(record.authFailures).toBe(2)

    record = applyTransition("github", record, { type: "auth_failure" })
    expect(record.state).toBe("IDLE")
    expect(record.authFailures).toBe(0) // reset on IDLE
  })

  it("transitions DISCOVERING → PROFILING on discovery_complete", () => {
    let record = createRecord()
    record = applyTransition("github", record, { type: "credential_provided" })
    record = applyTransition("github", record, { type: "auth_success" })
    record = applyTransition("github", record, { type: "discovery_complete" })
    expect(record.state).toBe("PROFILING")
  })

  it("transitions ACTIVE → CONNECTING on auth_failure", () => {
    let record = createRecord()
    record.state = "ACTIVE"
    record.confidence = 0.9
    record = applyTransition("github", record, { type: "auth_failure" })
    expect(record.state).toBe("CONNECTING")
  })

  it("transitions ACTIVE → PROFILING on confidence_drop below 0.6", () => {
    let record = createRecord()
    record.state = "ACTIVE"
    record.confidence = 0.9
    record = applyTransition("github", record, {
      type: "confidence_drop",
      payload: { confidence: 0.5 },
    })
    expect(record.state).toBe("PROFILING")
    expect(record.confidence).toBe(0.5)
  })

  it("transitions to IDLE on user_disconnect from any state", () => {
    const record = createRecord()
    record.state = "ACTIVE"
    const updated = applyTransition("github", record, { type: "user_disconnect" })
    expect(updated.state).toBe("IDLE")
  })

  it("increments traces on trace_captured", () => {
    const record = createRecord()
    const updated = applyTransition("github", record, { type: "trace_captured" })
    expect(updated.traces).toBe(1)
  })

  it("rejects invalid transitions", () => {
    const record = createRecord()
    const result = evaluateTransition("github", record, { type: "auth_success" })
    expect(result).toBeNull() // can't auth_success from IDLE
  })
})
