import {app, BrowserWindow, dialog, ipcMain, Menu, MenuItemConstructorOptions, shell} from 'electron'
import type {MessageBoxOptions} from 'electron'
import {autoUpdater} from 'electron-updater'
import {ChildProcess, spawn} from 'node:child_process'
import {existsSync, mkdirSync} from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import path from 'node:path'
import {activatePythonRuntime, PythonRuntime, resolvePythonRuntime, runtimeStore} from './runtime'
import {installLatestPythonRuntime, RuntimeProgress} from './runtime-download'
import {
  downloadAndApplyDesktopUpdate, fetchDevDesktopUpdate, readBuildInfo,
} from './desktop-update'

let webServer: ChildProcess | undefined
const webPort = process.env.VIDEOAGENTS_WEB_PORT || '8630'
const webOrigin = process.env.VIDEOAGENTS_WEB_URL?.replace(/\/$/, '') || `http://127.0.0.1:${webPort}`
let window: BrowserWindow | undefined
let activeRuntime: PythonRuntime | undefined
let runtimeProgressWindow: BrowserWindow | undefined
let runtimeUpdateInProgress = false

function webRoot(): string {
  const packaged = path.join(process.resourcesPath, 'backend', 'apps', 'web')
  const development = path.resolve(__dirname, '../../web')
  return app.isPackaged ? packaged : development
}

function backendRoot(): string {
  return app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(webRoot(), '../..')
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
): Promise<void> {
  if (runtimeProgressWindow && !runtimeProgressWindow.isDestroyed()) return
  runtimeProgressWindow = new BrowserWindow({
    width: 520, height: 230, resizable: false, minimizable: false, maximizable: false,
    closable: false, title: 'VideoAgents Python 环境', backgroundColor: '#111318',
    webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true},
  })
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;background:#111318;color:#f3f4f6;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{padding:34px}h2{font-size:18px;margin:0 0 18px}p{color:#b8bec9;height:22px;margin:0 0 14px}
    progress{width:100%;height:12px;accent-color:#5677ff}small{display:block;color:#727987;margin-top:12px}
  </style><main><h2>${title}</h2><p id="message">正在检查更新…</p>
  <progress id="progress"></progress><small>${note}</small></main>`
  await runtimeProgressWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
}

function updateRuntimeProgress(progress: RuntimeProgress): void {
  const target = runtimeProgressWindow
  if (!target || target.isDestroyed()) return
  const percent = progress.total && progress.received !== undefined
    ? Math.min(100, Math.round(progress.received / progress.total * 100)) : undefined
  void target.webContents.executeJavaScript(`(() => {
    document.getElementById('message').textContent = ${JSON.stringify(progress.message)};
    const bar = document.getElementById('progress');
    ${percent === undefined ? "bar.removeAttribute('value')" : `bar.value = ${percent}`};
  })()`)
}

function closeRuntimeProgress(): void {
  if (runtimeProgressWindow && !runtimeProgressWindow.isDestroyed()) runtimeProgressWindow.destroy()
  runtimeProgressWindow = undefined
}

async function ensurePythonRuntime(backend: string): Promise<PythonRuntime> {
  try {
    return resolvePythonRuntime({
      packaged: app.isPackaged,
      userData: app.getPath('userData'),
      developmentRoot: backend,
    })
  } catch (error) {
    if (!app.isPackaged || process.env.VIDEOAGENTS_PYTHON) throw error
  }
  await showRuntimeProgress()
  try {
    return (await installLatestPythonRuntime(app.getPath('userData'), updateRuntimeProgress)).runtime
  } finally {
    closeRuntimeProgress()
  }
}

async function ensureWebServer(): Promise<void> {
  if (await healthy()) return
  if (process.env.VIDEOAGENTS_WEB_URL) throw new Error(`Web 服务不可用：${webOrigin}`)

  const dataRoot = process.env.VIDEOAGENTS_DATA_DIR || path.join(app.getPath('userData'), 'data')
  mkdirSync(path.join(dataRoot, 'projects'), {recursive: true})
  const root = webRoot()
  const backend = backendRoot()
  activeRuntime = await ensurePythonRuntime(backend)
  console.log(`[runtime] ${activeRuntime.source}: ${activeRuntime.manifest?.version || activeRuntime.python}`)
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONNOUSERSITE: '1',
    VIDEOAGENTS_APP_ROOT: backend,
    VIDEOAGENTS_DATA_DIR: dataRoot,
    VIDEOAGENTS_WEB_HOST: '127.0.0.1',
    VIDEOAGENTS_WEB_PORT: webPort,
    VIDEOAGENTS_API_PORT: process.env.VIDEOAGENTS_API_PORT || '8640',
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

async function checkDevDesktopUpdate(): Promise<boolean> {
  const build = readBuildInfo(process.resourcesPath, app.isPackaged)
  if (build.channel !== 'dev') return false
  const artifact = await fetchDevDesktopUpdate(build)
  if (!artifact) return true
  const options: MessageBoxOptions = {
    type: 'info', title: '发现 VideoAgents Dev 更新',
    message: `发现 VideoAgents ${artifact.version} 的新 Dev 构建。`,
    detail: `最新构建 ${artifact.buildHash}，当前构建 ${build.buildHash}。是否立即下载并自动安装？`,
    buttons: ['下载并更新', '暂不更新'], defaultId: 0, cancelId: 1,
  }
  const answer = window
    ? await dialog.showMessageBox(window, options)
    : await dialog.showMessageBox(options)
  if (answer.response !== 0) return true
  await showRuntimeProgress('正在更新 VideoAgents', '下载完成后应用会自动安装并重新启动。')
  try {
    const currentAppPath = path.resolve(process.resourcesPath, '..', '..')
    await downloadAndApplyDesktopUpdate(
      app.getPath('userData'), currentAppPath, artifact, updateRuntimeProgress,
    )
    closeRuntimeProgress()
    app.quit()
  } catch (error) {
    closeRuntimeProgress()
    const message = error instanceof Error ? error.message : String(error)
    dialog.showErrorBox('VideoAgents 更新失败', message)
  }
  return true
}

ipcMain.on('desktop:version', event => {event.returnValue = app.getVersion()})
ipcMain.handle('desktop:open-external', async (_event, value: unknown) => {
  if (typeof value !== 'string' || !/^https?:\/\//.test(value)) throw new Error('不允许的 URL')
  await shell.openExternal(value)
})
ipcMain.handle('desktop:restart-backend', async () => {
  webServer?.kill(); webServer=undefined; await ensureWebServer(); window?.reload()
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
  installApplicationMenu()
  await createWindow()
  if (app.isPackaged) {
    void checkDevDesktopUpdate().then(isDev => {
      if (!isDev) {
        void autoUpdater.checkForUpdatesAndNotify().catch(error => {
          console.warn(`[updater] update check skipped: ${error instanceof Error ? error.message : String(error)}`)
        })
      }
    }).catch(error => {
      console.warn(`[dev-updater] update check skipped: ${error instanceof Error ? error.message : String(error)}`)
    })
  }
}).catch(error => {
  const message = error instanceof Error ? error.stack || error.message : String(error)
  console.error(message)
  dialog.showErrorBox('VideoAgents 启动失败', message)
  app.quit()
})
app.on('window-all-closed', () => {if(process.platform!=='darwin')app.quit()})
app.on('before-quit', () => webServer?.kill())
