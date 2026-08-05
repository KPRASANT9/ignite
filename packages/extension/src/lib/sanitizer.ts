/**
 * Auto-sanitization for trace data — redacts secrets and PII at capture time.
 *
 * TypeScript port of ignite_trace/sanitizer.py.
 */

const SECRET_PATTERNS: RegExp[] = [
  /(sk|pk|access|secret|token|key)[_-]?(live|test|prod)?[_-]?[a-zA-Z0-9]{20,}/gi,
  /Bearer\s+[a-zA-Z0-9._-]{20,}/gi,
  /Basic\s+[a-zA-Z0-9+/=]{20,}/gi,
]

const PII_PATTERNS: RegExp[] = [
  /\b\d{3}-\d{2}-\d{4}\b/g,
  /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/g,
]

const REDACTED = "[REDACTED]"
const PII_REDACTED = "[PII_REDACTED]"
const WEB_INPUT_MASKED = "***"

const SENSITIVE_KEYS = ["secret", "password", "token", "authorization", "credential"]

const WEB_SENSITIVE_NAME_PATTERN = new RegExp(
  "(password|passwd|ssn|social.?security|card.?number|cvv|cvc|" +
  "credit.?card|account.?number|routing.?number|pin)",
  "i",
)

export function sanitizeString(value: string): string {
  let result = value
  for (const pattern of SECRET_PATTERNS) {
    pattern.lastIndex = 0
    result = result.replace(pattern, REDACTED)
  }
  for (const pattern of PII_PATTERNS) {
    pattern.lastIndex = 0
    result = result.replace(pattern, PII_REDACTED)
  }
  return result
}

export function sanitizeDict(data: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(data)) {
    const lowerKey = key.toLowerCase()
    if (SENSITIVE_KEYS.some((s) => lowerKey.includes(s))) {
      result[key] = REDACTED
    } else if (Array.isArray(value)) {
      result[key] = value.map((item) => sanitizeValue(item))
    } else if (typeof value === "string") {
      result[key] = sanitizeString(value)
    } else if (typeof value === "object" && value !== null) {
      result[key] = sanitizeDict(value as Record<string, unknown>)
    } else {
      result[key] = value
    }
  }
  return result
}

export function sanitizeValue(value: unknown): unknown {
  if (typeof value === "string") return sanitizeString(value)
  if (Array.isArray(value)) return value.map((item) => sanitizeValue(item))
  if (typeof value === "object" && value !== null) {
    return sanitizeDict(value as Record<string, unknown>)
  }
  return value
}

export function sanitizeWebInput(
  value: string | null,
  elementName?: string,
  inputType?: string,
): string | null {
  if (value === null) return null
  if (inputType && inputType.toLowerCase() === "password") return WEB_INPUT_MASKED
  if (elementName && WEB_SENSITIVE_NAME_PATTERN.test(elementName)) return WEB_INPUT_MASKED
  return value
}

export function isSensitiveField(fieldName: string): boolean {
  const lower = fieldName.toLowerCase()
  return SENSITIVE_KEYS.some((s) => lower.includes(s))
}
