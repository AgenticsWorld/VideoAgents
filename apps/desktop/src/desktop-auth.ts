import {createHash, randomBytes} from 'node:crypto'
import {existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync} from 'node:fs'
import path from 'node:path'
import {safeStorage} from 'electron'
import type {BuildInfo} from './desktop-update'

export const OAUTH_CLIENT_ID = 'videoagents-desktop'

export interface ServiceRegion {
  distribution: 's3' | 'oss'
  ssoOrigin: string
  apiOrigin: string
  openrouterWrapperUrl: string
  redirectUri: string
}

export interface UserAccount {
  email: string
  balance: number
}

export interface StoredAuth {
  token: string
  onboarded: boolean
}

interface AuthFile {
  schema: 1
  protected: boolean
  token: string
  onboarded: boolean
}

export interface PkceSession {
  verifier: string
  challenge: string
  state: string
}

export class AgenticsApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export function serviceRegion(build: BuildInfo): ServiceRegion {
  const requested = process.env.VIDEOAGENTS_DISTRIBUTION
  const distribution = requested === 'oss' || requested === 's3'
    ? requested : build.distribution === 'oss' ? 'oss' : 's3'
  return distribution === 'oss' ? {
    distribution,
    ssoOrigin: 'https://sso.shumati.cn',
    apiOrigin: 'https://api.shumati.cn',
    openrouterWrapperUrl: 'https://wrapper.shumati.cn/wrapper/openrouter',
    redirectUri: 'videoagents://shumati.cn',
  } : {
    distribution,
    ssoOrigin: 'https://sso.agentics.world',
    apiOrigin: 'https://api.agentics.world',
    openrouterWrapperUrl: 'https://api.agentics.world/wrapper/openrouter',
    redirectUri: 'videoagents://agentics.world',
  }
}

export function createPkceSession(): PkceSession {
  const verifier = randomBytes(32).toString('base64url')
  return {
    verifier,
    challenge: createHash('sha256').update(verifier).digest('base64url'),
    state: randomBytes(24).toString('base64url'),
  }
}

export function authorizationUrl(region: ServiceRegion, session: PkceSession): string {
  const query = new URLSearchParams({
    client_id: OAUTH_CLIENT_ID,
    redirect_uri: region.redirectUri,
    response_type: 'code',
    code_challenge: session.challenge,
    code_challenge_method: 'S256',
    state: session.state,
  })
  return `${region.ssoOrigin}/#/authorize?${query.toString()}`
}

export function parseAuthorizationCallback(
  value: string,
  expectedState: string,
  expectedRedirectUri: string,
): {code: string} | undefined {
  let callback: URL
  try {
    callback = new URL(value)
  } catch {
    return undefined
  }
  const expected = new URL(expectedRedirectUri)
  if (callback.protocol !== expected.protocol || callback.hostname !== expected.hostname) return undefined
  const code = callback.searchParams.get('code') || ''
  const state = callback.searchParams.get('state') || ''
  if (!code || state !== expectedState) return undefined
  return {code}
}

function authPath(userData: string): string {
  return path.join(userData, 'auth.json')
}

export function loadStoredAuth(userData: string): StoredAuth | undefined {
  const target = authPath(userData)
  if (!existsSync(target)) return undefined
  try {
    const value = JSON.parse(readFileSync(target, 'utf8')) as Partial<AuthFile>
    if (value.schema !== 1 || typeof value.token !== 'string' || !value.token) return undefined
    const token = value.protected
      ? safeStorage.decryptString(Buffer.from(value.token, 'base64'))
      : value.token
    return token ? {token, onboarded: Boolean(value.onboarded)} : undefined
  } catch (error) {
    console.warn(`[auth] saved session is unavailable: ${String(error)}`)
    return undefined
  }
}

export function saveStoredAuth(userData: string, auth: StoredAuth): void {
  const target = authPath(userData)
  const temporary = `${target}.${process.pid}.tmp`
  mkdirSync(path.dirname(target), {recursive: true})
  const protect = safeStorage.isEncryptionAvailable()
  const value: AuthFile = {
    schema: 1,
    protected: protect,
    token: protect ? safeStorage.encryptString(auth.token).toString('base64') : auth.token,
    onboarded: auth.onboarded,
  }
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {encoding: 'utf8', mode: 0o600})
  renameSync(temporary, target)
}

export function clearStoredAuth(userData: string): void {
  rmSync(authPath(userData), {force: true})
}

async function responseData<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({})) as {
    code?: number
    msg?: string
    data?: T
  }
  if (!response.ok || payload.code !== 0 || payload.data === undefined) {
    throw new AgenticsApiError(payload.msg || `HTTP ${response.status}`, response.status)
  }
  return payload.data
}

export async function exchangeAuthorizationCode(
  region: ServiceRegion,
  code: string,
  verifier: string,
): Promise<string> {
  const response = await fetch(`${region.apiOrigin}/v1/session/oauth2/token`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      client_id: OAUTH_CLIENT_ID,
      code,
      code_verifier: verifier,
    }),
    redirect: 'error',
    signal: AbortSignal.timeout(15_000),
  })
  const data = await responseData<{access_token?: string}>(response)
  if (!data.access_token) throw new Error('登录服务未返回 access token')
  return data.access_token
}

export async function fetchUserAccount(region: ServiceRegion, token: string): Promise<UserAccount> {
  const response = await fetch(`${region.apiOrigin}/v1/user`, {
    headers: {Authorization: `Bearer ${token}`},
    redirect: 'error',
    signal: AbortSignal.timeout(15_000),
  })
  const data = await responseData<{user?: {email?: string; balance?: number} | null}>(response)
  const email = data.user?.email
  const balance = data.user?.balance
  if (!email || typeof balance !== 'number') throw new Error('登录服务返回的用户信息无效')
  return {email, balance}
}
