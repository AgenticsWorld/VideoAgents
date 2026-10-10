import {app, BrowserWindow, dialog, ipcMain, Menu, MenuItemConstructorOptions, session, shell, systemPreferences} from 'electron'
import type {MessageBoxOptions} from 'electron'
import {ChildProcess, spawn, spawnSync} from 'node:child_process'
import {existsSync, mkdirSync, writeFileSync} from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import path from 'node:path'
import {activatePythonRuntime, PythonRuntime, resolvePythonRuntime, runtimeStore} from './runtime'
import {fetchLatestRuntimeArtifact, installLatestPythonRuntime, RuntimeProgress} from './runtime-download'
import {
  cacheRequiredDesktopUpdate, clearCachedRequiredDesktopUpdate, DesktopArtifact, DesktopUpdate,
  downloadAndApplyDesktopUpdate, fetchDesktopUpdate, readBuildInfo, readCachedRequiredDesktopUpdate,
} from './desktop-update'
import {
  AgenticsApiError, authorizationUrl, clearStoredAuth, createPkceSession, exchangeAuthorizationCode,
  fetchUserAccount, loadStoredAuth, parseAuthorizationCallback, PkceSession, saveStoredAuth,
  ServiceRegion, serviceRegion, UserAccount,
} from './desktop-auth'
import {desktopExecutablePath} from './shell-environment'
import {
  FfmpegInstallerMissingError, inspectFfmpegEnvironment, installFfmpeg, managedFfmpegBinDirectory,
} from './ffmpeg-environment'

let webServer: ChildProcess | undefined
let webPort = process.env.VIDEOAGENTS_WEB_PORT || ''
let apiPort = process.env.VIDEOAGENTS_API_PORT || ''
let webOrigin = process.env.VIDEOAGENTS_WEB_URL?.replace(/\/$/, '') || ''
let window: BrowserWindow | undefined
let mainWindowWasCreated = false
let activeRuntime: PythonRuntime | undefined
let authWindow: BrowserWindow | undefined
let authToken = ''
let authRegion: ServiceRegion | undefined
let currentAccount: UserAccount | undefined

class LoginCancelledError extends Error {}

interface PendingLogin {
  session: PkceSession
  region: ServiceRegion
  resolve: (value: LoginResult) => void
  reject: (reason: Error) => void
}

type LoginResult = {token: string; account: UserAccount} | undefined

let pendingLogin: PendingLogin | undefined
const ownsSingleInstance = app.requestSingleInstanceLock()

function deepLinkFromArguments(args: string[]): string | undefined {
  return args.find(value => value.startsWith('videoagents://'))
}

async function handleAuthorizationCallback(value: string): Promise<boolean> {
  const pending = pendingLogin
  if (!pending) return false
  const callback = parseAuthorizationCallback(
    value, pending.session.state, pending.region.redirectUri,
  )
  if (!callback) return false
  pendingLogin = undefined
  try {
    const token = await exchangeAuthorizationCode(pending.region, callback.code, pending.session.verifier)
    const account = await fetchUserAccount(pending.region, token)
    if (authWindow && !authWindow.isDestroyed()) authWindow.close()
    pending.resolve({token, account})
  } catch (error) {
    if (authWindow && !authWindow.isDestroyed()) authWindow.close()
    pending.reject(error instanceof Error ? error : new Error(String(error)))
  }
  return true
}

app.on('open-url', (event, url) => {
  if (!url.startsWith('videoagents://')) return
  event.preventDefault()
  void handleAuthorizationCallback(url)
})

app.on('second-instance', (_event, argv) => {
  const callback = deepLinkFromArguments(argv)
  if (callback) void handleAuthorizationCallback(callback)
  const target = authWindow && !authWindow.isDestroyed() ? authWindow : window
  if (target && !target.isDestroyed()) {
    if (target.isMinimized()) target.restore()
    target.show()
    target.focus()
  }
})

function stopWebServerTree(): void {
  const server = webServer
  webServer = undefined
  if (!server || server.pid === undefined || server.exitCode !== null) return
  if (process.platform === 'win32') {
    const result = spawnSync(
      'taskkill.exe', ['/pid', String(server.pid), '/t', '/f'],
      {stdio: 'ignore', windowsHide: true},
    )
    if (result.error) console.warn(`[web] failed to stop backend process tree: ${String(result.error)}`)
    return
  }
  server.kill('SIGTERM')
}
let runtimeProgressWindow: BrowserWindow | undefined
// 下载速度采样:at=上次渲染时刻,received=上次渲染时已收字节,speed=指数平滑后的字节/秒
const runtimeProgressMeter = {at: 0, received: 0, speed: 0}
let runtimeUpdateInProgress = false
let requiredDesktopUpdateActive = false
let requiredDesktopUpdateCanQuit = false

function webRoot(): string {
  const packaged = path.join(process.resourcesPath, 'backend', 'apps', 'web')
  const development = path.resolve(__dirname, '../../web')
  return app.isPackaged ? packaged : development
}

function backendRoot(): string {
  return app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(webRoot(), '../..')
}

function desktopDataRoot(): string {
  return process.env.VIDEOAGENTS_DATA_DIR || path.join(app.getPath('userData'), 'data')
}

