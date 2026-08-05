/**
 * Bridge Client — communicates with the Python L2 service.
 *
 * REST POST for trace ingestion, SSE for spike signal streaming.
 * Used by the service worker and side panel.
 */

import { storeCredential as vaultStore, getCredential as vaultGet, deleteCredential as vaultDelete, type VaultRecord } from "./vault"

const DEFAULT_BRIDGE_URL = "http://localhost:8400"

export interface SpikeSignal {
  spike_type: string
  confidence: number
  urgency: string
  modalities: string[]
  chain: string[]
  action_space: string[]
  source_system: string
  correlation_boost: number
  description: string
}

export interface TraceResponse {
  ok: boolean
  trace_id: string | null
  span_count: number
  errors: string[]
  spikes: SpikeSignal[]
  analysis_summary: Record<string, unknown> | null
}

/**
 * Send a trace to the L2 pipeline for analysis.
 */
export async function ingestTrace(
  traceData: Record<string, unknown>,
  bridgeUrl = DEFAULT_BRIDGE_URL,
): Promise<TraceResponse> {
  const res = await fetch(`${bridgeUrl}/traces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(traceData),
  })
  return (await res.json()) as TraceResponse
}

/**
 * Check if the L2 bridge service is running.
 */
export async function checkHealth(
  bridgeUrl = DEFAULT_BRIDGE_URL,
): Promise<boolean> {
  try {
    const res = await fetch(`${bridgeUrl}/health`, { method: "GET" })
    return res.ok
  } catch {
    return false
  }
}

export type SpikeCallback = (spike: SpikeSignal) => void

/**
 * Subscribe to real-time spike signals via SSE.
 * Returns an abort function to close the connection.
 */
export function subscribeSpikeStream(
  onSpike: SpikeCallback,
  bridgeUrl = DEFAULT_BRIDGE_URL,
): () => void {
  const eventSource = new EventSource(`${bridgeUrl}/traces/spikes`)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === "spike" && data.spike) {
        onSpike(data.spike as SpikeSignal)
      }
    } catch {
      // Ignore malformed SSE data
    }
  }

  return () => eventSource.close()
}

// --- Credential management (vault-backed) ---

/**
 * Store a credential in the vault for a given system/archetype.
 */
export async function storeSystemCredential(
  system: string,
  archetype: string,
  token: string,
  scopes: string[],
  tier = "user",
  expiresAt: string | null = null,
): Promise<void> {
  await vaultStore({
    system,
    archetype,
    tier,
    token,
    scopes,
    granted_at: new Date().toISOString(),
    expires_at: expiresAt,
  })
}

/**
 * Retrieve a decrypted credential for a system/archetype.
 */
export async function getSystemCredential(
  system: string,
  archetype: string,
): Promise<VaultRecord | null> {
  return vaultGet(system, archetype)
}

/**
 * Remove a credential from the vault.
 */
export async function deleteSystemCredential(
  system: string,
  archetype: string,
): Promise<void> {
  await vaultDelete(system, archetype)
}

/**
 * Send a trace with an Authorization header from the vault.
 */
export async function ingestTraceAuthenticated(
  traceData: Record<string, unknown>,
  system: string,
  archetype: string,
  bridgeUrl = DEFAULT_BRIDGE_URL,
): Promise<TraceResponse> {
  const cred = await vaultGet(system, archetype)
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (cred) {
    headers["Authorization"] = `Bearer ${cred.token}`
  }
  const res = await fetch(`${bridgeUrl}/traces/web`, {
    method: "POST",
    headers,
    body: JSON.stringify(traceData),
  })
  return (await res.json()) as TraceResponse
}
