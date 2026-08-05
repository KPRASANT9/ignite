/**
 * mcp-ratelimit handler — rate limit tracking and backoff calculation.
 *
 * Tools: check_budget, record_usage, calculate_backoff.
 * Counters stored in chrome.storage.local for P1 scope.
 */

interface UsageRecord {
  system: string
  count: number
  window_start: number // epoch ms
  window_minutes: number
  limit: number
}

const DEFAULT_WINDOW_MINUTES = 60
const DEFAULT_LIMIT = 5000 // GitHub's default rate limit

async function getUsage(system: string): Promise<UsageRecord> {
  const key = `ratelimit:${system}`
  const result = await chrome.storage.local.get(key)
  const now = Date.now()

  if (result[key]) {
    const record = result[key] as UsageRecord
    const windowMs = record.window_minutes * 60 * 1000
    // Reset if window has elapsed
    if (now - record.window_start > windowMs) {
      return {
        system,
        count: 0,
        window_start: now,
        window_minutes: record.window_minutes,
        limit: record.limit,
      }
    }
    return record
  }

  return {
    system,
    count: 0,
    window_start: now,
    window_minutes: DEFAULT_WINDOW_MINUTES,
    limit: DEFAULT_LIMIT,
  }
}

async function saveUsage(record: UsageRecord): Promise<void> {
  const key = `ratelimit:${record.system}`
  await chrome.storage.local.set({ [key]: record })
}

/**
 * Check remaining budget for a system.
 */
export async function checkBudget(
  system: string,
): Promise<{ remaining: number; limit: number; resets_in_ms: number }> {
  const usage = await getUsage(system)
  const windowMs = usage.window_minutes * 60 * 1000
  const resets_in_ms = Math.max(0, windowMs - (Date.now() - usage.window_start))

  return {
    remaining: Math.max(0, usage.limit - usage.count),
    limit: usage.limit,
    resets_in_ms,
  }
}

/**
 * Record a usage event (call this after each API invocation).
 */
export async function recordUsage(system: string, count = 1): Promise<void> {
  const usage = await getUsage(system)
  usage.count += count
  await saveUsage(usage)
}

/**
 * Calculate backoff delay when rate limited.
 * Uses exponential backoff with jitter.
 */
export function calculateBackoff(
  attempt: number,
  baseMs = 1000,
  maxMs = 60000,
): { delay_ms: number; attempt: number } {
  const exponential = Math.min(maxMs, baseMs * Math.pow(2, attempt))
  const jitter = Math.random() * exponential * 0.1
  return {
    delay_ms: Math.round(exponential + jitter),
    attempt: attempt + 1,
  }
}