function bundledChromiumPath(userData: string, runtime: PythonRuntime): string | undefined {
  // Packaged runtimes carry Chromium beside the compact `py/` distribution.
  // Resolve from the versioned store rather than from the executable layout so
  // this remains valid on both macOS and Windows.
  if (runtime.source !== 'user' || !runtime.manifest?.version) {
    return process.env.PLAYWRIGHT_BROWSERS_PATH || undefined
  }
  return path.join(runtimeStore(userData), 'versions', runtime.manifest.version, 'playwright-browsers')
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character] || character)
}

function writeInitialJson(target: string, value: object): void {
  if (existsSync(target)) return
  mkdirSync(path.dirname(target), {recursive: true})
  try {
    writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, {
      encoding: 'utf8', mode: 0o600, flag: 'wx',
    })
  } catch (error) {
    const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined
    if (code !== 'EEXIST') throw error
  }
}

function initializeFirstLoginDefaults(): void {
  const runtime = path.join(desktopDataRoot(), '.videoagents')
  // Only create missing files. Existing installations may already contain user
  // choices from an older desktop or WebUI version and must never be overwritten.
  writeInitialJson(path.join(runtime, 'genconfig.json'), {
    image: {provider: 'agentics'},
    video: {provider: 'agentics'},
    music: {provider: 'agentics'},
    tts: {provider: 'agentics'},
    deepagents: {provider: 'agentics'},
  })
  writeInitialJson(path.join(runtime, 'state.json'), {
    sessions: {},
    // 顶栏默认引擎与网页版一致为 claude(2026-09-22);model '__smart' = 智能分配,
    // 服务端副本 model 留空 = 跟随 CLI 默认(与网页版无 state.json 的首装行为相同)
    ui_prefs: {engine: 'claude', model: '__smart', model_custom: '', project: ''},
    global_model: {engine: 'claude', model: ''},
  })
}

