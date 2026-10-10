import {spawn, spawnSync} from 'node:child_process'
import {existsSync, mkdirSync, readdirSync, renameSync, rmSync} from 'node:fs'
import path from 'node:path'
import {
  downloadArtifact, extractRuntimeArchive, fetchFfmpegArtifact, FFMPEG_KIND,
} from './runtime-download'

const INSTALL_TIMEOUT_MS = 30 * 60 * 1000
const VERSION_TIMEOUT_MS = 8000

export interface FfmpegStatus {
  ok: boolean
  version?: string
  problem?: string
}

export interface FfmpegInstallProgress {
  message: string
  detail?: string
  received?: number
  total?: number
}

export interface FfmpegInstallOptions {
  environment?: NodeJS.ProcessEnv
  executablePath: string
  /** Windows：自动下载的 FFmpeg 放在这个目录下；它的 bin 目录须已排在 executablePath 里。 */
  userData?: string
  onProgress?: (progress: FfmpegInstallProgress) => void
}

interface InstallCommand {
  executable: string
  args: string[]
  manager: 'homebrew' | 'winget'
}

export const FFMPEG_DOWNLOAD_URL = 'https://ffmpeg.org/download.html'

/** 缺少自动安装所需的包管理器（WinGet / Homebrew）：调用方据此改为引导手动下载。 */
export class FfmpegInstallerMissingError extends Error {
  readonly manager: 'homebrew' | 'winget'
  readonly downloadUrl = FFMPEG_DOWNLOAD_URL
  constructor(manager: 'homebrew' | 'winget', message: string) {
    super(message)
    this.name = 'FfmpegInstallerMissingError'
    this.manager = manager
  }
}

/** 自动下载的 FFmpeg 固定放在这里；路径不随版本变，可以在安装前就排进 PATH。 */
export function managedFfmpegBinDirectory(userData: string): string {
  return path.join(userData, 'ffmpeg', 'current', 'bin')
}

function executableCandidates(
  name: string,
  environment: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
): string[] {
  const extension = platform === 'win32' ? '.exe' : ''
  const candidates = (environment.PATH || '').split(path.delimiter)
    .filter(Boolean)
    .map(directory => path.join(directory, `${name}${extension}`))
  if (platform === 'darwin' && name === 'brew') {
    candidates.push('/opt/homebrew/bin/brew')
  }
  if (platform === 'win32' && name === 'winget') {
    if (environment.LOCALAPPDATA) {
      candidates.push(path.join(environment.LOCALAPPDATA, 'Microsoft', 'WindowsApps', 'winget.exe'))
    }
  }
  return candidates
}

export function findExecutable(
  name: string,
  environment: NodeJS.ProcessEnv,
  platform: NodeJS.Platform = process.platform,
): string | undefined {
  return executableCandidates(name, environment, platform).find(candidate => existsSync(candidate))
}

function toolVersion(tool: 'ffmpeg' | 'ffprobe', environment: NodeJS.ProcessEnv): string | undefined {
  const result = spawnSync(tool, ['-version'], {
    encoding: 'utf8', env: environment, stdio: ['ignore', 'pipe', 'pipe'],
    timeout: VERSION_TIMEOUT_MS, windowsHide: true,
  })
  if (result.status !== 0 || result.error) return undefined
  const firstLine = result.stdout.split(/\r?\n/, 1)[0]?.trim()
  return firstLine && firstLine.toLowerCase().startsWith(`${tool} version `) ? firstLine : undefined
}

/** Checks both commands because several media workflows use ffprobe as well as ffmpeg. */
export function inspectFfmpegEnvironment(environment: NodeJS.ProcessEnv): FfmpegStatus {
  const ffmpeg = toolVersion('ffmpeg', environment)
  if (!ffmpeg) return {ok: false, problem: '找不到可正常运行的 ffmpeg 命令'}
  if (!toolVersion('ffprobe', environment)) {
    return {ok: false, problem: 'ffmpeg 已存在，但配套的 ffprobe 不可用'}
  }
  return {ok: true, version: ffmpeg}
}

