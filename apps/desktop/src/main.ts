import {app, BrowserWindow, dialog, ipcMain, Menu, MenuItemConstructorOptions, shell} from 'electron'
import type {MessageBoxOptions} from 'electron'
import {ChildProcess, spawn, spawnSync} from 'node:child_process'
import {existsSync, mkdirSync} from 'node:fs'
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
import {desktopExecutablePath} from './shell-environment'

let webServer: ChildProcess | undefined
let webPort = process.env.VIDEOAGENTS_WEB_PORT || ''
let apiPort = process.env.VIDEOAGENTS_API_PORT || ''
let webOrigin = process.env.VIDEOAGENTS_WEB_URL?.replace(/\/$/, '') || ''
let window: BrowserWindow | undefined
let mainWindowWasCreated = false
let activeRuntime: PythonRuntime | undefined

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

  const dataRoot = process.env.VIDEOAGENTS_DATA_DIR || path.join(app.getPath('userData'), 'data')
  mkdirSync(path.join(dataRoot, 'projects'), {recursive: true})
  // macOS 会校验整个已签名的 .app。Python 若在 Resources/backend 写入
  // __pycache__，会使应用在下次启动时因签名失效而被 Gatekeeper 拒绝。
  const pythonCacheRoot = path.join(dataRoot, 'python-cache')
  mkdirSync(pythonCacheRoot, {recursive: true})
  const root = webRoot()
  const backend = backendRoot()
  activeRuntime = await ensurePythonRuntime(backend)
  console.log(`[runtime] ${activeRuntime.source}: ${activeRuntime.manifest?.version || activeRuntime.python}`)
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PATH: desktopExecutablePath(),
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

async function createWindow(): Promise<void> {
  await ensureWebServer()
  window = new BrowserWindow({
    width: 1440, height: 920, minWidth: 980, minHeight: 680,
    backgroundColor: '#0c0d11', title: 'VideoAgents',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false, contextIsolation: true, sandbox: true
    }
  })
  mainWindowWasCreated = true
  window.webContents.setWindowOpenHandler(({url}) => {
    if (url.startsWith('https://')) void shell.openExternal(url)
    return {action:'deny'}
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(webOrigin)) event.preventDefault()
  })
  await window.loadURL(webOrigin)
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
    {label: '窗口', submenu: [{role: 'reload'}, {role: 'toggleDevTools'}, {type: 'separator'},
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
ipcMain.handle('desktop:open-external', async (_event, value: unknown) => {
  if (typeof value !== 'string' || !/^https?:\/\//.test(value)) throw new Error('不允许的 URL')
  await shell.openExternal(value)
})
ipcMain.handle('desktop:restart-backend', async () => {
  stopWebServerTree(); await ensureWebServer(); window?.reload()
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
ipcMain.handle('desktop:open-project-folder', async (_event, project: unknown) => {
  if (process.env.VIDEOAGENTS_API_URL) throw new Error('远程项目目录不能在本机打开')
  if (typeof project !== 'string' || !/^[A-Za-z0-9_-]+$/.test(project)) throw new Error('项目名称无效')
  const dataRoot = process.env.VIDEOAGENTS_DATA_DIR || path.join(app.getPath('userData'), 'data')
  const folder = path.join(dataRoot, 'projects', project)
  if (!existsSync(folder)) throw new Error(`项目目录不存在：${project}`)
  const problem = await shell.openPath(folder)
  if (problem) throw new Error(problem)
})

app.whenReady().then(async () => {
  const build = readBuildInfo(process.resourcesPath, app.isPackaged)
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
  if (desktopUpdate) {
    void offerDesktopUpdate(desktopUpdate, build.version).catch(error => {
      console.warn(`[updater] update check skipped: ${error instanceof Error ? error.message : String(error)}`)
    })
  }
}).catch(error => {
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
