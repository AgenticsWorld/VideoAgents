import {createHash} from 'node:crypto'
import {spawn} from 'node:child_process'
import {createWriteStream, existsSync, mkdirSync, readFileSync, renameSync, rmSync} from 'node:fs'
import path from 'node:path'
import {Readable, Transform} from 'node:stream'
import {pipeline} from 'node:stream/promises'
import extract from 'extract-zip'
import {
  activatePythonRuntime, loadPythonRuntime, PythonRuntime, runtimeStore,
} from './runtime'

const DEFAULT_INDEX_URL = 'https://s3.agentics.world/packages/video-agents/metadata.json'
const DEFAULT_PACKAGE_PREFIX = 'https://s3.agentics.world/packages/video-agents/python/'
const SAFE_VERSION = /^[A-Za-z0-9._-]+$/
const MACOS_EXTRACT_TIMEOUT_MS = 15 * 60 * 1000

export interface RuntimeArtifact {
  version: string
  url: string
  sha256: string
  size: number
  pythonVersion?: string
}

interface RuntimeBuildInfo {
  updateIndexUrl?: string
  packageBaseUrl?: string
}

interface RuntimeIndex {
  schema: 1
  python: {
    mac?: Record<string, RuntimeArtifact>
    win?: Record<string, RuntimeArtifact>
  }
}

export interface RuntimeProgress {
  phase: 'checking' | 'downloading' | 'extracting' | 'activating'
  message: string
  received?: number
  total?: number
}

export interface RuntimeInstallResult {
  runtime: PythonRuntime
  artifact: RuntimeArtifact
  downloaded: boolean
}

function packagedBuildInfo(): RuntimeBuildInfo | undefined {
  try {
    const value = JSON.parse(readFileSync(path.join(process.resourcesPath, 'build-info.json'), 'utf8')) as RuntimeBuildInfo
    const validUrl = (url: unknown): url is string => {
      if (typeof url !== 'string') return false
      try {
        const parsed = new URL(url)
        return parsed.protocol === 'https:' || parsed.protocol === 'http:'
      } catch {
        return false
      }
    }
    if (!validUrl(value.updateIndexUrl)) delete value.updateIndexUrl
    if (!validUrl(value.packageBaseUrl)) delete value.packageBaseUrl
    else if (!value.packageBaseUrl.endsWith('/')) value.packageBaseUrl += '/'
    return value
  } catch {
    return undefined
  }
}

function indexUrl(build = packagedBuildInfo()): string {
  return process.env.VIDEOAGENTS_RUNTIME_INDEX_URL || build?.updateIndexUrl || DEFAULT_INDEX_URL
}

function validateArtifact(value: unknown, sourceIndex: string, build?: RuntimeBuildInfo): RuntimeArtifact {
  if (!value || typeof value !== 'object') throw new Error('运行时索引缺少当前平台制品')
  const artifact = value as Partial<RuntimeArtifact>
  if (typeof artifact.version !== 'string' || !SAFE_VERSION.test(artifact.version)
      || typeof artifact.url !== 'string' || !/^[a-f0-9]{64}$/i.test(artifact.sha256 || '')
      || typeof artifact.size !== 'number' || artifact.size <= 0 || artifact.size > 2 * 1024 * 1024 * 1024) {
    throw new Error('运行时索引中的制品信息无效')
  }
  const url = new URL(artifact.url)
  const configuredIndex = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
  if (configuredIndex) {
    if (url.origin !== new URL(sourceIndex).origin) throw new Error('运行时制品与索引来源不一致')
  } else if (!url.href.startsWith(`${build?.packageBaseUrl || DEFAULT_PACKAGE_PREFIX.replace(/python\/$/, '')}python/`)) {
    throw new Error('运行时制品 URL 不属于当前发布源的受信任路径')
  }
  return artifact as RuntimeArtifact
}

export async function fetchLatestRuntimeArtifact(): Promise<RuntimeArtifact> {
  const build = packagedBuildInfo()
  const source = indexUrl(build)
  const response = await fetch(source, {redirect: 'error', cache: 'no-store'})
  if (!response.ok) throw new Error(`运行时索引请求失败：HTTP ${response.status}`)
  const text = await response.text()
  if (text.length > 1024 * 1024) throw new Error('运行时索引文件过大')
  const index = JSON.parse(text) as RuntimeIndex
  if (index.schema !== 1 || !index.python) throw new Error('运行时索引格式无效')
  const platform = process.platform === 'darwin' ? 'mac' : process.platform === 'win32' ? 'win' : undefined
  if (!platform) throw new Error(`暂不支持的平台：${process.platform}`)
  return validateArtifact(index.python[platform]?.[process.arch], source, build)
}

