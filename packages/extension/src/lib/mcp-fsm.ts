/**
 * mcp-fsm handler — place coding archetype.
 *
 * Wraps the L2 optimizer's ExtractedFSM via bridge endpoints.
 * Tools: extract_fsm, get_states, get_transitions.
 *
 * Neural coding paradigm: Place coding — each state is a "place cell"
 * representing the system's position in its lifecycle.
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
 * Extract FSMs from recent traces for a system.
 * Calls the L2 optimizer's _extract_fsms() via bridge.
 */
export async function extractFsm(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-fsm")
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-fsm",
  }
  if (cred) headers["Authorization"] = `Bearer ${cred.token}`

  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers,
      body: JSON.stringify({ tool: "extract_fsm", params: { system }, system }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}

/**
 * Get the states of an extracted FSM for a system.
 */
export async function getStates(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-fsm" },
      body: JSON.stringify({ tool: "get_states", params: { system }, system }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}

/**
 * Get the transitions of an extracted FSM for a system.
 */
export async function getTransitions(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-fsm" },
      body: JSON.stringify({ tool: "get_transitions", params: { system }, system }),
    })
    if (!res.ok) return { ok: false, error: `Bridge returned ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}
