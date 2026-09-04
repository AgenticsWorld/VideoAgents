import {createHash, randomBytes} from 'node:crypto'
import {existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync} from 'node:fs'
import path from 'node:path'
import {safeStorage} from 'electron'
import type {BuildInfo} from './desktop-update'

export const OAUTH_CLIENT_ID = 'videoagents-desktop'
export type ServiceEnvironment = 'dev' | 'production'
const TRUSTED_API_ORIGINS = new Set([
  'https://api.agentics.world',
  'https://devapi.agentics.world',
  'https://api.shumati.cn',
])

export interface ServiceRegion {
  environment: ServiceEnvironment
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
  const requestedEnvironment = process.env.VIDEOAGENTS_SERVICE_ENV
  if (requestedEnvironment && requestedEnvironment !== 'dev' && requestedEnvironment !== 'production') {
    throw new Error(`Unknown Agentics service environment: ${requestedEnvironment}`)
  }
  const configuredApiOrigin = process.env.VIDEOAGENTS_SERVICE_API_ORIGIN?.replace(/\/$/, '')
  if (configuredApiOrigin && !TRUSTED_API_ORIGINS.has(configuredApiOrigin)) {
    throw new Error(`Untrusted Agentics service origin: ${configuredApiOrigin}`)
  }
  if (distribution === 'oss') {
    if (requestedEnvironment === 'dev') {
      throw new Error('The OSS distribution has no configured Agentics development environment')
    }
    if (configuredApiOrigin && configuredApiOrigin !== 'https://api.shumati.cn') {
      throw new Error('Agentics service origin does not match the oss distribution')
    }
    return {
      environment: 'production',
      distribution,
      ssoOrigin: 'https://sso.shumati.cn',
      apiOrigin: configuredApiOrigin || 'https://api.shumati.cn',
      openrouterWrapperUrl: 'https://wrapper.shumati.cn/wrapper/openrouter',
      redirectUri: 'videoagents://shumati.cn',
    }
  }
  if (configuredApiOrigin === 'https://api.shumati.cn') {
    throw new Error('Agentics service origin does not match the s3 distribution')
  }
  const environment: ServiceEnvironment = requestedEnvironment === 'dev' ? 'dev'
    : requestedEnvironment === 'production' ? 'production'
    : (configuredApiOrigin === 'https://devapi.agentics.world'
      || (!configuredApiOrigin && (build.channel === 'local' || build.channel === 'dev'))
      ? 'dev' : 'production')
  const endpoints = environment === 'dev' ? {
    apiOrigin: 'https://devapi.agentics.world',
    ssoOrigin: 'https://devsso.agentics.world',
  } : {
    apiOrigin: 'https://api.agentics.world',
    ssoOrigin: 'https://sso.agentics.world',
  }
  if (configuredApiOrigin && configuredApiOrigin !== endpoints.apiOrigin) {
    throw new Error(`Agentics API origin does not match the ${environment} environment`)
  }
  return {
    environment,
    distribution,
    ...endpoints,
    openrouterWrapperUrl: `${endpoints.apiOrigin}/wrapper/openrouter`,
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

function authPath(userData: string, environment: ServiceEnvironment): string {
  return path.join(userData, environment === 'dev' ? 'auth-dev.json' : 'auth.json')
}

export function loadStoredAuth(
  userData: string, environment: ServiceEnvironment = 'production',
): StoredAuth | undefined {
  const target = authPath(userData, environment)
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

export function saveStoredAuth(
  userData: string, auth: StoredAuth, environment: ServiceEnvironment = 'production',
): void {
  const target = authPath(userData, environment)
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

export function clearStoredAuth(
  userData: string, environment: ServiceEnvironment = 'production',
): void {
  rmSync(authPath(userData, environment), {force: true})
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