async function downloadArtifact(
  artifact: RuntimeArtifact,
  destination: string,
  onProgress: (progress: RuntimeProgress) => void,
): Promise<void> {
  const response = await fetch(artifact.url, {redirect: 'error', cache: 'no-store'})
  if (!response.ok || !response.body) throw new Error(`Python 环境包下载失败：HTTP ${response.status}`)
  const headerSize = Number(response.headers.get('content-length') || artifact.size)
  let received = 0
  const hash = createHash('sha256')
  const meter = new Transform({
    transform(chunk: Buffer, _encoding, callback) {
      received += chunk.length
      if (received > artifact.size + 1024) return callback(new Error('Python 环境包大小与索引不一致'))
      hash.update(chunk)
      onProgress({phase: 'downloading', message: '正在下载 Python 环境…', received, total: headerSize})
      callback(null, chunk)
    },
  })
  await pipeline(Readable.fromWeb(response.body as never), meter, createWriteStream(destination, {mode: 0o600}))
  if (received !== artifact.size) throw new Error(`Python 环境包大小校验失败：${received}/${artifact.size}`)
  if (hash.digest('hex').toLowerCase() !== artifact.sha256.toLowerCase()) {
    throw new Error('Python 环境包 SHA-256 校验失败')
  }
}

async function extractRuntimeArchive(archive: string, staging: string): Promise<void> {
  // extract-zip 在部分 macOS 运行时包（venv 内含符号链接）上会无限停在解压阶段。
  // 使用系统 ditto 保留链接与权限；设置上限以便网络盘/磁盘异常时能给用户明确错误。
  if (process.platform === 'darwin') {
    await new Promise<void>((resolve, reject) => {
      const child = spawn('/usr/bin/ditto', ['-x', '-k', archive, staging], {stdio: 'ignore'})
      let settled = false
      const finish = (error?: Error) => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        if (error) reject(error)
        else resolve()
      }
      const timeout = setTimeout(() => {
        child.kill('SIGKILL')
        finish(new Error('Python 环境解压超时（15 分钟）。请检查可用磁盘空间后重试。'))
      }, MACOS_EXTRACT_TIMEOUT_MS)
      child.once('error', error => finish(error))
      child.once('close', code => {
        if (code === 0) finish()
        else finish(new Error(`Python 环境解压失败：ditto 退出码 ${code ?? 'unknown'}`))
      })
    })
    return
  }

  let uncompressedSize = 0
  await extract(archive, {
    dir: staging,
    onEntry: entry => {
      uncompressedSize += entry.uncompressedSize
      if (uncompressedSize > 2 * 1024 * 1024 * 1024) throw new Error('Python 环境包解压后体积异常')
    },
  })
}

export async function installLatestPythonRuntime(
  userData: string,
  onProgress: (progress: RuntimeProgress) => void = () => undefined,
): Promise<RuntimeInstallResult> {
  onProgress({phase: 'checking', message: '正在检查 Python 环境版本…'})
  const artifact = await fetchLatestRuntimeArtifact()
  const store = runtimeStore(userData)
  const versions = path.join(store, 'versions')
  const target = path.join(versions, artifact.version)
  if (existsSync(target)) {
    try {
      const runtime = loadPythonRuntime(target)
      if (runtime.manifest?.version !== artifact.version) throw new Error('已有 Python 运行时版本清单不一致')
      activatePythonRuntime(userData, artifact.version)
      return {runtime, artifact, downloaded: false}
    } catch (error) {
      console.warn(`[runtime] 删除损坏的运行时 ${artifact.version}：${String(error)}`)
      rmSync(target, {recursive: true, force: true})
    }
  }

  mkdirSync(versions, {recursive: true})
  const downloads = path.join(store, 'downloads')
  mkdirSync(downloads, {recursive: true})
  const archive = path.join(downloads, `${artifact.version}.zip.part`)
  const staging = path.join(versions, `.installing-${artifact.version}-${process.pid}`)
  rmSync(archive, {force: true})
  rmSync(staging, {recursive: true, force: true})
  try {
    await downloadArtifact(artifact, archive, onProgress)
    onProgress({phase: 'extracting', message: '正在安装 Python 环境…'})
    mkdirSync(staging, {recursive: true})
    await extractRuntimeArchive(archive, staging)
    const runtime = loadPythonRuntime(staging)
    if (runtime.manifest?.version !== artifact.version) throw new Error('Python 环境包版本与索引不一致')
    onProgress({phase: 'activating', message: '正在启用 Python 环境…'})
    renameSync(staging, target)
    activatePythonRuntime(userData, artifact.version)
    return {runtime: loadPythonRuntime(target), artifact, downloaded: true}
  } finally {
    rmSync(archive, {force: true})
    rmSync(staging, {recursive: true, force: true})
  }
}
