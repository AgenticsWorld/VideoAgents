import assert from 'node:assert/strict'
import {existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import {savePendingDesktopUpdate, takePendingDesktopUpdate} from '../dist/desktop-update.js'

const BUILD = {schema: 1, channel: 'release', version: '1.0.44', buildHash: 'current'}

/** 在临时 userData 下造一个与平台对应的已解压更新包(macOS 为 .app,Windows 为安装程序)。 */
function stagedUpdate(userData, buildHash = 'next') {
  const staging = path.join(userData, 'app-updates', `staging-${buildHash}`)
  let payload
  if (process.platform === 'darwin') {
    payload = path.join(staging, 'VideoAgents.app')
    mkdirSync(path.join(payload, 'Contents'), {recursive: true})
    writeFileSync(path.join(payload, 'Contents', 'Info.plist'), '<plist/>')
  } else {
    mkdirSync(staging, {recursive: true})
    payload = path.join(staging, 'VideoAgents-Setup.exe')
    writeFileSync(payload, 'installer')
  }
  return {staging, payload}
}

function withUserData(run) {
  const userData = mkdtempSync(path.join(os.tmpdir(), 'videoagents-update-'))
  try {
    run(userData)
  } finally {
    rmSync(userData, {recursive: true, force: true})
  }
}

const marker = userData => path.join(userData, 'app-updates', 'pending-update.json')

test('a pending update downloaded last session is returned once, then forgotten', () => {
  withUserData(userData => {
    const {staging, payload} = stagedUpdate(userData)
    savePendingDesktopUpdate(userData, {version: '1.0.45', buildHash: 'next', staging, payload})
    assert.ok(existsSync(marker(userData)))
    assert.deepEqual(takePendingDesktopUpdate(userData, BUILD), {
      version: '1.0.45', buildHash: 'next', staging, payload,
    })
    assert.equal(existsSync(marker(userData)), false)
    assert.equal(takePendingDesktopUpdate(userData, BUILD), undefined)
    assert.ok(existsSync(payload), 'the staged package stays until the installer has used it')
  })
})

test('a pending update that is not newer than the running app is discarded with its files', () => {
  withUserData(userData => {
    const {staging, payload} = stagedUpdate(userData)
    savePendingDesktopUpdate(userData, {version: '1.0.44', buildHash: 'next', staging, payload})
    assert.equal(takePendingDesktopUpdate(userData, BUILD), undefined)
    assert.equal(existsSync(marker(userData)), false)
    assert.equal(existsSync(staging), false)
  })
})

test('dev builds install a pending update when its build hash differs', () => {
  withUserData(userData => {
    const dev = {schema: 1, channel: 'dev', version: '1.0.44', buildHash: 'current'}
    const {staging, payload} = stagedUpdate(userData)
    savePendingDesktopUpdate(userData, {version: '1.0.44', buildHash: 'next', staging, payload})
    assert.equal(takePendingDesktopUpdate(userData, dev)?.buildHash, 'next')
    savePendingDesktopUpdate(userData, {version: '1.0.44', buildHash: 'current', staging, payload})
    assert.equal(takePendingDesktopUpdate(userData, dev), undefined)
  })
})

test('a pending update whose package is missing or outside the update folder is ignored', () => {
  withUserData(userData => {
    const {staging, payload} = stagedUpdate(userData)
    rmSync(payload, {recursive: true, force: true})
    savePendingDesktopUpdate(userData, {version: '1.0.45', buildHash: 'next', staging, payload})
    assert.equal(takePendingDesktopUpdate(userData, BUILD), undefined)

    const outside = mkdtempSync(path.join(os.tmpdir(), 'videoagents-outside-'))
    try {
      savePendingDesktopUpdate(userData, {
        version: '1.0.45', buildHash: 'next', staging: outside, payload: path.join(outside, 'VideoAgents.app'),
      })
      assert.equal(takePendingDesktopUpdate(userData, BUILD), undefined)
      assert.ok(existsSync(outside), 'folders outside app-updates are never deleted')
    } finally {
      rmSync(outside, {recursive: true, force: true})
    }
  })
})

test('a corrupt pending-update record is removed instead of blocking startup', () => {
  withUserData(userData => {
    mkdirSync(path.dirname(marker(userData)), {recursive: true})
    writeFileSync(marker(userData), '{not json')
    assert.equal(takePendingDesktopUpdate(userData, BUILD), undefined)
    assert.equal(existsSync(marker(userData)), false)
  })
})
