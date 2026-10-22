// 内置 Agentics 引擎的 pi 启动入口(services/runtime/core.py agentics_pi_command)。
// 桌面端没有独立 Node,用 Electron 自带的 Node 跑(ELECTRON_RUN_AS_NODE=1):进到 Node 后
// 立即去掉该变量,免得 pi 的 bash 工具派生的子进程里若有 Electron 应用也被当成 Node 运行。
// pi 不会用 process.execPath 再拉起自身(1.3.0 核对),去掉是安全的。
delete process.env.ELECTRON_RUN_AS_NODE
await import('./node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js')
