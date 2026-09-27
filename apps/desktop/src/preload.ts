import {contextBridge, ipcRenderer} from 'electron'

contextBridge.exposeInMainWorld('videoagentsDesktop', {
  platform: process.platform,
  version: ipcRenderer.sendSync('desktop:version') as string,
  distribution: ipcRenderer.sendSync('desktop:distribution') as 's3' | 'oss',
  remoteBackend: Boolean(process.env.VIDEOAGENTS_API_URL),
  openExternal: (url: string) => ipcRenderer.invoke('desktop:open-external', url),
  openProjectFolder: (project: string) => ipcRenderer.invoke('desktop:open-project-folder', project),
  // 输入框「+」附件按钮:系统「选择文件」对话框,只返回绝对路径(文件不上传,Agent 按路径自行读取)
  pickFiles: (options?: {title?: string}) => ipcRenderer.invoke('desktop:pick-files', options) as Promise<string[]>,
  restartBackend: () => ipcRenderer.invoke('desktop:restart-backend'),
  runtimeInfo: () => ipcRenderer.invoke('desktop:runtime-info'),
  activateRuntime: (version: string) => ipcRenderer.invoke('desktop:activate-runtime', version),
  updateRuntime: () => ipcRenderer.invoke('desktop:update-runtime'),
  account: () => ipcRenderer.invoke('desktop:account'),
  login: () => ipcRenderer.invoke('desktop:login'),
  logout: () => ipcRenderer.invoke('desktop:logout')
})
