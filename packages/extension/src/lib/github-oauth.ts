/**
 * GitHub OAuth — Device Authorization Flow via chrome.identity.
 *
 * Uses chrome.identity.launchWebAuthFlow() to run the GitHub OAuth
 * web application flow. The extension acts as the OAuth client.
 *
 * Flow:
 *   1. Redirect to GitHub /login/oauth/authorize
 *   2. User approves in browser popup
 *   3. GitHub redirects back with authorization code
 *   4. Exchange code for access token via /login/oauth/access_token
 *
 * The client_id is public (embedded in the extension). The token exchange
 * goes through the bridge to keep client_secret server-side.
 */

const GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
const DEFAULT_SCOPES = ["repo", "read:user", "read:org"]

export interface GitHubOAuthConfig {
  clientId: string
  scopes?: string[]
  bridgeUrl?: string
}

export interface GitHubTokenResponse {
  access_token: string
  token_type: string
  scope: string
}

/**
 * Launch GitHub OAuth flow using chrome.identity.
 * Returns the authorization code from the redirect URL.
 */
export async function launchGitHubAuth(
  config: GitHubOAuthConfig,
): Promise<string> {
  const redirectUrl = chrome.identity.getRedirectURL("github")
  const scopes = config.scopes ?? DEFAULT_SCOPES
  const state = crypto.randomUUID()

  const authUrl = new URL(GITHUB_AUTH_URL)
  authUrl.searchParams.set("client_id", config.clientId)
  authUrl.searchParams.set("redirect_uri", redirectUrl)
  authUrl.searchParams.set("scope", scopes.join(" "))
  authUrl.searchParams.set("state", state)

  const responseUrl = await chrome.identity.launchWebAuthFlow({
    url: authUrl.toString(),
    interactive: true,
  })

  if (!responseUrl) {
    throw new Error("OAuth flow was cancelled or returned no URL")
  }

  const params = new URL(responseUrl).searchParams
  const returnedState = params.get("state")
  if (returnedState !== state) {
    throw new Error("OAuth state mismatch — possible CSRF")
  }

  const code = params.get("code")
  if (!code) {
    const error = params.get("error") ?? "unknown"
    const desc = params.get("error_description") ?? ""
    throw new Error(`GitHub OAuth error: ${error} — ${desc}`)
  }

  return code
}

/**
 * Exchange authorization code for access token via the bridge.
 *
 * The bridge holds the client_secret and proxies the token exchange
 * so the secret never touches the extension.
 */
export async function exchangeCodeForToken(
  code: string,
  bridgeUrl = "http://localhost:8400",
): Promise<GitHubTokenResponse> {
  const res = await fetch(`${bridgeUrl}/oauth/github/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Token exchange failed (${res.status}): ${body}`)
  }

  return (await res.json()) as GitHubTokenResponse
}

/**
 * Full GitHub OAuth flow: launch auth → exchange code → return token response.
 */
export async function authenticateGitHub(
  config: GitHubOAuthConfig,
): Promise<GitHubTokenResponse> {
  const code = await launchGitHubAuth(config)
  const token = await exchangeCodeForToken(code, config.bridgeUrl)
  return token
}