async function loginOnce(region: ServiceRegion): Promise<LoginResult> {
  const session = createPkceSession()
  const url = authorizationUrl(region, session)
  const skipUrl = 'videoagents://skip-login'
  authWindow = new BrowserWindow({
    width: 520, height: 330, resizable: false, minimizable: false, maximizable: false,
    title: '登录 VideoAgents', backgroundColor: '#111318',
    webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true},
  })
  const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
  <style>body{margin:0;background:#111318;color:#f3f4f6;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  main{padding:42px;text-align:center}h1{font-size:21px;margin:0 0 18px}p{color:#b8bec9;line-height:1.7;margin:0 0 24px}
  a{display:inline-block;background:#5677ff;color:white;text-decoration:none;border-radius:8px;padding:11px 24px;font-weight:600}
  a.skip{display:block;background:none;color:#8e96a5;padding:6px;margin:16px auto 0;font-weight:400;width:max-content}</style>
  <main><h1>登录后使用 VideoAgents</h1>
  <p>点击登录后会在系统浏览器中打开登录页面，<br>完成登录和授权后会自动返回。</p>
  <a href="${escapeHtml(url)}" target="_blank">登录</a>
  <a class="skip" href="${skipUrl}">暂不登录</a></main>`
  await authWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
  const openLoginPage = (): void => {
    void shell.openExternal(url).catch(error => {
      const request = pendingLogin
      if (!request) return
      pendingLogin = undefined
      request.reject(error instanceof Error ? error : new Error(String(error)))
      if (authWindow && !authWindow.isDestroyed()) authWindow.close()
    })
  }
  authWindow.webContents.setWindowOpenHandler(({url: target}) => {
    if (target === url) openLoginPage()
    return {action: 'deny'}
  })
  authWindow.webContents.on('will-navigate', (event, target) => {
    event.preventDefault()
    if (target === url) {
      openLoginPage()
      return
    }
    if (target === skipUrl) {
      const request = pendingLogin
      if (!request) return
      pendingLogin = undefined
      request.resolve(undefined)
      if (authWindow && !authWindow.isDestroyed()) authWindow.close()
    }
  })
  authWindow.show()

  return await new Promise((resolve, reject) => {
    const request: PendingLogin = {session, region, resolve, reject}
    pendingLogin = request
    authWindow?.once('closed', () => {
      authWindow = undefined
      if (pendingLogin === request) {
        pendingLogin = undefined
        reject(new LoginCancelledError('用户取消登录'))
      }
    })
  })
}

async function interactiveLogin(
  region: ServiceRegion, cancelLabel = '退出应用',
): Promise<LoginResult> {
  while (true) {
    try {
      return await loginOnce(region)
    } catch (error) {
      if (error instanceof LoginCancelledError) throw error
      const answer = await dialog.showMessageBox({
        type: 'error', title: 'VideoAgents 登录失败',
        message: '未能完成登录',
        detail: error instanceof Error ? error.message : String(error),
        buttons: ['重试', cancelLabel], defaultId: 0, cancelId: 1, noLink: true,
      })
      if (answer.response !== 0) throw new LoginCancelledError('用户取消登录')
    }
  }
}

async function requireDesktopLogin(build: ReturnType<typeof readBuildInfo>): Promise<void> {
  const region = serviceRegion(build)
  authRegion = region
  const userData = app.getPath('userData')
  const saved = loadStoredAuth(userData, region.environment)
  let auth = saved
  if (auth) {
    while (auth) {
      try {
        currentAccount = await fetchUserAccount(region, auth.token)
        break
      } catch (error) {
        if (error instanceof AgenticsApiError && error.status === 401) {
          console.warn('[auth] saved session has expired')
          clearStoredAuth(userData, region.environment)
          auth = undefined
          break
        }
        const answer = await dialog.showMessageBox({
          type: 'error', title: '无法验证 VideoAgents 登录状态',
          message: '暂时无法连接登录服务',
          detail: error instanceof Error ? error.message : String(error),
          buttons: ['重试', '退出应用'], defaultId: 0, cancelId: 1, noLink: true,
        })
        if (answer.response !== 0) throw new LoginCancelledError('用户取消登录验证')
      }
    }
  }
  if (!auth) {
    const result = await interactiveLogin(region)
    if (!result) {
      authToken = ''
      currentAccount = undefined
      return
    }
    auth = {token: result.token, onboarded: Boolean(saved?.onboarded)}
    currentAccount = result.account
  }
  if (!auth.onboarded) {
    initializeFirstLoginDefaults()
    auth.onboarded = true
  }
  saveStoredAuth(userData, auth, region.environment)
  authToken = auth.token
  authRegion = region
}

async function findAvailablePort(): Promise<string> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : undefined
      server.close(error => {
        if (error) reject(error)
        else if (port) resolve(String(port))
        else reject(new Error('未能分配本地端口'))
      })
    })
  })
}

async function ensureLocalPorts(): Promise<void> {
  if (process.env.VIDEOAGENTS_WEB_URL) return
  if (!webPort) webPort = await findAvailablePort()
  if (!apiPort) {
    do {
      apiPort = await findAvailablePort()
    } while (apiPort === webPort)
  }
  webOrigin = `http://127.0.0.1:${webPort}`
}

async function requestOk(url: string): Promise<boolean> {
  return new Promise(resolve => {
    const client = url.startsWith('https:') ? https : http
    const req = client.get(url, response => {
      response.resume(); resolve(response.statusCode === 200)
    })
    req.setTimeout(800, () => {req.destroy(); resolve(false)})
    req.on('error', () => resolve(false))
  })
}

async function healthy(): Promise<boolean> {
  return (await requestOk(webOrigin)) && (await requestOk(`${webOrigin}/api/v1/health`))
}

async function showRuntimeProgress(
  title = '正在准备 VideoAgents',
  note = '首次启动需要下载一次，之后不会自动更新。',
  windowTitle = 'VideoAgents Python 环境',
): Promise<void> {
  if (runtimeProgressWindow && !runtimeProgressWindow.isDestroyed()) return
  runtimeProgressWindow = new BrowserWindow({
    width: 520, height: 230, resizable: false, minimizable: false, maximizable: false,
    closable: false, title: windowTitle, backgroundColor: '#111318',
    webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true},
  })
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;background:#111318;color:#f3f4f6;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{padding:34px}h2{font-size:18px;margin:0 0 18px}p{color:#b8bec9;height:22px;margin:0 0 14px}
    progress{width:100%;height:12px;accent-color:#5677ff}small{display:block;color:#727987;margin-top:12px}
    #stats{color:#8b93a3;font-size:12px;height:16px;margin:8px 0 0;font-variant-numeric:tabular-nums}
  </style><main><h2>${title}</h2><p id="message">正在检查更新…</p>
  <progress id="progress" max="100"></progress><div id="stats"></div><small>${note}</small></main>`
  await runtimeProgressWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
}

function formatBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(value / 1024))} KB`
}

function resetRuntimeProgressMeter(): void {
  runtimeProgressMeter.at = 0
  runtimeProgressMeter.received = 0
  runtimeProgressMeter.speed = 0
}

function updateRuntimeProgress(progress: RuntimeProgress): void {
  const target = runtimeProgressWindow
  if (!target || target.isDestroyed()) return
  const downloading = progress.received !== undefined && typeof progress.total === 'number' && progress.total > 0
  let percent: number | undefined
  let stats = ''
  if (downloading) {
    const received = progress.received as number
    const total = progress.total as number
    // 同一进度窗口内的第二次下载（字节数回退）视为新任务，重置测速
    if (received < runtimeProgressMeter.received) resetRuntimeProgressMeter()
    // 每个数据块都会回调一次；限频渲染，避免高频 executeJavaScript(最后一块除外)
    if (runtimeProgressMeter.at && Date.now() - runtimeProgressMeter.at < 200 && received < total) return
    const now = Date.now()
    if (runtimeProgressMeter.at && now > runtimeProgressMeter.at) {
      const instant = (received - runtimeProgressMeter.received) / ((now - runtimeProgressMeter.at) / 1000)
      runtimeProgressMeter.speed = runtimeProgressMeter.speed
        ? runtimeProgressMeter.speed * 0.7 + instant * 0.3 : instant
    }
    runtimeProgressMeter.at = now
    runtimeProgressMeter.received = received
    percent = Math.min(100, Math.round(received / total * 100))
    stats = `${formatBytes(received)} / ${formatBytes(total)} · ${percent}%`
      + (runtimeProgressMeter.speed > 0 ? ` · ${formatBytes(runtimeProgressMeter.speed)}/s` : '')
  } else {
    resetRuntimeProgressMeter()
  }
  void target.webContents.executeJavaScript(`(() => {
    document.getElementById('message').textContent = ${JSON.stringify(progress.message)};
    document.getElementById('stats').textContent = ${JSON.stringify(stats)};
    const bar = document.getElementById('progress');
    ${percent === undefined ? "bar.removeAttribute('value')" : `bar.value = ${percent}`};
  })()`)
}

function closeRuntimeProgress(): void {
  if (runtimeProgressWindow && !runtimeProgressWindow.isDestroyed()) runtimeProgressWindow.destroy()
  runtimeProgressWindow = undefined
  resetRuntimeProgressMeter()
}

function executableSearchPath(): string {
  // The managed FFmpeg directory is listed even before anything is installed
  // there, so the running web server finds FFmpeg right after setup completes.
  return desktopExecutablePath(
    process.env,
    process.platform === 'win32' ? [managedFfmpegBinDirectory(app.getPath('userData'))] : [],
  )
}

async function offerFfmpegEnvironmentSetup(): Promise<void> {
  // FFmpeg is an optional desktop helper. Check it only after the main window
  // exists, including when the user chose “暂不登录”; never delay the main flow.
  if (!window || window.isDestroyed()) return
  let executablePath = executableSearchPath()
  const environment: NodeJS.ProcessEnv = {...process.env, PATH: executablePath}
  const existing = inspectFfmpegEnvironment(environment)
  if (existing.ok) {
    console.log(`[ffmpeg] ${existing.version}`)
    return
  }
  console.log(`[ffmpeg] optional environment unavailable: ${existing.problem}`)
  const answer = await dialog.showMessageBox(window, {
    type: 'warning',
    title: '需要 FFmpeg 才能使用完整视频功能',
    message: '未检测到可用的 FFmpeg 环境',
    detail: `${existing.problem}。部分视频处理功能可能不可用，但不影响其他功能。\n\n`
      + (process.platform === 'darwin'
        ? '可以通过 Homebrew 自动安装 Apple Silicon 版 FFmpeg。'
        : '可以自动下载并安装 Windows x64 版 FFmpeg，不需要管理员权限。'),
    buttons: ['自动安装', '暂时忽略'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  })
  if (answer.response !== 0) {
    console.log('[ffmpeg] setup ignored by user')
    return
  }
  await showRuntimeProgress(
    '正在安装 FFmpeg',
    process.platform === 'darwin'
      ? 'VideoAgents 将通过 Homebrew 安装 Apple Silicon 版 FFmpeg。'
      : 'VideoAgents 将下载并安装 Windows x64 版 FFmpeg。',
    'VideoAgents FFmpeg 环境',
  )
  try {
    // Rebuild after installation too: WinGet creates command links while this
    // Electron process is running, and a process restart should not be required.
    const installed = await installFfmpeg({
      executablePath,
      environment,
      userData: app.getPath('userData'),
      onProgress: progress => {
        updateRuntimeProgress({
          phase: progress.received === undefined ? 'extracting' : 'downloading',
          message: progress.detail || progress.message,
          received: progress.received, total: progress.total,
        })
      },
    })
    executablePath = executableSearchPath()
    const verified = inspectFfmpegEnvironment({...process.env, PATH: executablePath})
    if (!verified.ok) throw new Error(verified.problem)
    console.log(`[ffmpeg] installed: ${installed.version}`)
    closeRuntimeProgress()
    if (window && !window.isDestroyed()) {
      await dialog.showMessageBox(window, {
        type: 'info', title: 'FFmpeg 安装完成', message: 'FFmpeg 环境已准备完成。',
      })
    }
  } catch (error) {
    closeRuntimeProgress()
    const message = error instanceof Error ? error.message : String(error)
    console.warn(`[ffmpeg] optional setup failed: ${message}`)
    if (window && !window.isDestroyed() && error instanceof FfmpegInstallerMissingError) {
      // 自动下载没成、又缺包管理器（Windows 无 WinGet）：引导用户去 FFmpeg 官网手动下载安装。
      const answer = await dialog.showMessageBox(window, {
        type: 'warning',
        title: 'FFmpeg 安装未完成',
        message: '暂时无法安装 FFmpeg',
        detail: message,
        buttons: ['前往下载', '暂时跳过'],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      })
      if (answer.response === 0) void shell.openExternal(error.downloadUrl)
    } else if (window && !window.isDestroyed()) {
      await dialog.showMessageBox(window, {
        type: 'warning',
        title: 'FFmpeg 安装未完成',
        message: '暂时无法安装 FFmpeg',
        detail: `${message}\n\n这不会影响 VideoAgents 的其他功能，可以稍后重新启动应用再试。`,
        buttons: ['关闭'],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
      })
    }
  } finally {
    closeRuntimeProgress()
  }
}

async function ensurePythonRuntime(backend: string): Promise<PythonRuntime> {
  let installed: PythonRuntime | undefined
  try {
    installed = resolvePythonRuntime({
      packaged: app.isPackaged,
      userData: app.getPath('userData'),
      developmentRoot: backend,
    })
  } catch (error) {
    if (!app.isPackaged || process.env.VIDEOAGENTS_PYTHON) throw error
  }
  if (installed) {
    // 已装运行时的依赖清单与本次 app 构建期望不一致(app 升级带出了新依赖):
    // 自动更新运行时,老用户升级 app 后不再卡在旧 Python 环境缺包。
    // 更新检查/下载失败(离线、索引不可达)沿用现有运行时,不阻断启动。
    const expected = readBuildInfo(process.resourcesPath, app.isPackaged).requirementsSha256
    const actual = installed.manifest?.requirementsSha256
    if (installed.source !== 'user' || !expected || !actual || expected === actual) return installed
    try {
      const latest = await fetchLatestRuntimeArtifact()
      if (latest.version === installed.manifest?.version) {
        console.warn('[runtime] 依赖清单与 app 期望不一致,但索引暂无更新的运行时,沿用现有运行时')
        return installed
      }
      console.log(`[runtime] 依赖清单过期(${actual.slice(0, 12)} → ${expected.slice(0, 12)}),自动更新运行时到 ${latest.version}`)
    } catch (error) {
      console.warn(`[runtime] 运行时更新检查失败,沿用现有运行时:${String(error)}`)
      return installed
    }
  }
  await showRuntimeProgress()
  try {
    return (await installLatestPythonRuntime(app.getPath('userData'), updateRuntimeProgress)).runtime
  } catch (error) {
    if (installed) {
      console.warn(`[runtime] 运行时自动更新失败,沿用现有运行时:${String(error)}`)
      return installed
    }
    throw error
  } finally {
    closeRuntimeProgress()
  }
}

async function ensureWebServer(): Promise<void> {
  await ensureLocalPorts()
  if (await healthy()) return
  if (process.env.VIDEOAGENTS_WEB_URL) throw new Error(`Web 服务不可用：${webOrigin}`)

  const dataRoot = desktopDataRoot()
  mkdirSync(path.join(dataRoot, 'projects'), {recursive: true})
  // macOS 会校验整个已签名的 .app。Python 若在 Resources/backend 写入
  // __pycache__，会使应用在下次启动时因签名失效而被 Gatekeeper 拒绝。
  const pythonCacheRoot = path.join(dataRoot, 'python-cache')
  mkdirSync(pythonCacheRoot, {recursive: true})
  const root = webRoot()
  const backend = backendRoot()
  activeRuntime = await ensurePythonRuntime(backend)
  console.log(`[runtime] ${activeRuntime.source}: ${activeRuntime.manifest?.version || activeRuntime.python}`)
  const chromiumPath = bundledChromiumPath(app.getPath('userData'), activeRuntime)
  const clientBuild = readBuildInfo(process.resourcesPath, app.isPackaged)
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PATH: executableSearchPath(),
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONNOUSERSITE: '1',
    PYTHONPYCACHEPREFIX: pythonCacheRoot,
    VIDEOAGENTS_APP_ROOT: backend,
    VIDEOAGENTS_DATA_DIR: dataRoot,
    VIDEOAGENTS_PERMISSION_MODE: process.env.VIDEOAGENTS_PERMISSION_MODE || 'bypassPermissions',
    VIDEOAGENTS_WEB_HOST: '127.0.0.1',
    VIDEOAGENTS_WEB_PORT: webPort,
    VIDEOAGENTS_API_PORT: apiPort,
    VIDEOAGENTS_USER_JWT: authToken,
    VIDEOAGENTS_SERVICE_ENV: authRegion?.environment || '',
    VIDEOAGENTS_SERVICE_DISTRIBUTION: authRegion?.distribution || 's3',
    VIDEOAGENTS_SERVICE_SSO_ORIGIN: authRegion?.ssoOrigin || '',
    VIDEOAGENTS_SERVICE_API_ORIGIN: authRegion?.apiOrigin || '',
    VIDEOAGENTS_OPENROUTER_WRAPPER_URL: authRegion?.openrouterWrapperUrl || '',
    // 客户端版本:Issue 反馈提交正文里的「客户端」行(modules/issue_feedback.py)。
    VIDEOAGENTS_CLIENT_VERSION: app.getVersion(),
    VIDEOAGENTS_CLIENT_BUILD: `${clientBuild.channel}/${clientBuild.buildHash}`,
    ...(chromiumPath ? {PLAYWRIGHT_BROWSERS_PATH: chromiumPath} : {}),
  }
  webServer = spawn(activeRuntime.python, [path.join(root, 'server.py')], {
    cwd: backend,
    env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
  })
  webServer.stdout?.on('data', chunk => console.log(`[web] ${chunk}`))
  webServer.stderr?.on('data', chunk => console.error(`[web] ${chunk}`))
  for (let attempt=0; attempt<60; attempt++) {
    if (await healthy()) return
    if (webServer.exitCode !== null) throw new Error(`Python Web 服务退出：${webServer.exitCode}`)
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('Python Web 服务启动超时')
}

/** 麦克风权限(语音输入):只放行本机 Web 服务页面的音频采集,其它权限沿用 Electron 默认(允许)。
 *  macOS 首次访问由系统按 Info.plist 的 NSMicrophoneUsageDescription 弹授权框;这里先主动请求,
 *  避免 Chromium 在未授权时直接把 getUserMedia 判为 NotAllowedError。 */
function installMediaPermissionHandler(): void {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    if (permission !== 'media') { callback(true); return }
    const mediaTypes = (details as {mediaTypes?: string[]}).mediaTypes ?? []
    const fromApp = !details.requestingUrl || details.requestingUrl.startsWith(webOrigin)
    if (!fromApp || mediaTypes.includes('video')) { callback(false); return }
    if (process.platform === 'darwin') {
      systemPreferences.askForMediaAccess('microphone').then(callback, () => callback(false))
      return
    }
    callback(true)
  })
}

/** 应用页面窗口(主窗口与「新建窗口」共用):都连同一个本机 Web 服务,不另起后台进程。 */
const appWindows = new Set<BrowserWindow>()

async function openAppWindow(): Promise<BrowserWindow> {
  // 新窗口相对当前窗口错开一点,避免完全盖住
  const anchor = BrowserWindow.getFocusedWindow() ?? window
  const position = anchor && !anchor.isDestroyed() && appWindows.has(anchor) && !anchor.isMaximized() && !anchor.isFullScreen()
    ? {x: anchor.getPosition()[0] + 28, y: anchor.getPosition()[1] + 28}
    : {}
  const created = new BrowserWindow({
    width: 1440, height: 920, minWidth: 980, minHeight: 680, ...position,
    backgroundColor: '#0c0d11', title: 'VideoAgents',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false, contextIsolation: true, sandbox: true
    }
  })
  appWindows.add(created)
  if (!window || window.isDestroyed()) window = created
  mainWindowWasCreated = true
  created.on('closed', () => {
    appWindows.delete(created)
    // 主窗口关掉后,把对话框/登录回跳的宿主让给剩下的窗口
    if (window === created) window = [...appWindows][0]
  })
  created.webContents.setWindowOpenHandler(({url}) => {
    if (url.startsWith('https://')) void shell.openExternal(url)
    return {action:'deny'}
  })
  created.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(webOrigin)) event.preventDefault()
  })
  await created.loadURL(webOrigin)
  return created
}

