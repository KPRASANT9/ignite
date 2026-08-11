/**
 * IGNITE Side Panel — InlineChat: trace-grounded, profile-aware conversation.
 *
 * Combines real-time spike display with a conversational interface
 * grounded in the current page's trace history and system profile.
 *
 * Sections:
 * 1. System context bar — current system, state, archetype, tier
 * 2. Spike signal feed — real-time spikes with action buttons
 * 3. Chat interface — message input + trace-grounded responses
 */

import { useEffect, useState, useCallback, useRef } from "react"
import type { SpikeSignal } from "~/lib/bridge-client"
import { mapActions, type MappedAction } from "~/lib/action-mapper"

// --- Types ---

interface SystemContext {
  id: string
  state: string
  traces: number
  confidence: number
}

interface ChatMessage {
  role: "user" | "assistant" | "system"
  content: string
  timestamp: string
  groundedIn?: string[] // trace/spike IDs this message references
}

interface EndpointInfo {
  endpoint: string
  method: string
  count: number
  latency_avg_ms: number
}

// --- Component ---

function SidePanel() {
  const [spikes, setSpikes] = useState<SpikeSignal[]>([])
  const [connected, setConnected] = useState(false)
  const [bridgeUrl, setBridgeUrl] = useState("http://localhost:8400")
  const [actionResults, setActionResults] = useState<Record<number, string>>({})

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState("")
  const [chatLoading, setChatLoading] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  // System context
  const [systemContext, setSystemContext] = useState<SystemContext | null>(null)
  const [endpoints, setEndpoints] = useState<EndpointInfo[]>([])

  // View mode
  const [activeTab, setActiveTab] = useState<"spikes" | "chat">("spikes")

  // --- Bridge URL ---
  useEffect(() => {
    chrome.storage.sync.get("bridgeUrl", (result) => {
      if (result.bridgeUrl) setBridgeUrl(result.bridgeUrl)
    })
  }, [])

  // --- System context from onboarding store ---
  useEffect(() => {
    const loadContext = () => {
      chrome.storage.local.get("onboarding", (result) => {
        const store = result.onboarding || { systems: {} }
        const systems = Object.entries(store.systems)
        if (systems.length > 0) {
          // Show the most recently active system
          const [id, record] = systems.reduce((best, current) => {
            const [, rec] = current as [string, any]
            const [, bestRec] = best as [string, any]
            return rec.state === "ACTIVE" || rec.traces > (bestRec.traces || 0)
              ? current
              : best
          })
          const rec = record as any
          setSystemContext({
            id,
            state: rec.state,
            traces: rec.traces,
            confidence: rec.confidence,
          })
        }
      })
    }
    loadContext()
    const interval = setInterval(loadContext, 5000)
    return () => clearInterval(interval)
  }, [])

  // --- Fetch endpoint catalog ---
  useEffect(() => {
    if (!systemContext) return
    fetch(`${bridgeUrl}/endpoints`)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setEndpoints(data.slice(0, 10))
        }
      })
      .catch(() => {})
  }, [bridgeUrl, systemContext])

  // --- SSE spike stream ---
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

  // --- Scroll chat to bottom ---
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // --- Action handler ---
  const handleAction = useCallback(async (spikeIndex: number, action: MappedAction) => {
    setActionResults((prev) => ({ ...prev, [spikeIndex]: "running..." }))
    try {
      await action.handler()
      setActionResults((prev) => ({ ...prev, [spikeIndex]: "done" }))
    } catch (err) {
      setActionResults((prev) => ({
        ...prev,
        [spikeIndex]: `error: ${(err as Error).message}`,
      }))
    }
  }, [])

  // --- Chat: send message ---
  const handleSendMessage = useCallback(async () => {
    const text = inputValue.trim()
    if (!text || chatLoading) return

    const userMessage: ChatMessage = {
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    }

    setMessages((prev) => [...prev, userMessage])
    setInputValue("")
    setChatLoading(true)

    // Build trace-grounded context for the response
    const context = buildTraceContext()

    // Generate response grounded in traces + system profile
    const response = generateGroundedResponse(text, context)

    const assistantMessage: ChatMessage = {
      role: "assistant",
      content: response,
      timestamp: new Date().toISOString(),
      groundedIn: context.recentSpikeTypes,
    }

    setMessages((prev) => [...prev, assistantMessage])
    setChatLoading(false)
  }, [inputValue, chatLoading, spikes, systemContext, endpoints])

  // --- Build trace context for grounded responses ---
  function buildTraceContext() {
    const recent = spikes.slice(0, 10)
    const allModalities = new Set(recent.flatMap((s) => s.modalities))
    const allActions = recent.flatMap((s) => s.action_space)
    const actionCounts: Record<string, number> = {}
    for (const a of allActions) {
      actionCounts[a] = (actionCounts[a] || 0) + 1
    }
    return {
      system: systemContext?.id || "unknown",
      state: systemContext?.state || "IDLE",
      traces: systemContext?.traces || 0,
      confidence: systemContext?.confidence || 0,
      recentSpikes: recent.slice(0, 5),
      recentSpikeTypes: recent.slice(0, 5).map((s) => s.spike_type),
      endpoints: endpoints.slice(0, 5),
      modalities: [...allModalities],
      pendingActions: actionCounts,
      spikeHistory: recent,
    }
  }

  // --- Generate trace-grounded response ---
  // For P1, this is a local rule-based response. In P2+, this calls an LLM
  // with trace context injected as system prompt grounding.
  function generateGroundedResponse(
    query: string,
    context: ReturnType<typeof buildTraceContext>,
  ): string {
    const q = query.toLowerCase()

    // Status queries
    if (q.includes("status") || q.includes("how") || q.includes("state")) {
      if (context.state === "ACTIVE") {
        return `${context.system} is **ACTIVE** with ${context.traces} traces captured. Confidence: ${(context.confidence * 100).toFixed(0)}%. ${context.recentSpikes.length > 0 ? `Latest signal: ${context.recentSpikes[0].spike_type} (${context.recentSpikes[0].description}).` : "No recent spike signals."}`
      }
      return `${context.system} is in **${context.state}** state. ${context.traces} traces captured so far. ${context.state === "PROFILING" ? `Need confidence ≥ 80% to go ACTIVE (currently ${(context.confidence * 100).toFixed(0)}%).` : ""}`
    }

    // Spike queries
    if (q.includes("spike") || q.includes("signal") || q.includes("alert")) {
      if (context.recentSpikes.length === 0) {
        return "No spike signals detected recently. The system appears stable."
      }
      const summary = context.recentSpikes
        .map((s) => `• **${s.spike_type}** (${(s.confidence * 100).toFixed(0)}%): ${s.description}`)
        .join("\n")
      return `Recent spike signals for ${context.system}:\n\n${summary}`
    }

    // Endpoint queries
    if (q.includes("endpoint") || q.includes("api") || q.includes("latency")) {
      if (context.endpoints.length === 0) {
        return "No endpoint data available yet. Traces need to capture API interactions first."
      }
      const summary = context.endpoints
        .map((e) => `• \`${e.method} ${e.endpoint}\` — ${e.count} calls, avg ${e.latency_avg_ms}ms`)
        .join("\n")
      return `Tracked endpoints for ${context.system}:\n\n${summary}`
    }

    // Action queries
    if (q.includes("action") || q.includes("do") || q.includes("suggest") || q.includes("recommend")) {
      const actions = Object.entries(context.pendingActions)
      if (actions.length === 0) {
        return "No pending actions. The system is stable."
      }
      const summary = actions
        .sort((a, b) => b[1] - a[1])
        .map(([action, count]) => `• **${action}** (${count}x)`)
        .join("\n")
      return `Suggested actions from recent spikes:\n\n${summary}\n\nClick the action buttons on spike cards in the Signals tab to execute.`
    }

    // Summary/overview queries
    if (q.includes("summary") || q.includes("overview") || q.includes("report")) {
      const errorCount = context.spikeHistory.filter((s) => s.spike_type === "error").length
      const patternCount = context.spikeHistory.filter((s) => s.spike_type === "pattern").length
      return `**${context.system} Summary**\n\n• State: **${context.state}** (${(context.confidence * 100).toFixed(0)}% confidence)\n• Traces: ${context.traces} captured\n• Modalities: ${context.modalities.length > 0 ? context.modalities.join(", ") : "none yet"}\n• Recent signals: ${context.spikeHistory.length} (${errorCount} errors, ${patternCount} patterns)\n• Endpoints tracked: ${context.endpoints.length}\n• Pending actions: ${Object.keys(context.pendingActions).length} types`
    }

    // Error/issue queries
    if (q.includes("error") || q.includes("issue") || q.includes("problem")) {
      const errors = context.recentSpikes.filter((s) => s.spike_type === "error")
      if (errors.length === 0) {
        return "No error spikes detected recently. All systems operating normally."
      }
      return `${errors.length} error signal${errors.length > 1 ? "s" : ""} detected:\n\n${errors.map((e) => `• ${e.description} — Actions: ${e.action_space.join(", ")}`).join("\n")}`
    }

    // Cross-modality queries
    if (q.includes("modal") || q.includes("cross") || q.includes("correlation")) {
      const modalities = new Set(context.recentSpikes.flatMap((s) => s.modalities))
      if (modalities.size >= 2) {
        return `Cross-modality signals detected across **${[...modalities].join(" + ")}** for ${context.system}. ${modalities.size >= 2 ? "1.5x" : "1.0x"} correlation boost applied${modalities.size >= 3 ? " (2.0x for 3+ modalities)" : ""}. The BCI loop is seeing signals from multiple observation surfaces — this increases spike confidence.`
      }
      return `Only **${modalities.size > 0 ? [...modalities].join(", ") : "no"}** modality observed so far. Cross-modality correlation activates when 2+ modalities (web, api, cli, db, msg) contribute signals simultaneously.`
    }

    // BCI loop queries
    if (q.includes("bci") || q.includes("loop") || q.includes("pipeline")) {
      const stateEmoji = { IDLE: "⏸", CONNECTING: "🔌", DISCOVERING: "🔍", PROFILING: "📊", ACTIVE: "✅" }
      return `BCI loop for ${context.system}:\n\n• **Capture**: ${context.traces} traces (web + api modalities)\n• **Spikes**: ${context.recentSpikes.length} recent signals\n• **FSM**: ${(stateEmoji as any)[context.state] || "❓"} ${context.state} (confidence: ${(context.confidence * 100).toFixed(0)}%)\n• **Actions**: ${context.recentSpikes.flatMap((s) => s.action_space).length} pending action suggestions\n\nThe loop flows: capture → spike detection → FSM evolution → action suggestions → MCP execution.`
    }

    // Help
    if (q.includes("help") || q.includes("what can")) {
      return `I can help you understand your connected systems through their trace data. Try asking:\n\n• "What's the status of github?"\n• "Show me recent spike signals"\n• "What endpoints are being tracked?"\n• "Are there any errors?"\n• "Show cross-modality signals"\n• "How is the BCI loop?"\n\nI'm grounded in real trace data — my answers reflect what the BCI pipeline has actually observed.`
    }

    // Default
    return `I can see ${context.system} is in **${context.state}** state with ${context.traces} traces. ${context.recentSpikes.length} recent spike signals. Ask me about status, spikes, endpoints, errors, cross-modality, or the BCI loop for trace-grounded insights.`
  }

  // --- Render ---
  return (
    <div style={{ fontFamily: "system-ui", fontSize: 13, height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* System context bar */}
      <div style={{ padding: "10px 14px", borderBottom: "1px solid #e0e0e0", background: "#fafafa" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 700, fontSize: 15 }}>IGNITE</span>
          <span style={{
            fontSize: 10,
            padding: "2px 8px",
            borderRadius: 10,
            background: connected ? "#e8f5e9" : "#fff3e0",
            color: connected ? "#2e7d32" : "#e65100",
          }}>
            {connected ? "Connected" : "Connecting..."}
          </span>
        </div>
        {systemContext && (
          <div style={{ fontSize: 11, color: "#666", marginTop: 4, display: "flex", gap: 8 }}>
            <span><strong>{systemContext.id}</strong></span>
            <span style={{
              padding: "1px 6px",
              borderRadius: 4,
              background: systemContext.state === "ACTIVE" ? "#c8e6c9" : "#fff3e0",
              fontSize: 10,
            }}>
              {systemContext.state}
            </span>
            <span>{systemContext.traces} traces</span>
            <span>{(systemContext.confidence * 100).toFixed(0)}% conf</span>
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: "1px solid #e0e0e0" }}>
        <button
          onClick={() => setActiveTab("spikes")}
          style={{
            flex: 1,
            padding: "8px 0",
            border: "none",
            borderBottom: activeTab === "spikes" ? "2px solid #1976d2" : "2px solid transparent",
            background: "none",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: activeTab === "spikes" ? 600 : 400,
            color: activeTab === "spikes" ? "#1976d2" : "#666",
          }}
        >
          Signals ({spikes.length})
        </button>
        <button
          onClick={() => setActiveTab("chat")}
          style={{
            flex: 1,
            padding: "8px 0",
            border: "none",
            borderBottom: activeTab === "chat" ? "2px solid #1976d2" : "2px solid transparent",
            background: "none",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: activeTab === "chat" ? 600 : 400,
            color: activeTab === "chat" ? "#1976d2" : "#666",
          }}
        >
          Chat
        </button>
      </div>

      {/* Content area */}
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
        {activeTab === "spikes" && (
          <>
            {spikes.length === 0 ? (
              <p style={{ color: "#888", fontSize: 12, textAlign: "center", marginTop: 40 }}>
                No spike signals yet. Navigate to a connected system to begin trace capture.
              </p>
            ) : (
              spikes.map((spike, i) => {
                const actions = mapActions(spike.action_space, spike.source_system, "mcp-sync")
                return (
                  <div
                    key={i}
                    style={{
                      padding: "8px 10px",
                      marginBottom: 6,
                      borderRadius: 6,
                      background:
                        spike.urgency === "critical" ? "#ffcdd2"
                        : spike.urgency === "immediate" ? "#fff9c4"
                        : spike.urgency === "attention" ? "#fff3e0"
                        : "#f5f5f5",
                      borderLeft: `3px solid ${
                        spike.urgency === "critical" ? "#c62828"
                        : spike.urgency === "immediate" ? "#f9a825"
                        : spike.urgency === "attention" ? "#ef6c00"
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
                      confidence: {(spike.confidence * 100).toFixed(0)}% · {spike.modalities.join(", ")}
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
              })
            )}
          </>
        )}

        {activeTab === "chat" && (
          <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            {/* Messages */}
            <div style={{ flex: 1, overflowY: "auto" }}>
              {messages.length === 0 && (
                <div style={{ color: "#888", fontSize: 12, textAlign: "center", marginTop: 30 }}>
                  <p>Ask me about your connected systems.</p>
                  <p style={{ fontSize: 11, marginTop: 8 }}>
                    I'm grounded in real trace data — try "what's the status?" or "show me recent spikes"
                  </p>
                </div>
              )}
              {messages.map((msg, i) => (
                <div
                  key={i}
                  style={{
                    marginBottom: 10,
                    display: "flex",
                    justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
                  }}
                >
                  <div
                    style={{
                      maxWidth: "85%",
                      padding: "8px 12px",
                      borderRadius: msg.role === "user" ? "12px 12px 2px 12px" : "12px 12px 12px 2px",
                      background: msg.role === "user" ? "#e3f2fd" : "#f5f5f5",
                      fontSize: 12,
                      lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {msg.content}
                    {msg.groundedIn && msg.groundedIn.length > 0 && (
                      <div style={{ fontSize: 9, color: "#999", marginTop: 4 }}>
                        grounded in: {msg.groundedIn.join(", ")}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {chatLoading && (
                <div style={{ fontSize: 11, color: "#999", padding: "4px 0" }}>Thinking...</div>
              )}
              <div ref={chatEndRef} />
            </div>
          </div>
        )}
      </div>

      {/* Chat input (visible in chat tab) */}
      {activeTab === "chat" && (
        <div style={{
          padding: "8px 12px",
          borderTop: "1px solid #e0e0e0",
          display: "flex",
          gap: 6,
        }}>
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSendMessage() }}
            placeholder="Ask about your systems..."
            style={{
              flex: 1,
              padding: "8px 12px",
              borderRadius: 8,
              border: "1px solid #ddd",
              fontSize: 12,
              outline: "none",
            }}
          />
          <button
            onClick={handleSendMessage}
            disabled={chatLoading || !inputValue.trim()}
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              border: "none",
              background: "#1976d2",
              color: "#fff",
              fontSize: 12,
              cursor: chatLoading ? "wait" : "pointer",
              opacity: chatLoading || !inputValue.trim() ? 0.5 : 1,
            }}
          >
            Send
          </button>
        </div>
      )}
    </div>
  )
}

export default SidePanel
