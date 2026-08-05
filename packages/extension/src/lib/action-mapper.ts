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

    default:
      return null
  }
}
