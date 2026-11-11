import {createHash} from 'node:crypto'
import {
  appendFileSync, createWriteStream, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync,
  writeFileSync,
} from 'node:fs'
import path from 'node:path'
import {ChildProcess, spawn} from 'node:child_process'
import {Readable, Transform} from 'node:stream'
import {pipeline} from 'node:stream/promises'
import extract from 'extract-zip'
import {RuntimeProgress} from './runtime-download'

const DEFAULT_INDEX_URL = 'https://s3.agentics.world/packages/video-agents/metadata.json'
const DEFAULT_BASE = 'https://s3.agentics.world/packages/video-agents/'
const SAFE_VERSION = /^[A-Za-z0-9._-]+$/

export interface BuildInfo {
  schema: 1
  channel: 'local' | 'dev' | 'release'
  version: string
  buildHash: string
  /** 构建时固定的发布源；安装后更新始终沿用此来源。 */
  distribution?: 's3' | 'oss'
  updateIndexUrl?: string
  packageBaseUrl?: string
  /** 构建期 runtime-requirements.lock 的 sha256;与已装运行时清单比对,不一致时启动自动更新运行时 */
  requirementsSha256?: string
}

export interface DesktopArtifact {
  version: string
  buildHash: string
  url: string
  sha256: string
  size: number
}

export interface DesktopUpdate {
  artifact: DesktopArtifact
  required: boolean
  minimumVersion?: string
}

interface CachedRequiredUpdate {
  schema: 1
  minimumVersion: string
  artifact: DesktopArtifact
}

interface DesktopIndex {
  schema: 1
  desktop?: {
    minimumVersion?: string
    mac?: DesktopArtifact
    win?: DesktopArtifact
  }
}

export function readBuildInfo(resourcesPath: string, packaged: boolean): BuildInfo {
  if (!packaged) return {schema: 1, channel: 'local', version: '1.0.2', buildHash: 'development'}
  try {
    const value = JSON.parse(readFileSync(path.join(resourcesPath, 'build-info.json'), 'utf8')) as BuildInfo
    if (value.schema === 1 && ['local', 'dev', 'release'].includes(value.channel)
        && typeof value.version === 'string' && typeof value.buildHash === 'string') {
      if (value.requirementsSha256 !== undefined && !/^[a-f0-9]{64}$/i.test(value.requirementsSha256)) {
        delete value.requirementsSha256
      }
      if (value.distribution !== 's3' && value.distribution !== 'oss') delete value.distribution
      if (typeof value.updateIndexUrl !== 'string' || !validHttpUrl(value.updateIndexUrl)) {
        delete value.updateIndexUrl
      }
      if (typeof value.packageBaseUrl !== 'string' || !validHttpUrl(value.packageBaseUrl)) {
        delete value.packageBaseUrl
      } else if (!value.packageBaseUrl.endsWith('/')) {
        value.packageBaseUrl += '/'
      }
      return value
    }
  } catch (error) {
    console.warn(`[desktop-updater] build-info.json 不可用：${String(error)}`)
  }
  return {schema: 1, channel: 'local', version: '1.0.2', buildHash: 'unknown'}
}

function validHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

function updateIndexUrl(build?: BuildInfo): string {
  return process.env.VIDEOAGENTS_RUNTIME_INDEX_URL || build?.updateIndexUrl || DEFAULT_INDEX_URL
}

function packageBaseUrl(build?: BuildInfo): string {
  return build?.packageBaseUrl || DEFAULT_BASE
}

function validateArtifact(value: unknown, sourceIndex: string, build?: BuildInfo): DesktopArtifact {
  if (!value || typeof value !== 'object') throw new Error('桌面更新索引缺少当前平台制品')
  const artifact = value as Partial<DesktopArtifact>
  if (typeof artifact.version !== 'string' || !SAFE_VERSION.test(artifact.version)
      || typeof artifact.buildHash !== 'string' || !SAFE_VERSION.test(artifact.buildHash)
      || typeof artifact.url !== 'string' || !/^[a-f0-9]{64}$/i.test(artifact.sha256 || '')
      || typeof artifact.size !== 'number' || artifact.size <= 0 || artifact.size > 2 * 1024 * 1024 * 1024) {
    throw new Error('桌面更新索引格式无效')
  }
  const url = new URL(artifact.url)
  const configured = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
  if (configured) {
    if (url.origin !== new URL(sourceIndex).origin) throw new Error('桌面更新包与索引来源不一致')
  } else {
    const platform = process.platform === 'darwin' ? 'mac' : 'win'
    const expected = `${packageBaseUrl(build)}${platform}/VideoAgents-${artifact.version}.zip`
    if (url.href !== expected) throw new Error('桌面更新包 URL 不属于当前发布源的受信任路径')
  }
  return artifact as DesktopArtifact
}

