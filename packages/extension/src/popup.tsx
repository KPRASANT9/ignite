/**
 * IGNITE Popup — quick system status dashboard.
 *
 * Shows: connected systems, onboarding state per system,
 * bridge health status, and permission tiers.
 */

import { useEffect, useState } from "react"

interface SystemStatus {
  id: string
  state: string
  traces: number
  threshold: number
}

function Popup() {
  const [systems, setSystems] = useState<SystemStatus[]>([])
  const [bridgeHealthy, setBridgeHealthy] = useState<boolean | null>(null)

  useEffect(() => {
    chrome.storage.local.get(["onboarding", "bridgeHealthy"], (result) => {
      const store = result.onboarding || { systems: {} }
      const statuses = Object.entries(store.systems).map(
        ([id, record]: [string, any]) => ({
          id,
          state: record.state,
          traces: record.traces,
          threshold: record.threshold,
        }),
      )
      setSystems(statuses)
      setBridgeHealthy(result.bridgeHealthy ?? null)
    })
  }, [])

  return (
    <div style={{ width: 320, padding: 16, fontFamily: "system-ui" }}>
      <h2 style={{ margin: "0 0 12px", fontSize: 18 }}>🔥 IGNITE</h2>

      <div
        style={{
          padding: "8px 12px",
          borderRadius: 6,
          background: bridgeHealthy ? "#e8f5e9" : "#ffebee",
          marginBottom: 12,
          fontSize: 13,
        }}
      >
        L2 Bridge: {bridgeHealthy === null ? "checking..." : bridgeHealthy ? "✓ connected" : "✗ offline"}
      </div>

      {systems.length === 0 ? (
        <p style={{ color: "#666", fontSize: 13 }}>
          No systems connected yet. Navigate to a supported system to begin.
        </p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {systems.map((s) => (
            <li
              key={s.id}
              style={{
                padding: "8px 0",
                borderBottom: "1px solid #eee",
                fontSize: 13,
              }}
            >
              <strong>{s.id}</strong>
              <span
                style={{
                  marginLeft: 8,
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: s.state === "ACTIVE" ? "#c8e6c9" : "#fff3e0",
                  fontSize: 11,
                }}
              >
                {s.state}
              </span>
              {s.state === "PROFILING" && (
                <span style={{ marginLeft: 8, color: "#666", fontSize: 11 }}>
                  {s.traces}/{s.threshold} traces
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default Popup
