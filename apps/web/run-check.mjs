import {spawnSync} from 'node:child_process'
import {existsSync} from 'node:fs'

const configured = process.env.VIDEOAGENTS_PYTHON
const venv = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'
const candidates = configured ? [configured] : [existsSync(venv) && venv, ...(process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'])].filter(Boolean)
for (const executable of candidates) {
  const result = spawnSync(executable, ['apps/web/check.py'], {stdio: 'inherit'})
  if (!result.error) process.exit(result.status ?? 1)
}
console.error('Python 3 was not found. Set VIDEOAGENTS_PYTHON to its executable path.')
process.exit(1)