function versionParts(version: string): number[] | undefined {
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(version)
  return match ? match.slice(1).map(Number) : undefined
}

function isNewerVersion(candidate: string, current: string): boolean {
  const candidateParts = versionParts(candidate)
  const currentParts = versionParts(current)
  if (!candidateParts || !currentParts) return false
  for (let index = 0; index < candidateParts.length; index += 1) {
    if (candidateParts[index] !== currentParts[index]) return candidateParts[index] > currentParts[index]
  }
  return false
}

export async function fetchDesktopUpdate(build: BuildInfo): Promise<DesktopUpdate | undefined> {
  if (!['dev', 'release'].includes(build.channel)) return undefined
  const source = updateIndexUrl(build)
  const response = await fetch(source, {
    redirect: 'error',
    cache: 'no-store',
    signal: AbortSignal.timeout(10_000),
  })
  if (!response.ok) throw new Error(`桌面更新索引请求失败：HTTP ${response.status}`)
  const text = await response.text()
  if (text.length > 1024 * 1024) throw new Error('桌面更新索引文件过大')
  const index = JSON.parse(text) as DesktopIndex
  const platform = process.platform === 'darwin' ? 'mac' : process.platform === 'win32' ? 'win' : undefined
  if (index.schema !== 1 || !platform) throw new Error('桌面更新索引或平台无效')
  const artifact = validateArtifact(index.desktop?.[platform], source, build)
  const minimumVersion = index.desktop?.minimumVersion
  if (minimumVersion !== undefined && !versionParts(minimumVersion)) {
    throw new Error('桌面更新索引的最低可用版本无效')
  }
  if (minimumVersion && isNewerVersion(minimumVersion, artifact.version)) {
    throw new Error('桌面更新索引的最低可用版本高于最新安装包版本')
  }
  if (build.channel === 'dev') {
    return artifact.buildHash === build.buildHash
      ? undefined
      : {artifact, required: false}
  }
  if (!isNewerVersion(artifact.version, build.version)) return undefined
  return {
    artifact,
    required: Boolean(minimumVersion && isNewerVersion(minimumVersion, build.version)),
    minimumVersion,
  }
}

function requiredUpdateCachePath(userData: string): string {
  return path.join(userData, 'app-updates', 'required-update.json')
}

export function readCachedRequiredDesktopUpdate(
  userData: string,
  build: BuildInfo,
): DesktopUpdate | undefined {
  if (build.channel !== 'release') return undefined
  const cache = requiredUpdateCachePath(userData)
  if (!existsSync(cache)) return undefined
  try {
    const value = JSON.parse(readFileSync(cache, 'utf8')) as Partial<CachedRequiredUpdate>
    if (value.schema !== 1 || !value.minimumVersion || !versionParts(value.minimumVersion)) {
      throw new Error('强制更新缓存格式无效')
    }
    const source = updateIndexUrl(build)
    const artifact = validateArtifact(value.artifact, source, build)
    if (isNewerVersion(value.minimumVersion, artifact.version)) {
      throw new Error('强制更新缓存的最低版本高于安装包版本')
    }
    if (!isNewerVersion(value.minimumVersion, build.version)
        || !isNewerVersion(artifact.version, build.version)) return undefined
    return {artifact, required: true, minimumVersion: value.minimumVersion}
  } catch (error) {
    console.warn(`[desktop-updater] 强制更新缓存不可用：${String(error)}`)
    return undefined
  }
}

