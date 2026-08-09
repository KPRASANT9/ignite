/**
 * IGNITE Dashboard — system status, onboarding progress,
 * permission tiers, and BCI mode configuration.
 *
 * Shows the N×6 permission matrix per system with:
 * - Onboarding progress bar (confidence → ACTIVE threshold)
 * - Per-archetype permission tier badges
 * - BCI mode selector (pull/push/autonomous)
 * - Bridge health + credential summary
 */

import { useEffect, useState, useCallback } from "react"
import type { TriggerMode } from "~/lib/vault"

// --- Types (mirrors profile-manager.ts without chrome import) ---

interface PermissionCell {
  system: string
  archetype: string
  tier: string
  mode: TriggerMode
  tokenStatus: "active" | "expired" | "none"
  scopes: string[]
  grantedAt: string | null
}

interface SystemProfile {
  systemId: string
  onboardingState: string
  traces: number
  confidence: number
  archetypes: PermissionCell[]
}

// --- Constants ---

const ALL_ARCHETYPES = [
  "mcp-sync",
  "mcp-ratelimit",
  "mcp-fsm",
  "mcp-async",
  "mcp-scoring",
  "mcp-orchestration",
]

const STATE_COLORS: Record<string, string> = {
  IDLE: "#9e9e9e",
  CONNECTING: "#ff9800",
  DISCOVERING: "#2196f3",
  PROFILING: "#ff5722",
  ACTIVE: "#4caf50",
}

const TIER_COLORS: Record<string, string> = {
  none: "#e0e0e0",
  user: "#bbdefb",
  admin: "#c8e6c9",
  service: "#fff9c4",
}

const MODE_LABELS: Record<TriggerMode, string> = {
  pull: "Manual",
  push: "Auto",
  autonomous: "Full Auto",
}

// --- Component ---

