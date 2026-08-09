/**
 * Action Space Mapper — maps spike action_space values to executable actions.
 *
 * Each spike signal includes an action_space array of suggested responses.
 * This module maps those strings to concrete MCP tool calls or extension actions.
 */

export interface MappedAction {
  label: string
  description: string
  handler: () => Promise<unknown>
}

/**
 * Map a spike's action_space values to executable actions.
 */
export function mapActions(
  actionSpace: string[],
  system: string,
  archetype: string,
): MappedAction[] {
  return actionSpace
    .map((action) => mapSingleAction(action, system, archetype))
    .filter((a): a is MappedAction => a !== null)
}

function mapSingleAction(
  action: string,
  system: string,
  archetype: string,
): MappedAction | null {
  switch (action) {
    case "reauth_retry":
      return {
        label: "Re-authenticate",
        description: "Refresh credentials and retry the failed operation",
        handler: async () => {
          // Trigger OAuth re-flow via service worker
          return chrome.runtime.sendMessage({
            type: "OAUTH_GITHUB",
            config: { clientId: "" }, // populated from storage at runtime
          })
        },
      }

    case "backoff_retry":
      return {
        label: "Retry with backoff",
        description: "Wait for rate limit window, then retry",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype: "mcp-ratelimit",
            tool: "calculate_backoff",
            params: { system },
          })
        },
      }

    case "escalate_human":
      return {
        label: "Needs attention",
        description: "This requires manual review",
        handler: async () => {
          // Open side panel with the spike details
          await chrome.runtime.sendMessage({
            type: "SHOW_SIDE_PANEL",
            focus: "spike",
          })
          return { escalated: true }
        },
      }

    case "inspect_endpoint":
      return {
        label: "Inspect endpoint",
        description: "View detailed latency and error data for this endpoint",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype,
            tool: "analyze_latency",
            params: { system },
          })
        },
      }

    case "batch_check":
      return {
        label: "Batch health check",
        description: "Check health of related endpoints",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype,
            tool: "batch_invoke",
            params: {
              calls: [
                { endpoint: "/rate_limit", method: "GET" },
                { endpoint: "/user", method: "GET" },
              ],
            },
          })
        },
      }

    case "reduce_rate":
      return {
        label: "Reduce capture rate",
        description: "Throttle trace capture to stay within rate limits",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype: "mcp-ratelimit",
            tool: "check_budget",
            params: { system },
          })
        },
      }

    case "increase_timeout":
      return {
        label: "Increase timeout",
        description: "Retry with extended timeout for slow endpoints",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype,
            tool: "invoke",
            params: { system, timeout_ms: 30000 },
          })
        },
      }

    case "investigate":
      return {
        label: "Investigate",
        description: "Analyze endpoint behavior and recent errors",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype,
            tool: "analyze_latency",
            params: { system },
          })
        },
      }

    case "log_escalate":
      return {
        label: "Log & escalate",
        description: "Record the error and flag for manual review",
        handler: async () => {
          await chrome.runtime.sendMessage({
            type: "SHOW_SIDE_PANEL",
            focus: "spike",
          })
          return { escalated: true, logged: true }
        },
      }

    case "check_idempotency":
      return {
        label: "Check idempotency",
        description: "Verify if the failed mutation can be safely retried",
        handler: async () => {
          return chrome.runtime.sendMessage({
            type: "MCP_INVOKE",
            system,
            archetype,
            tool: "invoke",
            params: { system, check: "idempotency" },
          })
        },
      }

    case "monitor_outcome":
      return {
        label: "Monitor",
        description: "Watch this state transition for anomalies",
        handler: async () => {
          // No-op for P1 — logs the intent, monitoring infra in P2
          console.log(`[IGNITE] Monitoring outcome for ${system}`)
          return { monitoring: true }
        },
      }

    case "log_transition":
      return {
        label: "Log transition",
        description: "Record this state transition in trace history",
        handler: async () => {
          console.log(`[IGNITE] State transition logged for ${system}`)
          return { logged: true }
        },
      }

    case "log_pattern":
      return {
        label: "Log pattern",
        description: "Record this cross-modality pattern",
        handler: async () => {
          console.log(`[IGNITE] Cross-modality pattern logged for ${system}`)
          return { logged: true }
        },
      }

    case "alert_human":
      return {
        label: "Alert",
        description: "Surface this issue for human attention",
        handler: async () => {
          await chrome.runtime.sendMessage({
            type: "SHOW_SIDE_PANEL",
            focus: "spike",
          })
          return { alerted: true }
        },
      }

    default:
      return null
  }
}