export function cacheRequiredDesktopUpdate(userData: string, update: DesktopUpdate): void {
  if (!update.required || !update.minimumVersion) return
  const destination = requiredUpdateCachePath(userData)
  const temporary = `${destination}.${process.pid}.tmp`
  mkdirSync(path.dirname(destination), {recursive: true})
  const value: CachedRequiredUpdate = {
    schema: 1,
    minimumVersion: update.minimumVersion,
    artifact: update.artifact,
  }
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {encoding: 'utf8', mode: 0o600})
  renameSync(temporary, destination)
}

export function clearCachedRequiredDesktopUpdate(userData: string): void {
  rmSync(requiredUpdateCachePath(userData), {force: true})
}

async function download(
  artifact: DesktopArtifact,
  destination: string,
  onProgress: (progress: RuntimeProgress) => void,
): Promise<void> {
  const response = await fetch(artifact.url, {redirect: 'error', cache: 'no-store'})
  if (!response.ok || !response.body) throw new Error(`桌面更新包下载失败：HTTP ${response.status}`)
  let received = 0
  const total = Number(response.headers.get('content-length') || artifact.size)
  const hash = createHash('sha256')
  const meter = new Transform({
    transform(chunk: Buffer, _encoding, callback) {
      received += chunk.length
      if (received > artifact.size + 1024) return callback(new Error('桌面更新包大小与索引不一致'))
      hash.update(chunk)
      onProgress({phase: 'downloading', message: '正在下载 VideoAgents 更新…', received, total})
      callback(null, chunk)
    },
  })
  await pipeline(Readable.fromWeb(response.body as never), meter, createWriteStream(destination, {mode: 0o600}))
  if (received !== artifact.size) throw new Error(`桌面更新包大小校验失败：${received}/${artifact.size}`)
  if (hash.digest('hex').toLowerCase() !== artifact.sha256.toLowerCase()) {
    throw new Error('桌面更新包 SHA-256 校验失败')
  }
}

function findFile(root: string, predicate: (name: string) => boolean): string | undefined {
  for (const entry of readdirSync(root, {withFileTypes: true})) {
    const target = path.join(root, entry.name)
    if (entry.isDirectory()) {
      if (predicate(entry.name)) return target
      const nested = findFile(target, predicate)
      if (nested) return nested
    } else if (predicate(entry.name)) return target
  }
  return undefined
}

async function extractDesktopArchive(archive: string, staging: string): Promise<void> {
  // Electron 的 extract-zip 在部分 macOS App 包（Framework 内含符号链接）上会停在
  // 解压阶段而不抛错。ditto 是系统原生的 App/DMG 解包工具，可正确保留链接和权限。
  if (process.platform === 'darwin') {
    await new Promise<void>((resolve, reject) => {
      const child = spawn('/usr/bin/ditto', ['-x', '-k', archive, staging], {stdio: 'ignore'})
      child.once('error', reject)
      child.once('close', code => {
        if (code === 0) resolve()
        else reject(new Error(`macOS 更新包解压失败：ditto 退出码 ${code ?? 'unknown'}`))
      })
    })
    return
  }

  let uncompressedSize = 0
  await extract(archive, {
    dir: staging,
    onEntry: entry => {
      uncompressedSize += entry.uncompressedSize
      if (uncompressedSize > 3 * 1024 * 1024 * 1024) throw new Error('桌面更新包解压后体积异常')
    },
  })
}

/** 已下载、校验并解压好的更新包,可立即安装,也可留到下次启动再装。 */
export interface PreparedDesktopUpdate {
  version: string
  buildHash: string
  /** 解压目录(app-updates/staging-<buildHash>) */
  staging: string
  /** macOS:解压出的新 .app;Windows:VideoAgents-Setup.exe */
  payload: string
}

interface PendingDesktopUpdateRecord extends PreparedDesktopUpdate {
  schema: 1
  platform: NodeJS.Platform
  preparedAt: string
}

function updatesRoot(userData: string): string {
  return path.join(userData, 'app-updates')
}

function assertUpdatableAppPath(currentAppPath: string): void {
  if (process.platform !== 'darwin') return
  if (!currentAppPath.endsWith('.app')
      || !existsSync(path.join(currentAppPath, 'Contents', 'Info.plist'))) {
    throw new Error(`macOS 应用路径无效，已取消更新：${currentAppPath}`)
  }
  if (currentAppPath.includes('/AppTranslocation/')) {
    throw new Error('请先把 VideoAgents.app 移动到“应用程序”目录，再执行自动更新。')
  }
}

