/**
 * mcp-async handler — temporal coding archetype.
 *
 * Wraps the L2 spike.py's correlate_spans() via bridge endpoints.
 * Tools: correlate_spans, get_temporal_patterns, detect_cadence.
 *
 * Neural coding paradigm: Temporal coding — timing patterns in
 * system interactions reveal cadence, burst behavior, and anomalies.
 */

import { getSystemCredential } from "./bridge-client"

const DEFAULT_BRIDGE_URL = "http://localhost:8400"

async function getBridgeUrl(): Promise<string> {
  const result = await chrome.storage.sync.get("bridgeUrl")
  return result.bridgeUrl || DEFAULT_BRIDGE_URL
}

interface McpResult {
  ok: boolean
  data?: unknown
  error?: string
}

/**
 * Correlate spans across modalities within a time window.
 * Wraps spike.py correlate_spans(window_ms=5000).
 */
export async function correlateSpans(
  system: string,
  windowMs = 5000,
): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-async")
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-async",
  }
  if (cred) headers["Authorization"] = `Bearer ${cred.token}`

  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        tool: "correlate_spans",
        params: { system, window_ms: windowMs },
        system,
      }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}

/**
 * Get temporal patterns from recent traces — request cadence,
 * burst detection, quiet periods.
 */
export async function getTemporalPatterns(
  system: string,
  windowMinutes = 60,
): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-async" },
      body: JSON.stringify({
        tool: "get_temporal_patterns",
        params: { system, window_minutes: windowMinutes },
        system,
      }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}

/**
 * Detect the request cadence for a system — average interval,
 * variance, and whether the pattern is stable enough for PROFILING→ACTIVE.
 */
export async function detectCadence(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-async" },
      body: JSON.stringify({
        tool: "detect_cadence",
        params: { system },
        system,
      }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}