export function ffmpegInstallCommand(
  platform: NodeJS.Platform,
  arch: string,
  environment: NodeJS.ProcessEnv,
): InstallCommand {
  if (platform === 'darwin') {
    if (arch !== 'arm64') throw new Error(`不支持自动安装 FFmpeg 的 macOS 架构：${arch}`)
    const brew = findExecutable('brew', environment, platform)
    if (!brew) {
      throw new Error('未找到 Homebrew。请先从 https://brew.sh 安装 Homebrew，然后重新启动 VideoAgents。')
    }
    return {executable: brew, args: ['install', 'ffmpeg'], manager: 'homebrew'}
  }
  if (platform === 'win32') {
    if (arch !== 'x64') throw new Error(`不支持自动安装 FFmpeg 的 Windows 架构：${arch}（需要 x64）`)
    const winget = findExecutable('winget', environment, platform)
    if (!winget) {
      throw new FfmpegInstallerMissingError(
        'winget',
        `未找到 WinGet，暂时无法安装 FFmpeg（AI 自动剪辑需要），这不会影响 VideoAgents 的其他功能。\n请从 FFmpeg 官方下载后手动安装 ${FFMPEG_DOWNLOAD_URL}`,
      )
    }
    return {
      executable: winget,
      args: [
        'install', '--id', 'Gyan.FFmpeg', '--exact', '--source', 'winget', '--silent',
        '--disable-interactivity', '--accept-package-agreements', '--accept-source-agreements',
      ],
      manager: 'winget',
    }
  }
  throw new Error(`当前系统不支持自动安装 FFmpeg：${platform}/${arch}`)
}

function runInstallCommand(
  command: InstallCommand,
  environment: NodeJS.ProcessEnv,
  onProgress?: (progress: FfmpegInstallProgress) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let child: ReturnType<typeof spawn>
    try {
      child = spawn(command.executable, command.args, {
        env: environment, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      })
    } catch (error) {
      reject(error)
      return
    }
    let output = ''
    const receive = (chunk: Buffer): void => {
      const text = chunk.toString('utf8')
      output = `${output}${text}`.slice(-12000)
      const detail = text.trim().split(/\r?\n/).filter(Boolean).at(-1)
      if (detail) onProgress?.({message: '正在安装 FFmpeg…', detail})
    }
    child.stdout?.on('data', receive)
    child.stderr?.on('data', receive)
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error('FFmpeg 安装超时，请检查网络后重试。'))
    }, INSTALL_TIMEOUT_MS)
    child.once('error', error => {
      clearTimeout(timer)
      reject(error)
    })
    child.once('close', code => {
      clearTimeout(timer)
      if (code === 0) {
        resolve()
        return
      }
      const detail = output.trim().split(/\r?\n/).filter(Boolean).slice(-8).join('\n')
      reject(new Error(`${command.manager === 'homebrew' ? 'Homebrew' : 'WinGet'} 安装 FFmpeg 失败（退出码 ${code ?? '未知'}）${detail ? `：\n${detail}` : ''}`))
    })
  })
}

/** 压缩包里带 bin/ffmpeg.exe 与 bin/ffprobe.exe 的那一层目录（上游包外面套了一层版本目录）。 */
function findFfmpegRoot(directory: string, depth = 2): string | undefined {
  const bin = path.join(directory, 'bin')
  if (existsSync(path.join(bin, 'ffmpeg.exe')) && existsSync(path.join(bin, 'ffprobe.exe'))) return directory
  if (depth === 0) return undefined
  for (const entry of readdirSync(directory, {withFileTypes: true})) {
    if (!entry.isDirectory()) continue
    const found = findFfmpegRoot(path.join(directory, entry.name), depth - 1)
    if (found) return found
  }
  return undefined
}

/**
 * 从当前发布源下载 Windows 版 FFmpeg 并解压到 userData，不需要包管理器和管理员权限。
 * 索引没有收录本平台的安装包时返回 false。
 */