function isUsablePayload(payload: string): boolean {
  if (process.platform === 'darwin') {
    return payload.endsWith('.app') && existsSync(path.join(payload, 'Contents', 'Info.plist'))
  }
  return existsSync(payload) && lstatSync(payload).isFile()
}

/** 下载 → 校验 SHA-256 → 解压到 staging,找到要安装的 .app / 安装程序;不动当前应用。 */
export async function prepareDesktopUpdate(
  userData: string,
  currentAppPath: string,
  artifact: DesktopArtifact,
  onProgress: (progress: RuntimeProgress) => void,
): Promise<PreparedDesktopUpdate> {
  if (process.platform !== 'darwin' && process.platform !== 'win32') {
    throw new Error(`不支持桌面自更新的平台：${process.platform}`)
  }
  assertUpdatableAppPath(currentAppPath)
  const root = updatesRoot(userData)
  const archive = path.join(root, `${artifact.buildHash}.zip.part`)
  const staging = path.join(root, `staging-${artifact.buildHash}`)
  mkdirSync(root, {recursive: true})
  rmSync(archive, {force: true})
  rmSync(staging, {recursive: true, force: true})
  await download(artifact, archive, onProgress)
  onProgress({phase: 'extracting', message: '正在准备应用更新…'})
  mkdirSync(staging, {recursive: true})
  await extractDesktopArchive(archive, staging)
  rmSync(archive, {force: true})

  let payload: string | undefined
  if (process.platform === 'darwin') {
    payload = findFile(staging, name => name.endsWith('.app'))
    if (!payload) throw new Error('macOS 更新包中没有 .app')
  } else {
    payload = path.join(staging, 'VideoAgents-Setup.exe')
    if (!existsSync(payload) || !lstatSync(payload).isFile()) {
      throw new Error('Windows 更新包中没有 VideoAgents-Setup.exe')
    }
  }
  return {version: artifact.version, buildHash: artifact.buildHash, staging, payload}
}

/**
 * 启动外部安装进程:macOS 由脚本等当前进程退出后替换 .app 并重新打开;Windows 启动 NSIS 安装程序。
 * 调用方随后须退出应用。
 */
export function launchPreparedDesktopUpdate(
  userData: string,
  currentAppPath: string,
  prepared: PreparedDesktopUpdate,
  onProgress: (progress: RuntimeProgress) => void,
): void {
  assertUpdatableAppPath(currentAppPath)
  const root = updatesRoot(userData)
  mkdirSync(root, {recursive: true})

  if (process.platform === 'darwin') {
    const replacement = prepared.payload
    const helper = path.join(root, 'apply-macos-update.sh')
    const log = path.join(root, 'update.log')
    writeFileSync(helper, `#!/bin/sh\nset -eu\nexec >>"$4" 2>&1\npid="$1"\ncurrent="$2"\nreplacement="$3"\ncount=0\nwhile kill -0 "$pid" 2>/dev/null && [ "$count" -lt 120 ]; do sleep 1; count=$((count+1)); done\nbackup="\${current}.videoagents-previous"\nrm -rf "$backup"\nmv "$current" "$backup"\nif mv "$replacement" "$current"; then\n  open "$current"\n  rm -rf "$backup"\nelse\n  mv "$backup" "$current"\n  open "$current"\n  exit 1\nfi\n`, {mode: 0o700})
    const helperProcess = spawn('/bin/sh', [helper, String(process.pid), currentAppPath, replacement, log], {
      detached: true, stdio: 'ignore',
    })
    helperProcess.unref()
    return
  }

  if (process.platform === 'win32') {
    const installer = prepared.payload
    const installDir = path.resolve(currentAppPath)
    const log = path.join(root, 'update-windows.log')
    const args = ['--updated', `/D=${installDir}`]
    appendFileSync(log, `[${new Date().toISOString()}] Starting NSIS installer: ${installer}\n`)
    appendFileSync(log, `[${new Date().toISOString()}] Install directory: ${installDir}\n`)
    const installerProcess = spawn(installer, args, {
      detached: true,
      stdio: 'ignore',
      windowsHide: false,
    })
    installerProcess.once('error', error => {
      try { appendFileSync(log, `[${new Date().toISOString()}] Installer spawn failed: ${String(error)}\n`) } catch {}
    })
    appendFileSync(log, `[${new Date().toISOString()}] Installer started, pid=${installerProcess.pid ?? 'unknown'}\n`)
    installerProcess.unref()
    onProgress({phase: 'activating', message: '安装程序已启动，应用即将关闭并自动重启…'})
    return
  }
  throw new Error(`不支持桌面自更新的平台：${process.platform}`)
}

