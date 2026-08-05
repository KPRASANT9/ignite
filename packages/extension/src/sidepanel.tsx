/**
 * IGNITE Side Panel — inline chat interface (trace-grounded BCI conversation).
 *
 * Stays open across tab switches (Chrome Side Panel API).
 * Connects to L2 bridge via SSE for real-time spike signal streaming.
 *
 * Grounded in:
 * - Context: current page's trace history
 * - Knowledge: system profile
 * - Actions: archetype-matched MCPs
 * - Memory: profile graph (cross-system state)
 */

import { useEffect, useState, useCallback } from "react"
import type { SpikeSignal } from "~/lib/bridge-client"
import { mapActions, type MappedAction } from "~/lib/action-mapper"

function SidePanel() {
  const [spikes, setSpikes] = useState<SpikeSignal[]>([])
  const [connected, setConnected] = useState(false)
  const [bridgeUrl, setBridgeUrl] = useState("http://localhost:8400")
  const [actionResults, setActionResults] = useState<Record<number, string>>({})

  useEffect(() => {
    chrome.storage.sync.get("bridgeUrl", (result) => {
      if (result.bridgeUrl) setBridgeUrl(result.bridgeUrl)
    })
  }, [])

  useEffect(() => {
    const eventSource = new EventSource(`${bridgeUrl}/traces/spikes`)

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === "connected") {
          setConnected(true)
        }
        if (data.type === "spike" && data.spike) {
          setSpikes((prev) => [data.spike, ...prev].slice(0, 50))
        }
      } catch {
        // Ignore malformed events
      }
    }

    eventSource.onerror = () => {
      setConnected(false)
    }

    return () => eventSource.close()
  }, [bridgeUrl])

  const handleAction = useCallback(async (spikeIndex: number, action: MappedAction) => {
    setActionResults((prev) => ({ ...prev, [spikeIndex]: "running..." }))
    try {
      const result = await action.handler()
      setActionResults((prev) => ({ ...prev, [spikeIndex]: "done" }))
      console.log(`[IGNITE] Action "${action.label}" result:`, result)
    } catch (err) {
      setActionResults((prev) => ({
        ...prev,
        [spikeIndex]: `error: ${(err as Error).message}`,
      }))
    }
  }, [])

  return (
    <div style={{ padding: 16, fontFamily: "system-ui", fontSize: 13 }}>
      <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>IGNITE</h2>

      <div
        style={{
          padding: "4px 8px",
          borderRadius: 4,
          background: connected ? "#e8f5e9" : "#fff3e0",
          marginBottom: 12,
          fontSize: 11,
        }}
      >
        {connected ? "● Connected to BCI Engine" : "○ Connecting..."}
      </div>

      {spikes.length === 0 ? (
        <p style={{ color: "#888" }}>
          No spike signals yet. Navigate to a connected system to begin trace capture.
        </p>
      ) : (
        <div>
          <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>Spike Signals</h3>
          {spikes.map((spike, i) => {
            const actions = mapActions(
              spike.action_space,
              spike.source_system,
              "mcp-sync",
            )
            return (
              <div
                key={i}
                style={{
                  padding: "8px 10px",
                  marginBottom: 6,
                  borderRadius: 6,
                  background:
                    spike.urgency === "critical"
                      ? "#ffcdd2"
                      : spike.urgency === "immediate"
                        ? "#fff9c4"
                        : spike.urgency === "attention"
                          ? "#fff3e0"
                          : "#f5f5f5",
                  borderLeft: `3px solid ${
                    spike.urgency === "critical"
                      ? "#c62828"
                      : spike.urgency === "immediate"
                        ? "#f9a825"
                        : spike.urgency === "attention"
                          ? "#ef6c00"
                          : "#bdbdbd"
                  }`,
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 12 }}>
                  {spike.spike_type} — {spike.source_system}
                </div>
                <div style={{ fontSize: 11, color: "#555", marginTop: 2 }}>
                  {spike.description}
                </div>
                <div style={{ fontSize: 10, color: "#999", marginTop: 4 }}>
                  confidence: {(spike.confidence * 100).toFixed(0)}% ·{" "}
                  {spike.modalities.join(", ")}
                </div>
                {actions.length > 0 && (
                  <div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {actions.map((action, j) => (
                      <button
                        key={j}
                        onClick={() => handleAction(i, action)}
                        title={action.description}
                        style={{
                          fontSize: 10,
                          padding: "3px 8px",
                          borderRadius: 3,
                          border: "1px solid #ccc",
                          background: "#fff",
                          cursor: "pointer",
                        }}
                      >
                        {action.label}
                      </button>
                    ))}
                  </div>
                )}
                {actionResults[i] && (
                  <div style={{ fontSize: 10, color: "#666", marginTop: 3 }}>
                    {actionResults[i]}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default SidePanel
