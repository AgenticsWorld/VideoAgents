import {execFileSync} from 'node:child_process'
import {existsSync} from 'node:fs'
import {homedir, userInfo} from 'node:os'
import path from 'node:path'

const PATH_MARKER = '__VIDEOAGENTS_LOGIN_PATH__'

function loginShellPath(environment: NodeJS.ProcessEnv): string {
  if (process.platform !== 'darwin') return ''
  try {
    const shell = environment.SHELL || userInfo().shell || '/bin/zsh'
    if (!existsSync(shell)) return ''
    const output = execFileSync(
      shell,
      ['-ilc', `printf '\n${PATH_MARKER}%s\n' "$PATH"`],
      {
        encoding: 'utf8', env: environment, timeout: 8000,
        stdio: ['ignore', 'pipe', 'ignore'],
      },
    )
    const marker = output.lastIndexOf(PATH_MARKER)
    return marker === -1 ? '' : output.slice(marker + PATH_MARKER.length).split(/\r?\n/, 1)[0].trim()
  } catch (error) {
    console.warn(`[environment] 无法读取登录 Shell PATH：${String(error)}`)
    return ''
  }
}

function commonExecutableDirectories(environment: NodeJS.ProcessEnv): string[] {
  const home = environment.HOME || homedir()
  if (process.platform === 'win32') {
    return [
      environment.LOCALAPPDATA && path.join(environment.LOCALAPPDATA, 'Microsoft', 'WinGet', 'Links'),
      environment.LOCALAPPDATA && path.join(environment.LOCALAPPDATA, 'Microsoft', 'WindowsApps'),
      environment.ProgramFiles && path.join(environment.ProgramFiles, 'WinGet', 'Links'),
      environment.APPDATA && path.join(environment.APPDATA, 'npm'),
    ].filter(Boolean) as string[]
  }
  return [
    path.join(home, '.local', 'bin'),
    path.join(home, '.kimi-code', 'bin'),
    path.join(home, '.opencode', 'bin'),
    path.join(home, '.grok', 'bin'),
    path.join(home, '.volta', 'bin'),
    path.join(home, '.bun', 'bin'),
    path.join(home, 'Library', 'pnpm'),
    '/opt/homebrew/bin', '/opt/homebrew/sbin',
    '/usr/local/bin', '/usr/local/sbin',
  ]
}

export function desktopExecutablePath(environment: NodeJS.ProcessEnv = process.env): string {
  const values = [
    loginShellPath(environment),
    ...commonExecutableDirectories(environment),
    environment.PATH || '',
    process.platform === 'win32' ? '' : '/usr/bin:/bin:/usr/sbin:/sbin',
  ]
  const seen = new Set<string>()
  const directories: string[] = []
  for (const value of values) {
    for (const directory of value.split(path.delimiter)) {
      const normalized = directory.trim()
      if (normalized && !seen.has(normalized)) {
        seen.add(normalized)
        directories.push(normalized)
      }
    }
  }
  return directories.join(path.delimiter)
}
