/**
 * mcp-scoring handler — population coding archetype.
 *
 * Wraps the L2 spike.py's compute_correlation_boost() and
 * cross-modality scoring via bridge endpoints.
 * Tools: compute_boost, score_system, get_modality_coverage.
 *
 * Neural coding paradigm: Population coding — ensemble decode
 * from multiple modalities. More modalities = higher confidence.
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
 * Compute the correlation boost for a system based on modality count.
 * Wraps spike.py compute_correlation_boost():
 *   1 modality = 1.0x, 2 = 1.5x, 3+ = 2.0x
 */
export async function computeBoost(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-scoring")
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-scoring",
  }
  if (cred) headers["Authorization"] = `Bearer ${cred.token}`

  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        tool: "compute_boost",
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

/**
 * Score a system's overall health based on spike signals,
 * modality coverage, and confidence metrics.
 */
export async function scoreSystem(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-scoring" },
      body: JSON.stringify({
        tool: "score_system",
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

/**
 * Get modality coverage for a system — which modalities have been
 * observed (web, api, cli, db, message) and their signal rates.
 */
export async function getModalityCoverage(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-scoring" },
      body: JSON.stringify({
        tool: "get_modality_coverage",
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
