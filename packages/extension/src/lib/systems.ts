/**
 * System Detection — URL pattern registry for known digital systems.
 *
 * Maps URL patterns to system identifiers. The SystemDetector content script
 * uses this registry to auto-detect which system the user is on.
 *
 * Strategy 1 of 3 (covers ~80% of known systems).
 * Strategy 2: DOM fingerprinting (SPA/MPA/hybrid detection)
 * Strategy 3: Login activity tracking (auto-register new systems)
 */

export interface SystemProfile {
  id: string
  name: string
  urlPatterns: RegExp[]
  modalities: string[]
  archetypes: string[]
}

export const SYSTEM_REGISTRY: SystemProfile[] = [
  {
    id: "jira",
    name: "JIRA",
    urlPatterns: [
      /^https:\/\/[^/]+\.atlassian\.net/,
      /^https:\/\/jira\./,
    ],
    modalities: ["web", "api"],
    archetypes: ["mcp-sync", "mcp-orchestration"],
  },
  {
    id: "github",
    name: "GitHub",
    urlPatterns: [
      /^https:\/\/github\.com/,
      /^https:\/\/[^/]+\.github\.com/,
    ],
    modalities: ["web", "api", "cli"],
    archetypes: ["mcp-sync", "mcp-fsm"],
  },
  {
    id: "databricks",
    name: "Databricks",
    urlPatterns: [
      /^https:\/\/[^/]+\.cloud\.databricks\.com/,
      /^https:\/\/[^/]+\.azuredatabricks\.net/,
    ],
    modalities: ["web", "api", "cli"],
    archetypes: ["mcp-async", "mcp-fsm", "mcp-orchestration", "mcp-ratelimit"],
  },
  {
    id: "aws-console",
    name: "AWS Console",
    urlPatterns: [
      /^https:\/\/[^/]+\.console\.aws\.amazon\.com/,
      /^https:\/\/console\.aws\.amazon\.com/,
    ],
    modalities: ["web", "api"],
    archetypes: ["mcp-sync", "mcp-fsm", "mcp-async", "mcp-orchestration"],
  },
  {
    id: "gitlab",
    name: "GitLab",
    urlPatterns: [
      /^https:\/\/gitlab\.com/,
      /^https:\/\/[^/]+\.gitlab\./,
    ],
    modalities: ["web", "api", "cli"],
    archetypes: ["mcp-sync", "mcp-fsm"],
  },
]

/**
 * Detect which system the current URL belongs to.
 * Returns null if no match found.
 */
export function detectSystem(url: string): SystemProfile | null {
  for (const profile of SYSTEM_REGISTRY) {
    for (const pattern of profile.urlPatterns) {
      if (pattern.test(url)) {
        return profile
      }
    }
  }
  return null
}
