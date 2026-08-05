/**
 * mcp-sync handler — synchronous MCP tool invocation via the bridge.
 *
 * Three tools: invoke, batch_invoke, analyze_latency.
 * Calls bridge /mcp/tools/call with vault credential as Authorization header.
 */

import { getSystemCredential } from "./bridge-client"

const DEFAULT_BRIDGE_URL = "http://localhost:8400"

async function getBridgeUrl(): Promise<string> {
  const result = await chrome.storage.sync.get("bridgeUrl")
  return result.bridgeUrl || DEFAULT_BRIDGE_URL
}

interface McpCallResult {
  ok: boolean
  data?: unknown
  error?: string
  latency_ms: number
}

/**
 * Invoke a single API endpoint through the MCP bridge.
 */
export async function invoke(
  system: string,
  endpoint: string,
  method: string,
  params?: Record<string, unknown>,
): Promise<McpCallResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-sync")

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-sync",
  }
  if (cred) {
    headers["Authorization"] = `Bearer ${cred.token}`
  }

  const start = performance.now()
  const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      tool: "invoke",
      arguments: { endpoint, method, params },
      system,
      archetype: "mcp-sync",
    }),
  })
  const latency_ms = Math.round(performance.now() - start)

  if (!res.ok) {
    return { ok: false, error: `Bridge returned ${res.status}`, latency_ms }
  }

  const data = await res.json()
  return { ok: true, data, latency_ms }
}

/**
 * Invoke multiple API endpoints in sequence.
 */
export async function batchInvoke(
  system: string,
  calls: Array<{ endpoint: string; method: string; params?: Record<string, unknown> }>,
): Promise<McpCallResult[]> {
  const results: McpCallResult[] = []
  for (const call of calls) {
    results.push(await invoke(system, call.endpoint, call.method, call.params))
  }
  return results
}

/**
 * Analyze latency patterns for recent API calls via bridge.
 */
export async function analyzeLatency(
  system: string,
  endpointFilter?: string,
  windowMinutes = 60,
): Promise<McpCallResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-sync")

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-sync",
  }
  if (cred) {
    headers["Authorization"] = `Bearer ${cred.token}`
  }

  const start = performance.now()
  const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      tool: "analyze_latency",
      arguments: { endpoint: endpointFilter, window_minutes: windowMinutes },
      system,
      archetype: "mcp-sync",
    }),
  })
  const latency_ms = Math.round(performance.now() - start)

  if (!res.ok) {
    return { ok: false, error: `Bridge returned ${res.status}`, latency_ms }
  }

  const data = await res.json()
  return { ok: true, data, latency_ms }
}
