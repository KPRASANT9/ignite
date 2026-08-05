import { describe, it, expect } from "vitest"
import { getTools, getTool, listRegistered, hasTools } from "../src/lib/mcp-registry"

describe("MCP Registry", () => {
  it("returns tools for github/mcp-sync", () => {
    const tools = getTools("github", "mcp-sync")
    expect(tools.length).toBe(3)
    expect(tools.map((t) => t.name)).toEqual(["invoke", "batch_invoke", "analyze_latency"])
  })

  it("returns empty array for unknown system", () => {
    expect(getTools("unknown", "mcp-sync")).toEqual([])
  })

  it("returns empty array for unknown archetype", () => {
    expect(getTools("github", "unknown")).toEqual([])
  })

  it("looks up a specific tool by name", () => {
    const tool = getTool("github", "mcp-sync", "invoke")
    expect(tool).not.toBeNull()
    expect(tool!.name).toBe("invoke")
    expect(tool!.inputSchema).toBeDefined()
  })

  it("returns null for unknown tool name", () => {
    expect(getTool("github", "mcp-sync", "nonexistent")).toBeNull()
  })

  it("lists all registered system/archetype pairs", () => {
    const registered = listRegistered()
    expect(registered).toContainEqual({ system: "github", archetype: "mcp-sync" })
  })

  it("checks if a system has tools", () => {
    expect(hasTools("github")).toBe(true)
    expect(hasTools("unknown")).toBe(false)
  })
})
