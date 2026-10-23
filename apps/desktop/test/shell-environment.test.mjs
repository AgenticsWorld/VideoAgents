import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import path from 'node:path'
import test from 'node:test'
import {desktopExecutablePath} from '../dist/shell-environment.js'

test('selected Python precedes system paths while managed FFmpeg stays first', () => {
  const bin = path.join(tmpdir(), 'VideoAgents Runtime', 'bin')
  const managed = path.join(tmpdir(), 'managed-ffmpeg')
  const directories = desktopExecutablePath({
    SHELL: '/nonexistent-videoagents-shell',
    PATH: ['/usr/local/bin', bin, '/custom/tools'].join(path.delimiter),
  }, [managed, bin]).split(path.delimiter)
  assert.equal(directories[0], managed)
  assert.equal(directories[1], bin)
  assert.equal(directories.filter(directory => directory === bin).length, 1)
  assert.ok(directories.includes('/custom/tools'))
})

test('python3 resolves to the selected runtime despite a conflicting login shell', {
  skip: process.platform !== 'darwin',
}, () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'videoagents-python-'))
  try {
    const appBin = path.join(directory, 'app runtime', 'bin')
    const globalBin = path.join(directory, 'global', 'bin')
    for (const [bin, label] of [[appBin, 'application'], [globalBin, 'global']]) {
      mkdirSync(bin, {recursive: true})
      writeFileSync(path.join(bin, 'python3'), `#!/bin/sh\nprintf '${label}\\n'\n`, {mode: 0o755})
    }
    const shell = path.join(directory, 'login-shell')
    writeFileSync(shell, `#!/bin/sh\nprintf '__VIDEOAGENTS_LOGIN_PATH__%s\\n' '${globalBin}'\n`, {mode: 0o755})
    const env = {
      SHELL: shell, PATH: globalBin,
    }
    const result = spawnSync('/bin/sh', ['-c', 'python3'], {
      env: {...env, PATH: desktopExecutablePath(env, [appBin])}, encoding: 'utf8', timeout: 10000,
    })
    assert.equal(result.status, 0, result.stderr)
    assert.equal(result.stdout.trim(), 'application')
  } finally {
    rmSync(directory, {recursive: true, force: true})
  }
})
