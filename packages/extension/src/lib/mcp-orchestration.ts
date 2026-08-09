/**
 * mcp-orchestration handler — sequence coding archetype.
 *
 * Wraps the L2 executor's plan generation and topological ordering
 * via bridge endpoints.
 * Tools: generate_plan, get_execution_order, validate_plan.
 *
 * Neural coding paradigm: Sequence coding — ordered steps in
 * a defined sequence with validated transitions and dependencies.
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
 * Generate an execution plan for a system based on its trace findings.
 * Wraps the L2 planner → optimizer → executor pipeline.
 */
export async function generatePlan(
  system: string,
  objective?: string,
): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  const cred = await getSystemCredential(system, "mcp-orchestration")
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Mcp-Name": "mcp-orchestration",
  }
  if (cred) headers["Authorization"] = `Bearer ${cred.token}`

  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        tool: "generate_plan",
        params: { system, objective },
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
 * Get the topologically-sorted execution order for pending actions.
 * Respects dependency chains between steps.
 */
export async function getExecutionOrder(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-orchestration" },
      body: JSON.stringify({
        tool: "get_execution_order",
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
 * Validate a plan — check for circular dependencies, missing
 * prerequisites, and unreachable steps.
 */
export async function validatePlan(system: string): Promise<McpResult> {
  const bridgeUrl = await getBridgeUrl()
  try {
    const res = await fetch(`${bridgeUrl}/mcp/tools/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Mcp-Name": "mcp-orchestration" },
      body: JSON.stringify({
        tool: "validate_plan",
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