async function createWindow(): Promise<void> {
  await ensureWebServer()
  installMediaPermissionHandler()
  await openAppWindow()
}

async function openNewWindow(): Promise<void> {
  if (!mainWindowWasCreated || requiredDesktopUpdateActive) return
  await ensureWebServer()
  await openAppWindow()
}

async function updatePythonRuntimeManually(): Promise<void> {
  if (runtimeUpdateInProgress) return
  runtimeUpdateInProgress = true
  const previous = activeRuntime?.manifest?.version
  await showRuntimeProgress()
  try {
    const result = await installLatestPythonRuntime(app.getPath('userData'), updateRuntimeProgress)
    closeRuntimeProgress()
    if (previous === result.artifact.version) {
      await dialog.showMessageBox({type: 'info', title: 'Python 环境', message: '当前已经是最新 Python 环境。'})
      return
    }
    await dialog.showMessageBox({
      type: 'info', title: 'Python 环境已更新',
      message: `Python 环境已更新到 ${result.artifact.version}，应用将重新启动。`,
    })
    app.relaunch()
    stopWebServerTree()
    app.exit(0)
  } catch (error) {
    closeRuntimeProgress()
    const message = error instanceof Error ? error.message : String(error)
    dialog.showErrorBox('Python 环境更新失败', message)
    throw error
  } finally {
    runtimeUpdateInProgress = false
  }
}

