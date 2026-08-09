/**
 * MCP Tool Registry — maps {system, archetype} to available tool definitions.
 *
 * All 6 archetype MCPs registered:
 * - mcp-sync: synchronous API calls (rate coding)
 * - mcp-ratelimit: rate limiting and backoff (feedback coding)
 * - mcp-fsm: state machine extraction (place coding)
 * - mcp-async: temporal correlation (temporal coding)
 * - mcp-scoring: cross-modality scoring (population coding)
 * - mcp-orchestration: execution planning (sequence coding)
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
  // --- mcp-ratelimit (feedback coding) ---
  {
    system: "*",
    archetype: "mcp-ratelimit",
    tools: [
      {
        name: "check_budget",
        description: "Check remaining request budget for a system",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "record_usage",
        description: "Record API usage count for rate tracking",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" }, count: { type: "number" } },
          required: ["system"],
        },
      },
      {
        name: "calculate_backoff",
        description: "Calculate exponential backoff delay after rate limit hit",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" }, retry_count: { type: "number" } },
          required: ["system"],
        },
      },
    ],
  },
  // --- mcp-fsm (place coding) ---
  {
    system: "*",
    archetype: "mcp-fsm",
    tools: [
      {
        name: "extract_fsm",
        description: "Extract finite state machines from recent traces",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "get_states",
        description: "List states of the extracted FSM for a system",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "get_transitions",
        description: "List transitions of the extracted FSM for a system",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
    ],
  },
  // --- mcp-async (temporal coding) ---
  {
    system: "*",
    archetype: "mcp-async",
    tools: [
      {
        name: "correlate_spans",
        description: "Correlate spans across modalities within a time window",
        inputSchema: {
          type: "object",
          properties: {
            system: { type: "string" },
            window_ms: { type: "number", description: "Correlation window in milliseconds (default 5000)" },
          },
          required: ["system"],
        },
      },
      {
        name: "get_temporal_patterns",
        description: "Analyze temporal patterns — cadence, bursts, quiet periods",
        inputSchema: {
          type: "object",
          properties: {
            system: { type: "string" },
            window_minutes: { type: "number", description: "Analysis window in minutes (default 60)" },
          },
          required: ["system"],
        },
      },
      {
        name: "detect_cadence",
        description: "Detect the request cadence stability for PROFILING→ACTIVE transition",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
    ],
  },
  // --- mcp-scoring (population coding) ---
  {
    system: "*",
    archetype: "mcp-scoring",
    tools: [
      {
        name: "compute_boost",
        description: "Compute cross-modality correlation boost (1x/1.5x/2.0x)",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "score_system",
        description: "Score overall system health from spikes, modalities, and confidence",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "get_modality_coverage",
        description: "List observed modalities and their signal rates for a system",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
    ],
  },
  // --- mcp-orchestration (sequence coding) ---
  {
    system: "*",
    archetype: "mcp-orchestration",
    tools: [
      {
        name: "generate_plan",
        description: "Generate an execution plan from trace findings",
        inputSchema: {
          type: "object",
          properties: {
            system: { type: "string" },
            objective: { type: "string", description: "Optional objective for the plan" },
          },
          required: ["system"],
        },
      },
      {
        name: "get_execution_order",
        description: "Get topologically-sorted execution order for pending actions",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
        },
      },
      {
        name: "validate_plan",
        description: "Validate plan for circular dependencies and missing prerequisites",
        inputSchema: {
          type: "object",
          properties: { system: { type: "string" } },
          required: ["system"],
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
    (e) => (e.system === system || e.system === "*") && e.archetype === archetype,
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
