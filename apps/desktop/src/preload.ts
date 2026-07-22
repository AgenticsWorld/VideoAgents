import {contextBridge, ipcRenderer} from 'electron'

contextBridge.exposeInMainWorld('videoagentsDesktop', {
  platform: process.platform,
  version: ipcRenderer.sendSync('desktop:version') as string,
  remoteBackend: Boolean(process.env.VIDEOAGENTS_API_URL),
  openExternal: (url: string) => ipcRenderer.invoke('desktop:open-external', url),
  openProjectFolder: (project: string) => ipcRenderer.invoke('desktop:open-project-folder', project),
  restartBackend: () => ipcRenderer.invoke('desktop:restart-backend'),
  runtimeInfo: () => ipcRenderer.invoke('desktop:runtime-info'),
  activateRuntime: (version: string) => ipcRenderer.invoke('desktop:activate-runtime', version),
  updateRuntime: () => ipcRenderer.invoke('desktop:update-runtime')
})
