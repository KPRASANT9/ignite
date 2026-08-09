/**
 * ProfileManager — unified N_systems × 6_archetypes permission matrix.
 *
 * Aggregates vault credentials + onboarding state into a single view
 * of every system's permission matrix. Each cell represents one
 * {system, archetype} pair with tier, mode, token status, and scopes.
 *
 * Used by:
 * - BCIEngine: reads trigger mode for spike processing decisions
 * - Dashboard (popup.tsx): renders permission matrix UI
 * - InlineChat: provides profile context for grounded responses
 */

import { listCredentials, type TriggerMode } from "./vault"
import type { OnboardingStore, SystemOnboardingRecord } from "./onboarding-fsm"

// --- Constants ---

export const ALL_ARCHETYPES = [
  "mcp-sync",
  "mcp-ratelimit",
  "mcp-fsm",
  "mcp-async",
  "mcp-scoring",
  "mcp-orchestration",
] as const

export type Archetype = (typeof ALL_ARCHETYPES)[number]

// --- Types ---

export interface PermissionCell {
  system: string
  archetype: Archetype
  tier: string
  mode: TriggerMode
  tokenStatus: "active" | "expired" | "none"
  scopes: string[]
  grantedAt: string | null
  expiresAt: string | null
}

export interface SystemProfile {
  systemId: string
  onboardingState: string
  traces: number
  confidence: number
  archetypes: PermissionCell[]
}

export interface PermissionMatrix {
  systems: SystemProfile[]
  totalSystems: number
  totalCredentials: number
}

// --- ProfileManager ---

/**
 * Build the full permission matrix from vault + onboarding state.
 * Returns N systems × 6 archetypes.
 */
export async function getPermissionMatrix(): Promise<PermissionMatrix> {
  // Load vault credentials (metadata only — no decryption needed)
  const credentials = await listCredentials()

  // Load onboarding state
  const storeResult = await chrome.storage.local.get("onboarding")
  const store: OnboardingStore = storeResult.onboarding || { systems: {} }

  // Collect all known system IDs from both sources
  const systemIds = new Set<string>()
  for (const cred of credentials) {
    systemIds.add(cred.system)
  }
  for (const id of Object.keys(store.systems)) {
    systemIds.add(id)
  }

  // Build profile for each system
  const systems: SystemProfile[] = []
  for (const systemId of systemIds) {
    const record = store.systems[systemId]
    const archetypes: PermissionCell[] = ALL_ARCHETYPES.map((archetype) => {
      const cred = credentials.find(
        (c) => c.system === systemId && c.archetype === archetype,
      )

      let tokenStatus: PermissionCell["tokenStatus"] = "none"
      if (cred) {
        if (cred.expires_at && new Date(cred.expires_at) < new Date()) {
          tokenStatus = "expired"
        } else {
          tokenStatus = "active"
        }
      }

      return {
        system: systemId,
        archetype,
        tier: cred?.tier || "none",
        mode: (cred?.mode as TriggerMode) || "push",
        tokenStatus,
        scopes: cred?.scopes || [],
        grantedAt: cred?.granted_at || null,
        expiresAt: cred?.expires_at || null,
      }
    })

    systems.push({
      systemId,
      onboardingState: record?.state || "IDLE",
      traces: record?.traces || 0,
      confidence: record?.confidence || 0,
      archetypes,
    })
  }

  return {
    systems,
    totalSystems: systems.length,
    totalCredentials: credentials.length,
  }
}

/**
 * Get the permission matrix for a single system.
 */
export async function getSystemProfile(
  systemId: string,
): Promise<SystemProfile | null> {
  const matrix = await getPermissionMatrix()
  return matrix.systems.find((s) => s.systemId === systemId) || null
}

/**
 * Get the trigger mode for a specific system/archetype.
 * Used by BCIEngine to decide whether to process spikes.
 */
export async function getTriggerMode(
  systemId: string,
  archetype: Archetype,
): Promise<TriggerMode> {
  const profile = await getSystemProfile(systemId)
  if (!profile) return "push" // default
  const cell = profile.archetypes.find((a) => a.archetype === archetype)
  return cell?.mode || "push"
}

/**
 * Update the trigger mode for a system/archetype in the vault.
 */
export async function setTriggerMode(
  systemId: string,
  archetype: Archetype,
  mode: TriggerMode,
): Promise<void> {
  const { getCredential, storeCredential } = await import("./vault")
  const existing = await getCredential(systemId, archetype)
  if (existing) {
    existing.mode = mode
    await storeCredential(existing)
  } else {
    // Create a placeholder credential with just the mode set
    await storeCredential({
      system: systemId,
      archetype,
      tier: "none",
      mode,
      token: "",
      scopes: [],
      granted_at: new Date().toISOString(),
      expires_at: null,
    })
  }
}

/**
 * Get a summary of all active credentials across systems.
 * Useful for the dashboard overview.
 */
export async function getCredentialSummary(): Promise<{
  totalActive: number
  totalExpired: number
  totalNone: number
  bySystem: Record<string, { active: number; expired: number; none: number }>
}> {
  const matrix = await getPermissionMatrix()
  const result = {
    totalActive: 0,
    totalExpired: 0,
    totalNone: 0,
    bySystem: {} as Record<string, { active: number; expired: number; none: number }>,
  }

  for (const system of matrix.systems) {
    const counts = { active: 0, expired: 0, none: 0 }
    for (const cell of system.archetypes) {
      counts[cell.tokenStatus]++
      result[`total${cell.tokenStatus.charAt(0).toUpperCase()}${cell.tokenStatus.slice(1)}` as keyof typeof result] =
        (result[`total${cell.tokenStatus.charAt(0).toUpperCase()}${cell.tokenStatus.slice(1)}` as keyof typeof result] as number) + 1
    }
    result.bySystem[system.systemId] = counts
  }

  return result
}
