import {spawn} from 'node:child_process'
import {existsSync} from 'node:fs'

const configured = process.env.VIDEOAGENTS_PYTHON
const venv = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'
const candidates = configured ? [configured] : [existsSync(venv) && venv, ...(process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'])].filter(Boolean)

function start(index) {
  if (index >= candidates.length) {
    console.error('Python 3 was not found. Activate .venv or set VIDEOAGENTS_PYTHON.')
    process.exit(1)
  }
  const child = spawn(candidates[index], ['apps/web/server.py'], {stdio: 'inherit', env: process.env})
  child.once('error', error => {
    if (error.code === 'ENOENT') start(index + 1)
    else throw error
  })
  child.once('exit', code => process.exit(code ?? 1))
}

start(0)