function Popup() {
  const [systems, setSystems] = useState<SystemProfile[]>([])
  const [bridgeHealthy, setBridgeHealthy] = useState<boolean | null>(null)
  const [bridgeUrl, setBridgeUrl] = useState("http://localhost:8400")
  const [totalCredentials, setTotalCredentials] = useState(0)

  // Load data from chrome.storage + vault
  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    // Bridge health
    const healthResult = await chrome.storage.local.get("bridgeHealthy")
    setBridgeHealthy(healthResult.bridgeHealthy ?? null)

    // Bridge URL
    const urlResult = await chrome.storage.sync.get("bridgeUrl")
    if (urlResult.bridgeUrl) setBridgeUrl(urlResult.bridgeUrl)

    // Onboarding state
    const storeResult = await chrome.storage.local.get("onboarding")
    const store = storeResult.onboarding || { systems: {} }

    // Build profiles (without vault access from popup — use stored summary)
    const profiles: SystemProfile[] = Object.entries(store.systems).map(
      ([id, record]: [string, any]) => ({
        systemId: id,
        onboardingState: record.state,
        traces: record.traces,
        confidence: record.confidence,
        archetypes: ALL_ARCHETYPES.map((arch) => ({
          system: id,
          archetype: arch,
          tier: "none",
          mode: "push" as TriggerMode,
          tokenStatus: "none" as const,
          scopes: [],
          grantedAt: null,
        })),
      }),
    )

    setSystems(profiles)
  }

  const handleBridgeUrlChange = useCallback(async (url: string) => {
    setBridgeUrl(url)
    await chrome.storage.sync.set({ bridgeUrl: url })
  }, [])

  const handleModeChange = useCallback(
    async (systemId: string, archetype: string, mode: TriggerMode) => {
      // Update local state immediately
      setSystems((prev) =>
        prev.map((s) => {
          if (s.systemId !== systemId) return s
          return {
            ...s,
            archetypes: s.archetypes.map((a) => {
              if (a.archetype !== archetype) return a
              return { ...a, mode }
            }),
          }
        }),
      )

      // Persist via service worker (vault access)
      chrome.runtime.sendMessage({
        type: "SET_TRIGGER_MODE",
        systemId,
        archetype,
        mode,
      })
    },
    [],
  )

  const handleOAuthGitHub = useCallback(() => {
    chrome.runtime.sendMessage({
      type: "OAUTH_GITHUB",
      config: { clientId: "" },
    })
  }, [])

  return (
    <div style={{ width: 400, padding: 16, fontFamily: "system-ui", fontSize: 13 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>IGNITE</h2>
        <span
          style={{
            fontSize: 10,
            padding: "3px 8px",
            borderRadius: 10,
            background: bridgeHealthy ? "#e8f5e9" : bridgeHealthy === false ? "#ffebee" : "#f5f5f5",
            color: bridgeHealthy ? "#2e7d32" : bridgeHealthy === false ? "#c62828" : "#666",
          }}
        >
          Bridge: {bridgeHealthy === null ? "checking..." : bridgeHealthy ? "connected" : "offline"}
        </span>
      </div>

      {/* Bridge URL config */}
      <div style={{ marginBottom: 16, display: "flex", gap: 6 }}>
        <input
          type="text"
          value={bridgeUrl}
          onChange={(e) => handleBridgeUrlChange(e.target.value)}
          style={{
            flex: 1,
            padding: "6px 10px",
            borderRadius: 6,
            border: "1px solid #ddd",
            fontSize: 11,
          }}
          placeholder="http://localhost:8400"
        />
        <button
          onClick={handleOAuthGitHub}
          style={{
            padding: "6px 12px",
            borderRadius: 6,
            border: "1px solid #1976d2",
            background: "#e3f2fd",
            color: "#1976d2",
            fontSize: 11,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          + GitHub
        </button>
      </div>

      {/* Systems */}
      {systems.length === 0 ? (
        <div style={{ color: "#888", textAlign: "center", padding: "20px 0" }}>
          <p>No systems connected yet.</p>
          <p style={{ fontSize: 11 }}>Navigate to a supported system to begin onboarding.</p>
        </div>
      ) : (
        systems.map((system) => (
          <div
            key={system.systemId}
            style={{
              marginBottom: 12,
              border: "1px solid #e0e0e0",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {/* System header */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                background: "#fafafa",
                borderBottom: "1px solid #eee",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{system.systemId}</span>
                <span
                  style={{
                    fontSize: 10,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: STATE_COLORS[system.onboardingState] || "#9e9e9e",
                    color: "#fff",
                  }}
                >
                  {system.onboardingState}
                </span>
              </div>
              <span style={{ fontSize: 10, color: "#666" }}>
                {system.traces} traces
              </span>
            </div>

            {/* Onboarding progress bar */}
            <div style={{ padding: "6px 12px", borderBottom: "1px solid #f0f0f0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#666", marginBottom: 3 }}>
                <span>Confidence</span>
                <span>{(system.confidence * 100).toFixed(0)}%</span>
              </div>
              <div style={{ height: 6, borderRadius: 3, background: "#e0e0e0", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, system.confidence * 100)}%`,
                    borderRadius: 3,
                    background:
                      system.confidence >= 0.8 ? "#4caf50"
                      : system.confidence >= 0.6 ? "#ff9800"
                      : "#f44336",
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#999", marginTop: 2 }}>
                <span>0%</span>
                <span style={{ color: system.confidence >= 0.8 ? "#4caf50" : "#999" }}>80% (ACTIVE)</span>
                <span>100%</span>
              </div>
            </div>

            {/* Permission matrix — 6 archetypes */}
            <div style={{ padding: "6px 12px 8px" }}>
              <div style={{ fontSize: 10, color: "#666", marginBottom: 4, fontWeight: 600 }}>
                Permission Matrix
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4 }}>
                {system.archetypes.map((cell) => (
                  <div
                    key={cell.archetype}
                    style={{
                      padding: "4px 6px",
                      borderRadius: 4,
                      background: "#f9f9f9",
                      border: "1px solid #eee",
                      fontSize: 9,
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 2, fontSize: 9 }}>
                      {cell.archetype.replace("mcp-", "")}
                    </div>
                    <div style={{ display: "flex", gap: 3, alignItems: "center", flexWrap: "wrap" }}>
                      {/* Tier badge */}
                      <span
                        style={{
                          padding: "1px 4px",
                          borderRadius: 3,
                          background: TIER_COLORS[cell.tier] || TIER_COLORS.none,
                          fontSize: 8,
                        }}
                      >
                        {cell.tier}
                      </span>
                      {/* Token status */}
                      <span style={{ fontSize: 8, color: cell.tokenStatus === "active" ? "#4caf50" : cell.tokenStatus === "expired" ? "#f44336" : "#999" }}>
                        {cell.tokenStatus === "active" ? "●" : cell.tokenStatus === "expired" ? "○" : "—"}
                      </span>
                      {/* Mode selector */}
                      <select
                        value={cell.mode}
                        onChange={(e) =>
                          handleModeChange(
                            system.systemId,
                            cell.archetype,
                            e.target.value as TriggerMode,
                          )
                        }
                        style={{
                          fontSize: 8,
                          padding: "1px 2px",
                          border: "1px solid #ddd",
                          borderRadius: 2,
                          background: "#fff",
                          cursor: "pointer",
                        }}
                      >
                        <option value="pull">{MODE_LABELS.pull}</option>
                        <option value="push">{MODE_LABELS.push}</option>
                        <option value="autonomous">{MODE_LABELS.autonomous}</option>
                      </select>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))
      )}

      {/* Footer */}
      <div style={{ fontSize: 9, color: "#bbb", textAlign: "center", marginTop: 8 }}>
        IGNITE v0.1.0 · {systems.length} system{systems.length !== 1 ? "s" : ""} · 6 archetypes
      </div>
    </div>
  )
}

export default Popup
