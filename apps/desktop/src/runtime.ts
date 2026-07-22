import {
  existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync,
  statSync, writeFileSync,
} from 'node:fs'
import path from 'node:path'

export interface RuntimeManifest {
  schema: 1
  version: string
  pythonVersion: string
  platform: NodeJS.Platform
  arch: string
  executable: string
  requirementsSha256: string
}

export interface PythonRuntime {
  python: string
  source: 'environment' | 'development' | 'user'
  manifest?: RuntimeManifest
}

const SAFE_VERSION = /^[A-Za-z0-9._-]+$/

export function loadPythonRuntime(directory: string): PythonRuntime {
  const root = realpathSync(directory)
  const manifestPath = path.join(root, 'runtime-manifest.json')
  let manifest: RuntimeManifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as RuntimeManifest
  } catch (error) {
    throw new Error(`无法读取 Python 运行时清单 ${manifestPath}: ${String(error)}`)
  }
  if (manifest.schema !== 1 || typeof manifest.version !== 'string' || !SAFE_VERSION.test(manifest.version)
      || typeof manifest.executable !== 'string') {
    throw new Error(`Python 运行时清单无效：${manifestPath}`)
  }
  if (manifest.platform !== process.platform || manifest.arch !== process.arch) {
    throw new Error(`Python 运行时平台不匹配：需要 ${process.platform}/${process.arch}，实际 ${manifest.platform}/${manifest.arch}`)
  }
  if (path.isAbsolute(manifest.executable)) throw new Error('Python 运行时 executable 必须是相对路径')
  const unresolvedPython = path.resolve(root, manifest.executable)
  if (!existsSync(unresolvedPython)) throw new Error(`Python 运行时 executable 不存在：${manifest.executable}`)
  const python = realpathSync(unresolvedPython)
  const relative = path.relative(root, python)
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Python 运行时 executable 不存在或越界：${manifest.executable}`)
  }
  return {python, source: 'user', manifest}
}

export function runtimeStore(userData: string): string {
  return path.join(userData, 'python-runtimes')
}

export function activatePythonRuntime(userData: string, version: string): RuntimeManifest {
  if (!SAFE_VERSION.test(version)) throw new Error('Python 运行时版本号无效')
  const store = runtimeStore(userData)
  const runtime = loadPythonRuntime(path.join(store, 'versions', version))
  const activePath = path.join(store, 'active.json')
  const temporary = `${activePath}.tmp`
  mkdirSync(store, {recursive: true})
  writeFileSync(temporary, `${JSON.stringify({schema: 1, version}, null, 2)}\n`, {encoding: 'utf8', mode: 0o600})
  renameSync(temporary, activePath)
  return runtime.manifest!
}

export function findInstalledPythonRuntime(userData: string): PythonRuntime | undefined {
  const store = runtimeStore(userData)
  const versions = path.join(store, 'versions')
  const activePath = path.join(store, 'active.json')
  if (existsSync(activePath)) {
    try {
      const version = (JSON.parse(readFileSync(activePath, 'utf8')) as {version?: unknown}).version
      if (typeof version === 'string' && SAFE_VERSION.test(version)) {
        return loadPythonRuntime(path.join(versions, version))
      }
    } catch (error) {
      console.warn(`[runtime] 忽略不可用的 active.json：${String(error)}`)
    }
  }
  if (!existsSync(versions)) return undefined
  const candidates = readdirSync(versions, {withFileTypes: true})
    .filter(entry => entry.isDirectory() && SAFE_VERSION.test(entry.name) && !entry.name.startsWith('.'))
    .map(entry => ({name: entry.name, mtime: statSync(path.join(versions, entry.name)).mtimeMs}))
    .sort((left, right) => right.mtime - left.mtime)
  for (const candidate of candidates) {
    try {
      const runtime = loadPythonRuntime(path.join(versions, candidate.name))
      activatePythonRuntime(userData, candidate.name)
      return runtime
    } catch (error) {
      console.warn(`[runtime] 忽略不可用的运行时 ${candidate.name}：${String(error)}`)
    }
  }
  return undefined
}

export function resolvePythonRuntime(options: {
  packaged: boolean
  userData: string
  developmentRoot: string
}): PythonRuntime {
  const configured = process.env.VIDEOAGENTS_PYTHON
  if (configured) {
    if (!existsSync(configured)) throw new Error(`VIDEOAGENTS_PYTHON 不存在：${configured}`)
    return {python: configured, source: 'environment'}
  }
  if (!options.packaged) {
    const python = process.platform === 'win32'
      ? path.join(options.developmentRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(options.developmentRoot, '.venv', 'bin', 'python')
    if (!existsSync(python)) throw new Error(`开发环境 Python 不存在：${python}。请先执行 make install-dev`)
    return {python, source: 'development'}
  }
  const installed = findInstalledPythonRuntime(options.userData)
  if (!installed) throw new Error('Python 运行时尚未安装')
  return installed
}
