/**
 * MCP Tool Registry — maps {system, archetype} to available tool definitions.
 *
 * P1 scope: GitHub mcp-sync tools only.
 * Future: dynamic registration from bridge /mcp/tools/list responses.
 */

export interface McpToolDefinition {
  name: string
  description: string
  inputSchema: Record<string, unknown>
}

export interface McpArchetypeEntry {
  system: string
  archetype: string
  tools: McpToolDefinition[]
}

const registry: McpArchetypeEntry[] = [
  {
    system: "github",
    archetype: "mcp-sync",
    tools: [
      {
        name: "invoke",
        description: "Invoke a single GitHub API endpoint",
        inputSchema: {
          type: "object",
          properties: {
            endpoint: { type: "string", description: "API path, e.g. /repos/{owner}/{repo}/issues" },
            method: { type: "string", enum: ["GET", "POST", "PUT", "PATCH", "DELETE"] },
            params: { type: "object", description: "Query params or request body" },
          },
          required: ["endpoint", "method"],
        },
      },
      {
        name: "batch_invoke",
        description: "Invoke multiple GitHub API endpoints in sequence",
        inputSchema: {
          type: "object",
          properties: {
            calls: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  endpoint: { type: "string" },
                  method: { type: "string" },
                  params: { type: "object" },
                },
                required: ["endpoint", "method"],
              },
            },
          },
          required: ["calls"],
        },
      },
      {
        name: "analyze_latency",
        description: "Analyze latency patterns for recent GitHub API calls",
        inputSchema: {
          type: "object",
          properties: {
            endpoint_filter: { type: "string", description: "Optional endpoint path prefix to filter" },
            window_minutes: { type: "number", description: "Analysis window in minutes (default 60)" },
          },
        },
      },
    ],
  },
]

/**
 * Look up tools for a given system and archetype.
 */
export function getTools(system: string, archetype: string): McpToolDefinition[] {
  const entry = registry.find(
    (e) => e.system === system && e.archetype === archetype,
  )
  return entry?.tools ?? []
}

/**
 * Look up a specific tool by name for a given system/archetype.
 */
export function getTool(
  system: string,
  archetype: string,
  toolName: string,
): McpToolDefinition | null {
  const tools = getTools(system, archetype)
  return tools.find((t) => t.name === toolName) ?? null
}

/**
 * Get all registered system/archetype pairs.
 */
export function listRegistered(): Array<{ system: string; archetype: string }> {
  return registry.map(({ system, archetype }) => ({ system, archetype }))
}

/**
 * Check if a system has any MCP tools registered for any archetype.
 */
export function hasTools(system: string): boolean {
  return registry.some((e) => e.system === system)
}
