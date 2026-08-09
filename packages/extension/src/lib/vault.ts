/**
 * IAM Vault — encrypted credential storage using WebCrypto + IndexedDB.
 *
 * PBKDF2 key derivation -> AES-256-GCM encryption -> IndexedDB persistence.
 * Tokens encrypted at rest, metadata stored in the clear.
 */

const DB_NAME = "ignite-vault"
const DB_VERSION = 1
const CREDENTIALS_STORE = "credentials"
const META_STORE = "meta"
const PBKDF2_ITERATIONS = 600_000

export type TriggerMode = "pull" | "push" | "autonomous"

export interface VaultRecord {
  system: string
  archetype: string
  tier: string
  mode: TriggerMode
  token: string
  scopes: string[]
  granted_at: string
  expires_at: string | null
}

export interface EncryptedVaultRecord {
  system: string
  archetype: string
  tier: string
  mode: TriggerMode
  ciphertext: ArrayBuffer
  iv: Uint8Array
  scopes: string[]
  granted_at: string
  expires_at: string | null
}

let derivedKey: CryptoKey | null = null

function compositeKey(system: string, archetype: string): string {
  return `${system}:${archetype}`
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(CREDENTIALS_STORE)) {
        db.createObjectStore(CREDENTIALS_STORE)
      }
      if (!db.objectStoreNames.contains(META_STORE)) {
        db.createObjectStore(META_STORE)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function idbGet<T>(db: IDBDatabase, store: string, key: string): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly")
    const req = tx.objectStore(store).get(key)
    req.onsuccess = () => resolve(req.result as T | undefined)
    req.onerror = () => reject(req.error)
  })
}

function idbPut(db: IDBDatabase, store: string, key: string, value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite")
    tx.objectStore(store).put(value, key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

function idbDelete(db: IDBDatabase, store: string, key: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite")
    tx.objectStore(store).delete(key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

function idbGetAll<T>(db: IDBDatabase, store: string): Promise<T[]> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly")
    const req = tx.objectStore(store).getAll()
    req.onsuccess = () => resolve(req.result as T[])
    req.onerror = () => reject(req.error)
  })
}

async function deriveKey(masterPassword: string, salt: Uint8Array): Promise<CryptoKey> {
  const encoder = new TextEncoder()
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    encoder.encode(masterPassword),
    "PBKDF2",
    false,
    ["deriveKey"],
  )
  return crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt,
      iterations: PBKDF2_ITERATIONS,
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  )
}

async function encryptToken(token: string): Promise<{ ciphertext: ArrayBuffer, iv: Uint8Array }> {
  if (!derivedKey) throw new Error("Vault is locked")
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encoder = new TextEncoder()
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    derivedKey,
    encoder.encode(token),
  )
  return { ciphertext, iv }
}

async function decryptToken(ciphertext: ArrayBuffer, iv: Uint8Array): Promise<string> {
  if (!derivedKey) throw new Error("Vault is locked")
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv },
    derivedKey,
    ciphertext,
  )
  return new TextDecoder().decode(plaintext)
}

export async function initVault(masterPassword: string): Promise<void> {
  const db = await openDB()
  let salt = await idbGet<Uint8Array>(db, META_STORE, "salt")
  if (!salt) {
    salt = crypto.getRandomValues(new Uint8Array(32))
    await idbPut(db, META_STORE, "salt", salt)
  }
  derivedKey = await deriveKey(masterPassword, salt)
  db.close()
}

export function lockVault(): void {
  derivedKey = null
}

export function isUnlocked(): boolean {
  return derivedKey !== null
}

export async function storeCredential(record: VaultRecord): Promise<void> {
  if (!derivedKey) throw new Error("Vault is locked")
  const { ciphertext, iv } = await encryptToken(record.token)
  const encrypted: EncryptedVaultRecord = {
    system: record.system,
    archetype: record.archetype,
    tier: record.tier,
    mode: record.mode,
    ciphertext,
    iv,
    scopes: record.scopes,
    granted_at: record.granted_at,
    expires_at: record.expires_at,
  }
  const db = await openDB()
  await idbPut(db, CREDENTIALS_STORE, compositeKey(record.system, record.archetype), encrypted)
  db.close()
}

export async function getCredential(system: string, archetype: string): Promise<VaultRecord | null> {
  if (!derivedKey) throw new Error("Vault is locked")
  const db = await openDB()
  const encrypted = await idbGet<EncryptedVaultRecord>(db, CREDENTIALS_STORE, compositeKey(system, archetype))
  db.close()
  if (!encrypted) return null
  const token = await decryptToken(encrypted.ciphertext, encrypted.iv)
  return {
    system: encrypted.system,
    archetype: encrypted.archetype,
    tier: encrypted.tier,
    mode: encrypted.mode || "push",
    token,
    scopes: encrypted.scopes,
    granted_at: encrypted.granted_at,
    expires_at: encrypted.expires_at,
  }
}

export async function deleteCredential(system: string, archetype: string): Promise<void> {
  const db = await openDB()
  await idbDelete(db, CREDENTIALS_STORE, compositeKey(system, archetype))
  db.close()
}

export async function listCredentials(): Promise<
  Array<{
    system: string
    archetype: string
    tier: string
    scopes: string[]
    granted_at: string
    expires_at: string | null
  }>
> {
  const db = await openDB()
  const all = await idbGetAll<EncryptedVaultRecord>(db, CREDENTIALS_STORE)
  db.close()
  return all.map(({ system, archetype, tier, mode, scopes, granted_at, expires_at }) => ({
    system,
    archetype,
    tier,
    mode: mode || "push",
    scopes,
    granted_at,
    expires_at,
  }))
}
