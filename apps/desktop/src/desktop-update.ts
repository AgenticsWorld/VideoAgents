import {createHash} from 'node:crypto'
import {
  createWriteStream, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync,
} from 'node:fs'
import path from 'node:path'
import {spawn} from 'node:child_process'
import {Readable, Transform} from 'node:stream'
import {pipeline} from 'node:stream/promises'
import extract from 'extract-zip'
import {RuntimeProgress} from './runtime-download'

const DEFAULT_INDEX_URL = 'https://s3.agentics.world/packages/video-agents.json'
const DEFAULT_BASE = 'https://s3.agentics.world/packages/'
const SAFE_VERSION = /^[A-Za-z0-9._-]+$/

export interface BuildInfo {
  schema: 1
  channel: 'local' | 'dev' | 'release'
  version: string
  buildHash: string
}

export interface DesktopArtifact {
  version: string
  buildHash: string
  url: string
  sha256: string
  size: number
}

interface DesktopIndex {
  schema: 1
  desktop?: {mac?: DesktopArtifact, win?: DesktopArtifact}
}

export function readBuildInfo(resourcesPath: string, packaged: boolean): BuildInfo {
  if (!packaged) return {schema: 1, channel: 'local', version: '1.0.2', buildHash: 'development'}
  try {
    const value = JSON.parse(readFileSync(path.join(resourcesPath, 'build-info.json'), 'utf8')) as BuildInfo
    if (value.schema === 1 && ['local', 'dev', 'release'].includes(value.channel)
        && typeof value.version === 'string' && typeof value.buildHash === 'string') return value
  } catch (error) {
    console.warn(`[desktop-updater] build-info.json 不可用：${String(error)}`)
  }
  return {schema: 1, channel: 'local', version: '1.0.2', buildHash: 'unknown'}
}

function validateArtifact(value: unknown, sourceIndex: string): DesktopArtifact {
  if (!value || typeof value !== 'object') throw new Error('桌面更新索引缺少当前平台制品')
  const artifact = value as Partial<DesktopArtifact>
  if (artifact.version !== '1.0.2' || typeof artifact.buildHash !== 'string'
      || !SAFE_VERSION.test(artifact.buildHash)
      || typeof artifact.url !== 'string' || !/^[a-f0-9]{64}$/i.test(artifact.sha256 || '')
      || typeof artifact.size !== 'number' || artifact.size <= 0 || artifact.size > 2 * 1024 * 1024 * 1024) {
    throw new Error('桌面更新索引格式无效')
  }
  const url = new URL(artifact.url)
  const configured = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
  if (configured) {
    if (url.origin !== new URL(sourceIndex).origin) throw new Error('桌面更新包与索引来源不一致')
  } else {
    const expected = process.platform === 'darwin'
      ? `${DEFAULT_BASE}video-agents-mac.zip` : `${DEFAULT_BASE}video-agents-win.zip`
    if (url.href !== expected) throw new Error('桌面更新包 URL 不属于受信任的 S3 路径')
  }
  return artifact as DesktopArtifact
}

export async function fetchDevDesktopUpdate(build: BuildInfo): Promise<DesktopArtifact | undefined> {
  if (build.channel !== 'dev') return undefined
  const source = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL || DEFAULT_INDEX_URL
  const response = await fetch(source, {redirect: 'error', cache: 'no-store'})
  if (!response.ok) throw new Error(`桌面更新索引请求失败：HTTP ${response.status}`)
  const text = await response.text()
  if (text.length > 1024 * 1024) throw new Error('桌面更新索引文件过大')
  const index = JSON.parse(text) as DesktopIndex
  const platform = process.platform === 'darwin' ? 'mac' : process.platform === 'win32' ? 'win' : undefined
  if (index.schema !== 1 || !platform) throw new Error('桌面更新索引或平台无效')
  const artifact = validateArtifact(index.desktop?.[platform], source)
  return artifact.buildHash === build.buildHash ? undefined : artifact
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

export async function downloadAndApplyDesktopUpdate(
  userData: string,
  currentAppPath: string,
  artifact: DesktopArtifact,
  onProgress: (progress: RuntimeProgress) => void,
): Promise<void> {
  const root = path.join(userData, 'app-updates')
  const archive = path.join(root, `${artifact.buildHash}.zip.part`)
  const staging = path.join(root, `staging-${artifact.buildHash}`)
  mkdirSync(root, {recursive: true})
  rmSync(archive, {force: true})
  rmSync(staging, {recursive: true, force: true})
  await download(artifact, archive, onProgress)
  onProgress({phase: 'extracting', message: '正在准备应用更新…'})
  mkdirSync(staging, {recursive: true})
  let uncompressedSize = 0
  await extract(archive, {
    dir: staging,
    onEntry: entry => {
      uncompressedSize += entry.uncompressedSize
      if (uncompressedSize > 3 * 1024 * 1024 * 1024) throw new Error('桌面更新包解压后体积异常')
    },
  })
  rmSync(archive, {force: true})

  if (process.platform === 'darwin') {
    if (currentAppPath.includes('/AppTranslocation/')) {
      throw new Error('请先把 VideoAgents.app 移动到“应用程序”目录，再执行自动更新。')
    }
    const replacement = findFile(staging, name => name.endsWith('.app'))
    if (!replacement) throw new Error('macOS 更新包中没有 .app')
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
    const installer = findFile(staging, name => name.toLowerCase().endsWith('.exe'))
    if (!installer || !existsSync(installer)) throw new Error('Windows 更新包中没有安装程序')
    const helper = path.join(root, 'apply-windows-update.ps1')
    writeFileSync(helper, `param([int]$PidToWait, [string]$Installer)\ntry { Wait-Process -Id $PidToWait -Timeout 120 -ErrorAction SilentlyContinue } catch {}\nStart-Process -FilePath $Installer -ArgumentList '/S'\n`)
    const helperProcess = spawn('powershell.exe', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
      '-File', helper, '-PidToWait', String(process.pid), '-Installer', installer,
    ], {detached: true, stdio: 'ignore', windowsHide: true})
    helperProcess.unref()
    return
  }
  throw new Error(`不支持桌面自更新的平台：${process.platform}`)
}