export async function installManagedFfmpeg(
  userData: string,
  onProgress: (progress: FfmpegInstallProgress) => void = () => undefined,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): Promise<boolean> {
  onProgress({message: '正在检查 FFmpeg 安装包…'})
  const artifact = await fetchFfmpegArtifact(platform, arch)
  if (!artifact) return false
  const target = path.dirname(managedFfmpegBinDirectory(userData))
  const store = path.dirname(target)
  const archive = path.join(store, `${artifact.version}.zip.part`)
  const staging = path.join(store, `.installing-${process.pid}`)
  mkdirSync(store, {recursive: true})
  rmSync(archive, {force: true})
  rmSync(staging, {recursive: true, force: true})
  try {
    await downloadArtifact(artifact, archive, progress => onProgress({
      message: progress.message, received: progress.received, total: progress.total,
    }), FFMPEG_KIND)
    onProgress({message: '正在解压 FFmpeg…'})
    mkdirSync(staging, {recursive: true})
    await extractRuntimeArchive(archive, staging, FFMPEG_KIND)
    const root = findFfmpegRoot(staging)
    if (!root) throw new Error('FFmpeg 安装包里没有 ffmpeg.exe 与 ffprobe.exe')
    // 只用 ffmpeg / ffprobe；播放器占三分之一体积，不留。
    rmSync(path.join(root, 'bin', 'ffplay.exe'), {force: true})
    rmSync(target, {recursive: true, force: true})
    renameSync(root, target)
    return true
  } finally {
    rmSync(archive, {force: true})
    rmSync(staging, {recursive: true, force: true})
  }
}

function describeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return message === 'fetch failed' ? '无法连接下载服务器，请检查网络' : message
}

export async function installFfmpeg(options: FfmpegInstallOptions): Promise<FfmpegStatus> {
  const environment: NodeJS.ProcessEnv = {
    ...process.env, ...options.environment, PATH: options.executablePath,
  }
  const existing = inspectFfmpegEnvironment(environment)
  if (existing.ok) return existing

  // Windows 先从自己的发布源下载；没下成（索引未收录、网络不通、校验不过）再退到 WinGet。
  let downloadProblem: string | undefined
  if (process.platform === 'win32' && options.userData) {
    try {
      if (await installManagedFfmpeg(options.userData, options.onProgress)) {
        const downloaded = inspectFfmpegEnvironment(environment)
        if (downloaded.ok) return downloaded
        downloadProblem = `FFmpeg 已下载，但环境验收失败：${downloaded.problem}`
      } else {
        downloadProblem = '当前发布源暂未提供 FFmpeg 安装包'
      }
    } catch (error) {
      downloadProblem = describeError(error)
    }
    console.warn(`[ffmpeg] 自动下载未完成，改试 WinGet：${downloadProblem}`)
  }

  let command: InstallCommand
  try {
    command = ffmpegInstallCommand(process.platform, process.arch, environment)
  } catch (error) {
    if (downloadProblem && error instanceof FfmpegInstallerMissingError) {
      throw new FfmpegInstallerMissingError(
        error.manager,
        `自动下载 FFmpeg 未完成：${downloadProblem}。本机也没有 WinGet 可用。\nFFmpeg 只有 AI 自动剪辑需要，这不会影响 VideoAgents 的其他功能。\n可以稍后重新启动应用再试，或从 FFmpeg 官方下载后手动安装 ${FFMPEG_DOWNLOAD_URL}`,
      )
    }
    throw error
  }
  options.onProgress?.({
    message: command.manager === 'homebrew' ? '正在通过 Homebrew 安装 FFmpeg…' : '正在通过 WinGet 安装 FFmpeg…',
  })
  try {
    await runInstallCommand(command, environment, options.onProgress)
  } catch (error) {
    if (!downloadProblem) throw error
    throw new Error(`${describeError(error)}\n\n此前自动下载也未完成：${downloadProblem}`)
  }

  // The desktop PATH includes Homebrew and WinGet link directories up front, so
  // newly created command links are visible without restarting Electron.
  const refreshedEnvironment = {...environment, PATH: options.executablePath}
  const installed = inspectFfmpegEnvironment(refreshedEnvironment)
  if (!installed.ok) {
    throw new Error(`FFmpeg 安装命令已完成，但环境验收失败：${installed.problem}`)
  }
  return installed
}
