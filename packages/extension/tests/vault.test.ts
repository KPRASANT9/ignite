import { describe, it, expect } from "vitest"
import {
  sanitizeString,
  sanitizeDict,
  sanitizeValue,
  sanitizeWebInput,
  isSensitiveField,
} from "../src/lib/sanitizer"

describe("sanitizeString", () => {
  it("redacts Bearer tokens", () => {
    const input = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123"
    const result = sanitizeString(input)
    expect(result).toContain("[REDACTED]")
    expect(result).not.toContain("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
  })

  it("redacts API keys", () => {
    const input = "key_live_abcdefghijklmnopqrstuvwxyz"
    const result = sanitizeString(input)
    expect(result).toBe("[REDACTED]")
  })

  it("redacts secret keys with various prefixes", () => {
    const input = "sk_test_1234567890abcdefghij"
    expect(sanitizeString(input)).toBe("[REDACTED]")
  })

  it("redacts SSNs", () => {
    const input = "SSN is 123-45-6789"
    const result = sanitizeString(input)
    expect(result).toContain("[PII_REDACTED]")
    expect(result).not.toContain("123-45-6789")
  })

  it("redacts credit card numbers", () => {
    const input = "Card: 4111 1111 1111 1111"
    const result = sanitizeString(input)
    expect(result).toContain("[PII_REDACTED]")
    expect(result).not.toContain("4111 1111 1111 1111")
  })

  it("leaves clean strings untouched", () => {
    const input = "Hello, this is a normal string"
    expect(sanitizeString(input)).toBe(input)
  })
})

describe("sanitizeWebInput", () => {
  it("masks password fields by input type", () => {
    expect(sanitizeWebInput("hunter2", undefined, "password")).toBe("***")
  })

  it("masks password fields by element name", () => {
    expect(sanitizeWebInput("hunter2", "user_password", undefined)).toBe("***")
  })

  it("masks SSN fields by element name", () => {
    expect(sanitizeWebInput("123456789", "ssn_field", undefined)).toBe("***")
  })

  it("masks credit card fields by element name", () => {
    expect(sanitizeWebInput("4111111111111111", "card_number", undefined)).toBe("***")
  })

  it("returns null for null input", () => {
    expect(sanitizeWebInput(null, "password", "text")).toBeNull()
  })

  it("passes through non-sensitive fields", () => {
    expect(sanitizeWebInput("hello", "username", "text")).toBe("hello")
  })
})

describe("sanitizeDict", () => {
  it("redacts keys containing sensitive names", () => {
    const data = {
      username: "alice",
      password: "supersecret",
      api_token: "tok_abc123",
      authorization: "Bearer xyz",
    }
    const result = sanitizeDict(data)
    expect(result.username).toBe("alice")
    expect(result.password).toBe("[REDACTED]")
    expect(result.api_token).toBe("[REDACTED]")
    expect(result.authorization).toBe("[REDACTED]")
  })

  it("redacts sensitive keys recursively", () => {
    const data = {
      config: {
        nested: {
          secret_key: "abc123",
          name: "test",
        },
      },
    }
    const result = sanitizeDict(data) as Record<string, Record<string, Record<string, string>>>
    expect(result.config.nested.secret_key).toBe("[REDACTED]")
    expect(result.config.nested.name).toBe("test")
  })

  it("sanitizes string values for patterns", () => {
    const data = {
      log: "Got Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.longtoken here",
    }
    const result = sanitizeDict(data)
    expect(result.log).toContain("[REDACTED]")
  })

  it("handles arrays in dict values", () => {
    const data = {
      items: ["normal", "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.longtoken"],
    }
    const result = sanitizeDict(data)
    const items = result.items as string[]
    expect(items[0]).toBe("normal")
    expect(items[1]).toContain("[REDACTED]")
  })
})

describe("isSensitiveField", () => {
  it("detects password fields", () => {
    expect(isSensitiveField("password")).toBe(true)
    expect(isSensitiveField("user_password")).toBe(true)
    expect(isSensitiveField("PASSWORD")).toBe(true)
  })

  it("detects token fields", () => {
    expect(isSensitiveField("api_token")).toBe(true)
    expect(isSensitiveField("access_token")).toBe(true)
  })

  it("detects secret fields", () => {
    expect(isSensitiveField("client_secret")).toBe(true)
    expect(isSensitiveField("SECRET_KEY")).toBe(true)
  })

  it("detects authorization fields", () => {
    expect(isSensitiveField("authorization")).toBe(true)
    expect(isSensitiveField("Authorization")).toBe(true)
  })

  it("detects credential fields", () => {
    expect(isSensitiveField("credential")).toBe(true)
    expect(isSensitiveField("user_credentials")).toBe(true)
  })

  it("returns false for non-sensitive fields", () => {
    expect(isSensitiveField("username")).toBe(false)
    expect(isSensitiveField("email")).toBe(false)
    expect(isSensitiveField("name")).toBe(false)
  })
})

// Vault tests require IndexedDB + WebCrypto (browser/extension environment).
// They cannot run in Node.js without polyfills like fake-indexeddb.
// To test vault functions:
//   1. Install fake-indexeddb: npm i -D fake-indexeddb
//   2. Add to vitest setup: import "fake-indexeddb/auto"
//   3. WebCrypto is available in Node 18+ via globalThis.crypto
