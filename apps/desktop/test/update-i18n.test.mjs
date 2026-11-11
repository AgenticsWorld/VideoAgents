import assert from 'node:assert/strict'
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import {
  desktopUiLanguage, fillTemplate, UPDATE_LANGUAGES, UPDATE_STRINGS, updateStrings,
} from '../dist/update-i18n.js'

function withRuntimeDir(files, run) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'videoagents-lang-'))
  try {
    for (const [name, value] of Object.entries(files)) {
      writeFileSync(path.join(dir, name), typeof value === 'string' ? value : JSON.stringify(value))
    }
    run(dir)
  } finally {
    rmSync(dir, {recursive: true, force: true})
  }
}

test('update dialogs follow the language saved by the web UI', () => {
  withRuntimeDir({'state.json': {ui_lang: 'ja'}, 'genconfig.json': {ui_language: 'zh'}}, dir => {
    assert.equal(desktopUiLanguage(dir), 'ja')
  })
  withRuntimeDir({'state.json': {ui_lang: ''}, 'genconfig.json': {ui_language: 'zh'}}, dir => {
    assert.equal(desktopUiLanguage(dir), 'zh', 'older installs only saved the language in genconfig.json')
  })
  withRuntimeDir({'state.json': {ui_lang: 'pt-BR'}}, dir => {
    assert.equal(desktopUiLanguage(dir), 'pt')
  })
})

test('update dialogs default to English when no supported language is saved', () => {
  withRuntimeDir({}, dir => assert.equal(desktopUiLanguage(dir), 'en'))
  withRuntimeDir({'state.json': {ui_lang: 'xx'}}, dir => assert.equal(desktopUiLanguage(dir), 'en'))
  withRuntimeDir({'state.json': '{broken'}, dir => assert.equal(desktopUiLanguage(dir), 'en'))
})

test('every web UI language has every update string with the same placeholders', () => {
  assert.deepEqual([...UPDATE_LANGUAGES].sort(),
    ['ar', 'de', 'en', 'es', 'fr', 'id', 'ja', 'ko', 'pt', 'ru', 'vi', 'zh'])
  const placeholders = value => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
  const english = UPDATE_STRINGS.en
  for (const language of UPDATE_LANGUAGES) {
    const strings = UPDATE_STRINGS[language]
    assert.deepEqual(Object.keys(strings).sort(), Object.keys(english).sort(), language)
    for (const [key, value] of Object.entries(strings)) {
      assert.ok(value.trim(), `${language}.${key} is empty`)
      assert.deepEqual(placeholders(value), placeholders(english[key]), `${language}.${key} placeholders`)
    }
    const buttons = [strings.installNow, strings.installNextLaunch, strings.notNow]
    assert.equal(new Set(buttons).size, 3, `${language} buttons must differ`)
  }
})

test('templates are filled with the versions', () => {
  const en = updateStrings('en')
  assert.equal(fillTemplate(en.availableMessage, {version: '1.0.45'}), 'VideoAgents 1.0.45 is available.')
  assert.match(fillTemplate(en.requiredDetail, {current: '1.0.40', minimum: '1.0.42', version: '1.0.45'}),
    /1\.0\.40 .* 1\.0\.42\. Update to 1\.0\.45/)
})
