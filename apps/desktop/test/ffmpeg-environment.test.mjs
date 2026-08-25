import assert from 'node:assert/strict'
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import path from 'node:path'
import test from 'node:test'
import {ffmpegInstallCommand} from '../dist/ffmpeg-environment.js'

function withTempDirectory(run) {
  const directory = mkdtempSync(path.join(tmpdir(), 'videoagents-ffmpeg-test-'))
  try {
    run(directory)
  } finally {
    rmSync(directory, {recursive: true, force: true})
  }
}

test('Apple Silicon macOS installs FFmpeg through Homebrew', () => {
  withTempDirectory(directory => {
    const brew = path.join(directory, 'brew')
    writeFileSync(brew, '')
    const command = ffmpegInstallCommand('darwin', 'arm64', {PATH: directory})
    assert.deepEqual(command, {
      executable: brew,
      args: ['install', 'ffmpeg'],
      manager: 'homebrew',
    })
  })
})

test('Windows x64 installs the exact Gyan.FFmpeg package through WinGet', () => {
  withTempDirectory(directory => {
    const windowsApps = path.join(directory, 'Microsoft', 'WindowsApps')
    mkdirSync(windowsApps, {recursive: true})
    const winget = path.join(windowsApps, 'winget.exe')
    writeFileSync(winget, '')
    const command = ffmpegInstallCommand('win32', 'x64', {
      PATH: '', LOCALAPPDATA: directory,
    })
    assert.equal(command.executable, winget)
    assert.equal(command.manager, 'winget')
    assert.deepEqual(command.args, [
      'install', '--id', 'Gyan.FFmpeg', '--exact', '--source', 'winget', '--silent',
      '--disable-interactivity', '--accept-package-agreements', '--accept-source-agreements',
    ])
  })
})

test('unsupported architectures fail before invoking a package manager', () => {
  assert.throws(
    () => ffmpegInstallCommand('darwin', 'x64', {}),
    /macOS.*x64/,
  )
  assert.throws(
    () => ffmpegInstallCommand('win32', 'arm64', {}),
    /Windows.*arm64.*x64/,
  )
})

test('missing package managers produce actionable setup errors', () => {
  assert.throws(
    () => ffmpegInstallCommand('win32', 'x64', {PATH: ''}),
    /WinGet.*Microsoft Store/,
  )
})
