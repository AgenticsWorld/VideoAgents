import {createHash} from 'node:crypto'
import {createWriteStream, existsSync, mkdirSync, renameSync, rmSync} from 'node:fs'
import path from 'node:path'
import {Readable, Transform} from 'node:stream'
import {pipeline} from 'node:stream/promises'
import extract from 'extract-zip'
import {
  activatePythonRuntime, loadPythonRuntime, PythonRuntime, runtimeStore,
} from './runtime'

const DEFAULT_INDEX_URL = 'https://s3.agentics.world/packages/video-agents.json'
const DEFAULT_PACKAGE_PREFIX = 'https://s3.agentics.world/packages/python/'
const SAFE_VERSION = /^[A-Za-z0-9._-]+$/

export interface RuntimeArtifact {
  version: string
  url: string
  sha256: string
  size: number
  pythonVersion?: string
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

function indexUrl(): string {
  return process.env.VIDEOAGENTS_RUNTIME_INDEX_URL || DEFAULT_INDEX_URL
}

function validateArtifact(value: unknown, sourceIndex: string): RuntimeArtifact {
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
  } else if (!url.href.startsWith(DEFAULT_PACKAGE_PREFIX)) {
    throw new Error('运行时制品 URL 不属于受信任的 S3 路径')
  }
  return artifact as RuntimeArtifact
}

export async function fetchLatestRuntimeArtifact(): Promise<RuntimeArtifact> {
  const source = indexUrl()
  const response = await fetch(source, {redirect: 'error', cache: 'no-store'})
  if (!response.ok) throw new Error(`运行时索引请求失败：HTTP ${response.status}`)
  const text = await response.text()
  if (text.length > 1024 * 1024) throw new Error('运行时索引文件过大')
  const index = JSON.parse(text) as RuntimeIndex
  if (index.schema !== 1 || !index.python) throw new Error('运行时索引格式无效')
  const platform = process.platform === 'darwin' ? 'mac' : process.platform === 'win32' ? 'win' : undefined
  if (!platform) throw new Error(`暂不支持的平台：${process.platform}`)
  return validateArtifact(index.python[platform]?.[process.arch], source)
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
    let uncompressedSize = 0
    await extract(archive, {
      dir: staging,
      onEntry: entry => {
        uncompressedSize += entry.uncompressedSize
        if (uncompressedSize > 2 * 1024 * 1024 * 1024) throw new Error('Python 环境包解压后体积异常')
      },
    })
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