function installApplicationMenu(): void {
  const updateItem: MenuItemConstructorOptions = {
    label: '检查并更新 Python 环境…',
    click: () => {void updatePythonRuntimeManually().catch(error => console.error(error))},
  }
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{
      label: app.name,
      submenu: [{role: 'about' as const}, {type: 'separator' as const}, updateItem,
        {type: 'separator' as const}, {role: 'quit' as const}],
    }] : [{label: '环境', submenu: [updateItem]}]),
    {label: '编辑', submenu: [{role: 'undo'}, {role: 'redo'}, {type: 'separator'},
      {role: 'cut'}, {role: 'copy'}, {role: 'paste'}, {role: 'selectAll'}]},
    {label: '窗口', submenu: [
      {label: '新建窗口', accelerator: 'CmdOrCtrl+N',
        click: () => {void openNewWindow().catch(error => console.error(error))}},
      {type: 'separator'}, {role: 'reload'}, {role: 'toggleDevTools'}, {type: 'separator'},
      {role: 'minimize'}, {role: 'close'}]},
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

async function applyDesktopUpdate(artifact: DesktopArtifact): Promise<void> {
  const currentAppPath = process.platform === 'darwin'
    ? path.resolve(process.resourcesPath, '..', '..')
    : path.resolve(process.resourcesPath, '..')
  await downloadAndApplyDesktopUpdate(
    app.getPath('userData'), currentAppPath, artifact, updateRuntimeProgress,
  )
}

