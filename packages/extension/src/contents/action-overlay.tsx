/**
 * ActionOverlay — Plasmo CSUI content script with Shadow DOM isolation.
 *
 * Renders inline spike suggestions on detected system pages,
 * similar to Grammarly's overlay: non-intrusive, contextual, dismissable.
 *
 * Consumes spikes via chrome.runtime message passing from the BCIEngine.
 * Shows a floating indicator badge that expands to show spike details
 * and action buttons when clicked.
 *
 * Shadow DOM isolation ensures no CSS bleed with host page.
 */

import type { PlasmoCSConfig, PlasmoGetOverlayAnchor } from "plasmo"
import { useEffect, useState, useCallback } from "react"
import type { SpikeSignal } from "~/lib/bridge-client"
import { mapActions, type MappedAction } from "~/lib/action-mapper"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"],
  run_at: "document_idle",
}

// Position the overlay at the bottom-right of the viewport
export const getStyle = () => {
  const style = document.createElement("style")
  style.textContent = `
    :host {
      position: fixed !important;
      bottom: 24px !important;
      right: 24px !important;
      z-index: 2147483647 !important;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
      pointer-events: auto !important;
    }
  `
  return style
}

// --- Urgency colors ---

const URGENCY_COLORS: Record<string, { bg: string; border: string; badge: string }> = {
  critical: { bg: "#ffebee", border: "#c62828", badge: "#c62828" },
  immediate: { bg: "#fff8e1", border: "#f9a825", badge: "#f57f17" },
  attention: { bg: "#fff3e0", border: "#ef6c00", badge: "#e65100" },
  background: { bg: "#f5f5f5", border: "#9e9e9e", badge: "#757575" },
}

function ActionOverlay() {
  const [spikes, setSpikes] = useState<SpikeSignal[]>([])
  const [expanded, setExpanded] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [actionResults, setActionResults] = useState<Record<string, string>>({})

  // Listen for spike signals from the BCIEngine via service worker
  useEffect(() => {
    const listener = (message: any) => {
      if (message.type === "OVERLAY_SPIKE" && message.spike) {
        setDismissed(false)
        setSpikes((prev) => [message.spike, ...prev].slice(0, 5))
      }
    }
    chrome.runtime.onMessage.addListener(listener)
    return () => chrome.runtime.onMessage.removeListener(listener)
  }, [])

  const handleAction = useCallback(async (spikeIdx: number, action: MappedAction) => {
    const key = `${spikeIdx}-${action.label}`
    setActionResults((prev) => ({ ...prev, [key]: "..." }))
    try {
      await action.handler()
      setActionResults((prev) => ({ ...prev, [key]: "done" }))
    } catch (err) {
      setActionResults((prev) => ({ ...prev, [key]: "error" }))
    }
  }, [])

  const handleDismiss = useCallback(() => {
    setDismissed(true)
    setExpanded(false)
  }, [])

  const handleDismissSpike = useCallback((index: number) => {
    setSpikes((prev) => prev.filter((_, i) => i !== index))
  }, [])

  // Nothing to show
  if (spikes.length === 0 || dismissed) {
    return null
  }

  const topSpike = spikes[0]
  const colors = URGENCY_COLORS[topSpike.urgency] || URGENCY_COLORS.background

  // Collapsed: show badge with spike count
  if (!expanded) {
    return (
      <div
        onClick={() => setExpanded(true)}
        style={{
          width: 44,
          height: 44,
          borderRadius: "50%",
          background: colors.badge,
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          fontSize: 16,
          fontWeight: 700,
          boxShadow: "0 2px 12px rgba(0,0,0,0.25)",
          transition: "transform 0.15s ease",
          userSelect: "none",
        }}
        title={`${spikes.length} spike signal${spikes.length > 1 ? "s" : ""} — click to expand`}
      >
        {spikes.length > 9 ? "9+" : spikes.length}
      </div>
    )
  }

  // Expanded: show spike cards with actions
  return (
    <div
      style={{
        width: 340,
        maxHeight: 420,
        overflowY: "auto",
        background: "#fff",
        borderRadius: 12,
        boxShadow: "0 4px 24px rgba(0,0,0,0.18)",
        border: `1px solid ${colors.border}`,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          borderBottom: "1px solid #eee",
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13, color: "#333" }}>
          IGNITE — {spikes.length} signal{spikes.length > 1 ? "s" : ""}
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setExpanded(false)}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 16,
              color: "#999",
              padding: "0 4px",
            }}
            title="Minimize"
          >
            −
          </button>
          <button
            onClick={handleDismiss}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 16,
              color: "#999",
              padding: "0 4px",
            }}
            title="Dismiss all"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Spike cards */}
      <div style={{ padding: "8px 10px" }}>
        {spikes.map((spike, i) => {
          const spikeColors = URGENCY_COLORS[spike.urgency] || URGENCY_COLORS.background
          const actions = mapActions(spike.action_space, spike.source_system, "mcp-sync")

          return (
            <div
              key={i}
              style={{
                padding: "8px 10px",
                marginBottom: 6,
                borderRadius: 8,
                background: spikeColors.bg,
                borderLeft: `3px solid ${spikeColors.border}`,
                position: "relative",
              }}
            >
              {/* Dismiss single spike */}
              <button
                onClick={() => handleDismissSpike(i)}
                style={{
                  position: "absolute",
                  top: 4,
                  right: 6,
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  color: "#bbb",
                  padding: "0 2px",
                }}
              >
                ✕
              </button>

              <div style={{ fontWeight: 600, fontSize: 12, color: "#333", paddingRight: 16 }}>
                {spike.spike_type} — {spike.source_system}
              </div>
              <div style={{ fontSize: 11, color: "#555", marginTop: 2 }}>
                {spike.description}
              </div>
              <div style={{ fontSize: 10, color: "#999", marginTop: 3 }}>
                confidence: {(spike.confidence * 100).toFixed(0)}% · {spike.modalities.join(", ")}
              </div>

              {actions.length > 0 && (
                <div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {actions.map((action, j) => {
                    const resultKey = `${i}-${action.label}`
                    return (
                      <button
                        key={j}
                        onClick={() => handleAction(i, action)}
                        title={action.description}
                        disabled={actionResults[resultKey] === "..."}
                        style={{
                          fontSize: 10,
                          padding: "3px 8px",
                          borderRadius: 4,
                          border: "1px solid #ccc",
                          background: actionResults[resultKey] === "done" ? "#e8f5e9" : "#fff",
                          cursor: actionResults[resultKey] === "..." ? "wait" : "pointer",
                          color: "#333",
                        }}
                      >
                        {action.label}
                        {actionResults[resultKey] === "..." && " ⏳"}
                        {actionResults[resultKey] === "done" && " ✓"}
                        {actionResults[resultKey] === "error" && " ✗"}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default ActionOverlay