export async function downloadAndApplyDesktopUpdate(
  userData: string,
  currentAppPath: string,
  artifact: DesktopArtifact,
  onProgress: (progress: RuntimeProgress) => void,
): Promise<void> {
  const prepared = await prepareDesktopUpdate(userData, currentAppPath, artifact, onProgress)
  launchPreparedDesktopUpdate(userData, currentAppPath, prepared, onProgress)
}

function pendingUpdatePath(userData: string): string {
  return path.join(updatesRoot(userData), 'pending-update.json')
}

function isInsideUpdatesRoot(userData: string, target: string): boolean {
  const relative = path.relative(path.resolve(updatesRoot(userData)), path.resolve(target))
  return Boolean(relative) && !relative.startsWith('..') && !path.isAbsolute(relative)
}

/** 「下次启动时安装」:后台下载解压完成后记下待装更新,下次启动时 takePendingDesktopUpdate 取出安装。 */
export function savePendingDesktopUpdate(userData: string, prepared: PreparedDesktopUpdate): void {
  const destination = pendingUpdatePath(userData)
  const temporary = `${destination}.${process.pid}.tmp`
  mkdirSync(path.dirname(destination), {recursive: true})
  const value: PendingDesktopUpdateRecord = {
    schema: 1,
    platform: process.platform,
    preparedAt: new Date().toISOString(),
    ...prepared,
  }
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {encoding: 'utf8', mode: 0o600})
  renameSync(temporary, destination)
}

/**
 * 取出待装更新并立即删除记录(安装失败也不会每次启动反复重试,改由启动时的更新检查重新询问)。
 * 记录无效、平台不符、更新包缺失或版本已不比当前新时返回 undefined,并清掉对应的解压目录。
 */
export function takePendingDesktopUpdate(
  userData: string,
  build: BuildInfo,
): PreparedDesktopUpdate | undefined {
  const marker = pendingUpdatePath(userData)
  if (!existsSync(marker)) return undefined
  let value: Partial<PendingDesktopUpdateRecord> | undefined
  try {
    value = JSON.parse(readFileSync(marker, 'utf8')) as Partial<PendingDesktopUpdateRecord>
  } catch (error) {
    console.warn(`[desktop-updater] 待装更新记录不可用：${String(error)}`)
  }
  rmSync(marker, {force: true})
  if (!value) return undefined
  const discard = (reason: string): undefined => {
    console.warn(`[desktop-updater] 放弃待装更新：${reason}`)
    if (typeof value?.staging === 'string' && isInsideUpdatesRoot(userData, value.staging)
        && path.basename(value.staging).startsWith('staging-')) {
      try { rmSync(value.staging, {recursive: true, force: true}) } catch {}
    }
    return undefined
  }
  if (value.schema !== 1 || typeof value.version !== 'string' || !SAFE_VERSION.test(value.version)
      || typeof value.buildHash !== 'string' || !SAFE_VERSION.test(value.buildHash)
      || typeof value.staging !== 'string' || typeof value.payload !== 'string') {
    return discard('记录格式无效')
  }
  if (value.platform !== process.platform) return discard(`平台不符(${String(value.platform)})`)
  if (!isInsideUpdatesRoot(userData, value.staging) || !isInsideUpdatesRoot(userData, value.payload)) {
    return discard('更新包不在更新目录内')
  }
  const applicable = build.channel === 'release'
    ? isNewerVersion(value.version, build.version)
    : build.channel === 'dev' && value.buildHash !== build.buildHash
  if (!applicable) return discard(`当前已是 ${build.version}`)
  if (!isUsablePayload(value.payload)) return discard('更新包文件缺失')
  return {version: value.version, buildHash: value.buildHash, staging: value.staging, payload: value.payload}
}