async function offerDesktopUpdate(update: DesktopUpdate, currentVersion: string): Promise<void> {
  const {artifact} = update
  const options: MessageBoxOptions = {
    type: 'info', title: '发现 VideoAgents 更新',
    message: `发现 VideoAgents ${artifact.version} 新版本。`,
    detail: `当前版本 ${currentVersion}。是否立即下载并自动安装？`,
    buttons: ['下载并更新', '暂不更新'], defaultId: 0, cancelId: 1,
  }
  const answer = window
    ? await dialog.showMessageBox(window, options)
    : await dialog.showMessageBox(options)
  if (answer.response !== 0) return
  await showRuntimeProgress(
    '正在更新 VideoAgents', '下载完成后应用会自动安装并重新启动。', 'VideoAgents 更新',
  )
  try {
    await applyDesktopUpdate(artifact)
    closeRuntimeProgress()
    if (process.platform === 'win32') {
      stopWebServerTree()
      app.exit(0)
    } else {
      app.quit()
    }
  } catch (error) {
    closeRuntimeProgress()
    const message = error instanceof Error ? error.message : String(error)
    dialog.showErrorBox('VideoAgents 更新失败', message)
  }
}

async function enforceDesktopUpdate(update: DesktopUpdate, currentVersion: string): Promise<never> {
  const {artifact, minimumVersion} = update
  requiredDesktopUpdateActive = true
  Menu.setApplicationMenu(null)
  let lastError = ''
  while (true) {
    const answer = await dialog.showMessageBox({
      type: lastError ? 'error' : 'warning',
      title: '必须更新 VideoAgents',
      message: lastError ? '更新失败，请重试' : '当前版本已停止支持',
      detail: lastError
        ? `${lastError}\n\n当前版本 ${currentVersion}，最低可用版本 ${minimumVersion}。`
        : `当前版本 ${currentVersion} 低于最低可用版本 ${minimumVersion}。`
          + ` 必须更新到 ${artifact.version} 后才能继续使用。`,
      buttons: ['立即更新', '退出应用'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    })
    if (answer.response !== 0) {
      requiredDesktopUpdateCanQuit = true
      stopWebServerTree()
      app.exit(0)
      await new Promise<never>(() => {})
    }
    await showRuntimeProgress(
      '正在强制更新 VideoAgents',
      '更新完成并重新启动前，不能使用应用的其他功能。',
      'VideoAgents 必须更新',
    )
    try {
      await applyDesktopUpdate(artifact)
      closeRuntimeProgress()
      requiredDesktopUpdateCanQuit = true
      if (process.platform === 'win32') {
        stopWebServerTree()
        app.exit(0)
      } else {
        app.quit()
      }
      await new Promise<never>(() => {})
    } catch (error) {
      closeRuntimeProgress()
      lastError = error instanceof Error ? error.message : String(error)
    }
  }
}

ipcMain.on('desktop:version', event => {event.returnValue = app.getVersion()})
// 发行版标识:oss = shumati.cn,s3 = agentics.world;页面据此把「账号 ID」显示为 SMT ID / AGT ID
ipcMain.on('desktop:distribution', event => {
  event.returnValue = (authRegion ?? serviceRegion(readBuildInfo(process.resourcesPath, app.isPackaged))).distribution
})
ipcMain.handle('desktop:open-external', async (_event, value: unknown) => {
  if (typeof value !== 'string' || !/^https?:\/\//.test(value)) throw new Error('不允许的 URL')
  await shell.openExternal(value)
})
ipcMain.handle('desktop:restart-backend', async () => {
  stopWebServerTree(); await ensureWebServer()
  for (const item of appWindows) item.reload()
})
ipcMain.handle('desktop:runtime-info', () => ({
  source: activeRuntime?.source,
  manifest: activeRuntime?.manifest,
  updateDirectory: path.join(runtimeStore(app.getPath('userData')), 'versions'),
}))
ipcMain.handle('desktop:activate-runtime', (_event, version: unknown) => {
  if (typeof version !== 'string') throw new Error('Python 运行时版本号无效')
  const manifest = activatePythonRuntime(app.getPath('userData'), version)
  app.relaunch()
  stopWebServerTree()
  app.exit(0)
  return manifest
})
ipcMain.handle('desktop:update-runtime', async () => updatePythonRuntimeManually())
ipcMain.handle('desktop:account', async () => {
  if (!authRegion || !authToken) throw new Error('尚未登录')
  try {
    currentAccount = await fetchUserAccount(authRegion, authToken)
  } catch (error) {
    if (error instanceof AgenticsApiError && error.status === 401) {
      clearStoredAuth(app.getPath('userData'), authRegion.environment)
      authToken = ''
      currentAccount = undefined
      throw new Error('登录已过期，请重新登录')
    }
    if (!currentAccount) throw error
    console.warn(`[auth] account refresh failed: ${String(error)}`)
  }
  return currentAccount
})
ipcMain.handle('desktop:login', async () => {
  if (authToken) return true
  if (!authRegion) throw new Error('登录服务尚未初始化')
  let result: LoginResult
  try {
    result = await interactiveLogin(authRegion, '取消')
  } catch (error) {
    if (error instanceof LoginCancelledError) return false
    throw error
  }
  if (!result) return false
  const auth = {token: result.token, onboarded: false}
  initializeFirstLoginDefaults()
  auth.onboarded = true
  saveStoredAuth(app.getPath('userData'), auth, authRegion.environment)
  authToken = auth.token
  currentAccount = result.account
  app.relaunch()
  stopWebServerTree()
  app.exit(0)
  return true
})
ipcMain.handle('desktop:logout', async () => {
  if (!authRegion || !authToken) return false
  const options: MessageBoxOptions = {
    type: 'question', title: '退出 VideoAgents 登录',
    message: '确定退出当前登录？',
    detail: '退出后应用会重新启动，可重新登录或暂不登录继续使用。',
    buttons: ['退出登录', '取消'], defaultId: 1, cancelId: 1, noLink: true,
  }
  const answer = window && !window.isDestroyed()
    ? await dialog.showMessageBox(window, options)
    : await dialog.showMessageBox(options)
  if (answer.response !== 0) return false
  clearStoredAuth(app.getPath('userData'), authRegion.environment)
  authToken = ''
  authRegion = undefined
  currentAccount = undefined
  app.relaunch()
  stopWebServerTree()
  app.exit(0)
  return true
})
ipcMain.handle('desktop:pick-files', async (event, options: unknown) => {
  // 输入框「+」附件按钮:本机原生「选择文件」对话框,只回绝对路径,不上传;连远程后端时本机路径对 Agent 无意义,交页面转手填
  if (process.env.VIDEOAGENTS_API_URL) throw new Error('远程服务时不能选择本机文件')
  const title = options && typeof options === 'object' && typeof (options as {title?: unknown}).title === 'string'
    ? (options as {title: string}).title.slice(0, 120) : '选择文件'
  const anchor = BrowserWindow.fromWebContents(event.sender) ?? BrowserWindow.getFocusedWindow() ?? window
  const dialogOptions = {title, properties: ['openFile', 'multiSelections'] as Array<'openFile' | 'multiSelections'>}
  const result = anchor ? await dialog.showOpenDialog(anchor, dialogOptions) : await dialog.showOpenDialog(dialogOptions)
  return result.canceled ? [] : result.filePaths
})
ipcMain.handle('desktop:open-project-folder', async (_event, project: unknown) => {
  if (process.env.VIDEOAGENTS_API_URL) throw new Error('远程项目目录不能在本机打开')
  if (typeof project !== 'string' || !/^[A-Za-z0-9_-]+$/.test(project)) throw new Error('项目名称无效')
  const dataRoot = desktopDataRoot()
  const folder = path.join(dataRoot, 'projects', project)
  if (!existsSync(folder)) throw new Error(`项目目录不存在：${project}`)
  const problem = await shell.openPath(folder)
  if (problem) throw new Error(problem)
})

app.whenReady().then(async () => {
  if (!ownsSingleInstance) {
    app.quit()
    return
  }
  if (app.isPackaged) {
    app.setAsDefaultProtocolClient('videoagents')
  } else if (process.defaultApp && process.argv[1]) {
    app.setAsDefaultProtocolClient('videoagents', process.execPath, [path.resolve(process.argv[1])])
  }
  const build = readBuildInfo(process.resourcesPath, app.isPackaged)
  await requireDesktopLogin(build)
  const userData = app.getPath('userData')
  let desktopUpdate = readCachedRequiredDesktopUpdate(userData, build)
  if (app.isPackaged) {
    try {
      const fetchedUpdate = await fetchDesktopUpdate(build)
      desktopUpdate = fetchedUpdate
      clearCachedRequiredDesktopUpdate(userData)
      if (fetchedUpdate?.required) cacheRequiredDesktopUpdate(userData, fetchedUpdate)
    } catch (error) {
      console.warn(`[updater] update check skipped: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  if (desktopUpdate?.required) {
    await enforceDesktopUpdate(desktopUpdate, build.version)
  }
  installApplicationMenu()
  await createWindow()
  // Post-launch optional prompts run in sequence after the main page is visible.
  // Neither desktop-update prompting nor FFmpeg setup can block the main flow.
  void (async () => {
    if (desktopUpdate) {
      try {
        await offerDesktopUpdate(desktopUpdate, build.version)
      } catch (error) {
        console.warn(`[updater] update check skipped: ${error instanceof Error ? error.message : String(error)}`)
      }
    }
    await offerFfmpegEnvironmentSetup()
  })().catch(error => {
    console.warn(`[ffmpeg] optional environment check skipped: ${error instanceof Error ? error.message : String(error)}`)
  })
}).catch(error => {
  if (error instanceof LoginCancelledError) {
    app.quit()
    return
  }
  const message = error instanceof Error ? error.stack || error.message : String(error)
  console.error(message)
  dialog.showErrorBox('VideoAgents 启动失败', message)
  app.quit()
})
app.on('window-all-closed', () => {
  // Closing the first-launch runtime progress window briefly leaves no windows.
  // Keep the app alive until the actual main window has been created.
  if (mainWindowWasCreated) app.quit()
})
app.on('before-quit', event => {
  if (requiredDesktopUpdateActive && !requiredDesktopUpdateCanQuit) event.preventDefault()
  stopWebServerTree()
})
