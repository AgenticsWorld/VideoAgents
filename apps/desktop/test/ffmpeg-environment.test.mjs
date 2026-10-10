import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync} from 'node:fs'
import http from 'node:http'
import {tmpdir} from 'node:os'
import path from 'node:path'
import test from 'node:test'
import zlib from 'node:zlib'
import {
  FFMPEG_DOWNLOAD_URL, FfmpegInstallerMissingError, ffmpegInstallCommand, installManagedFfmpeg,
  managedFfmpegBinDirectory,
} from '../dist/ffmpeg-environment.js'
import {fetchFfmpegArtifact} from '../dist/runtime-download.js'
import {desktopExecutablePath} from '../dist/shell-environment.js'

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

test('missing WinGet is a typed error that points at the official FFmpeg download', () => {
  assert.throws(
    () => ffmpegInstallCommand('win32', 'x64', {PATH: ''}),
    error => {
      assert.ok(error instanceof FfmpegInstallerMissingError)
      assert.equal(error.manager, 'winget')
      assert.equal(error.downloadUrl, FFMPEG_DOWNLOAD_URL)
      assert.match(error.message, /未找到 WinGet.*AI 自动剪辑需要.*不会影响 VideoAgents 的其他功能/s)
      assert.ok(error.message.includes('https://ffmpeg.org/download.html'))
      return true
    },
  )
})

/** Stored (uncompressed) ZIP, enough for the extractor: [[name, content], ...]. */
function zipArchive(files) {
  const locals = []
  const central = []
  let offset = 0
  for (const [name, content] of files) {
    const nameBytes = Buffer.from(name, 'utf8')
    const data = Buffer.from(content)
    const crc = zlib.crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(data.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    const header = Buffer.alloc(46)
    header.writeUInt32LE(0x02014b50, 0)
    header.writeUInt16LE(20, 4)
    header.writeUInt16LE(20, 6)
    header.writeUInt32LE(crc, 16)
    header.writeUInt32LE(data.length, 20)
    header.writeUInt32LE(data.length, 24)
    header.writeUInt16LE(nameBytes.length, 28)
    header.writeUInt32LE(offset, 42)
    locals.push(local, nameBytes, data)
    central.push(header, nameBytes)
    offset += local.length + nameBytes.length + data.length
  }
  const directory = Buffer.concat(central)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(files.length, 8)
  end.writeUInt16LE(files.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, directory, end])
}

/** Serves an index and packages the way a distribution mirror does. */
async function withMirror(routes, run) {
  const server = http.createServer((request, response) => {
    const body = routes[request.url]
    if (body === undefined) {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, {'content-length': body.length}).end(body)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  const previous = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
  process.env.VIDEOAGENTS_RUNTIME_INDEX_URL = `${origin}/metadata.json`
  const directory = mkdtempSync(path.join(tmpdir(), 'videoagents-ffmpeg-test-'))
  try {
    await run(origin, directory)
  } finally {
    if (previous === undefined) delete process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
    else process.env.VIDEOAGENTS_RUNTIME_INDEX_URL = previous
    rmSync(directory, {recursive: true, force: true})
    await new Promise(resolve => server.close(resolve))
  }
}

const FFMPEG_PACKAGE = zipArchive([
  ['ffmpeg-9.9-essentials_build/LICENSE', 'GPLv3'],
  ['ffmpeg-9.9-essentials_build/bin/ffmpeg.exe', 'ffmpeg binary'],
  ['ffmpeg-9.9-essentials_build/bin/ffprobe.exe', 'ffprobe binary'],
  ['ffmpeg-9.9-essentials_build/bin/ffplay.exe', 'ffplay binary'],
])

function mirrorIndex(origin, artifact) {
  return Buffer.from(JSON.stringify({
    schema: 1,
    python: {},
    ...(artifact ? {ffmpeg: {win: {x64: {
      version: '9.9-essentials',
      url: `${origin}/ffmpeg/ffmpeg-9.9-essentials_build.zip`,
      sha256: createHash('sha256').update(FFMPEG_PACKAGE).digest('hex'),
      size: FFMPEG_PACKAGE.length,
      ...artifact,
    }}}} : {}),
  }))
}

test('the managed FFmpeg directory leads the desktop PATH', () => {
  const managed = managedFfmpegBinDirectory(path.join('user-data'))
  assert.equal(managed, path.join('user-data', 'ffmpeg', 'current', 'bin'))
  const searchPath = desktopExecutablePath({PATH: ['/system/bin', managed].join(path.delimiter)}, [managed])
  const directories = searchPath.split(path.delimiter)
  assert.equal(directories[0], managed)
  assert.equal(directories.filter(directory => directory === managed).length, 1)
  assert.ok(directories.includes('/system/bin'))
})

test('Windows x64 downloads FFmpeg from the distribution mirror without a package manager', async () => {
  let routes
  await withMirror(routes = {}, async (origin, userData) => {
    routes['/metadata.json'] = mirrorIndex(origin, {})
    routes['/ffmpeg/ffmpeg-9.9-essentials_build.zip'] = FFMPEG_PACKAGE
    const progress = []
    assert.equal(await installManagedFfmpeg(userData, update => progress.push(update), 'win32', 'x64'), true)
    const bin = managedFfmpegBinDirectory(userData)
    assert.equal(readFileSync(path.join(bin, 'ffmpeg.exe'), 'utf8'), 'ffmpeg binary')
    assert.equal(readFileSync(path.join(bin, 'ffprobe.exe'), 'utf8'), 'ffprobe binary')
    assert.equal(existsSync(path.join(bin, 'ffplay.exe')), false)
    assert.equal(readFileSync(path.join(bin, '..', 'LICENSE'), 'utf8'), 'GPLv3')
    // Nothing but the installed tree is left behind.
    assert.deepEqual(readdirSync(path.join(userData, 'ffmpeg')), ['current'])
    const last = progress.filter(update => update.total !== undefined).at(-1)
    assert.equal(last.received, FFMPEG_PACKAGE.length)
    assert.equal(last.total, FFMPEG_PACKAGE.length)

    // A second install replaces the directory instead of failing on it.
    assert.equal(await installManagedFfmpeg(userData, undefined, 'win32', 'x64'), true)
    assert.deepEqual(readdirSync(path.join(userData, 'ffmpeg')), ['current'])
  })
})

test('a mirror index without an FFmpeg package for the platform is reported, not treated as a failure', async () => {
  let routes
  await withMirror(routes = {}, async (origin, userData) => {
    routes['/metadata.json'] = mirrorIndex(origin)
    assert.equal(await installManagedFfmpeg(userData, undefined, 'win32', 'x64'), false)
    routes['/metadata.json'] = mirrorIndex(origin, {})
    assert.equal(await installManagedFfmpeg(userData, undefined, 'win32', 'arm64'), false)
    assert.equal(await installManagedFfmpeg(userData, undefined, 'darwin', 'arm64'), false)
    assert.equal(existsSync(managedFfmpegBinDirectory(userData)), false)
  })
})

test('a package that does not match the index checksum is not installed', async () => {
  let routes
  await withMirror(routes = {}, async (origin, userData) => {
    routes['/metadata.json'] = mirrorIndex(origin, {sha256: 'a'.repeat(64)})
    routes['/ffmpeg/ffmpeg-9.9-essentials_build.zip'] = FFMPEG_PACKAGE
    await assert.rejects(
      installManagedFfmpeg(userData, undefined, 'win32', 'x64'),
      /FFmpeg 安装包 SHA-256 校验失败/,
    )
    assert.equal(existsSync(managedFfmpegBinDirectory(userData)), false)
    assert.deepEqual(readdirSync(path.join(userData, 'ffmpeg')), [])
  })
})

test('an FFmpeg package hosted outside the index origin is refused', async () => {
  let routes
  await withMirror(routes = {}, async origin => {
    routes['/metadata.json'] = mirrorIndex(origin, {url: 'https://example.com/ffmpeg/ffmpeg.zip'})
    await assert.rejects(fetchFfmpegArtifact('win32', 'x64'), /运行时制品与索引来源不一致/)
  })
})

test('each distribution build only takes FFmpeg from its own mirror', async () => {
  // Two mirrors stand in for S3 (agentics.world build) and OSS (shumati.cn build).
  const previousIndex = process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
  const previousResources = process.resourcesPath
  const resources = mkdtempSync(path.join(tmpdir(), 'videoagents-build-info-test-'))
  const builtFor = origin => writeFileSync(path.join(resources, 'build-info.json'), JSON.stringify({
    updateIndexUrl: `${origin}/packages/metadata.json`, packageBaseUrl: `${origin}/packages`,
  }))
  const packagedIndex = (origin, url) => mirrorIndex(origin, {url})
  let s3
  let oss
  try {
    await withMirror(s3 = {}, async s3Origin => {
      await withMirror(oss = {}, async ossOrigin => {
        delete process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
        process.resourcesPath = resources
        const s3Package = `${s3Origin}/packages/ffmpeg/ffmpeg-9.9-essentials_build.zip`
        const ossPackage = `${ossOrigin}/packages/ffmpeg/ffmpeg-9.9-essentials_build.zip`
        s3['/packages/metadata.json'] = packagedIndex(s3Origin, s3Package)
        oss['/packages/metadata.json'] = packagedIndex(ossOrigin, ossPackage)

        builtFor(s3Origin)
        assert.equal((await fetchFfmpegArtifact('win32', 'x64')).url, s3Package)
        builtFor(ossOrigin)
        assert.equal((await fetchFfmpegArtifact('win32', 'x64')).url, ossPackage)

        // An index that points at the other mirror, or outside ffmpeg/, is refused.
        oss['/packages/metadata.json'] = packagedIndex(ossOrigin, s3Package)
        await assert.rejects(fetchFfmpegArtifact('win32', 'x64'), /不属于当前发布源的受信任路径/)
        oss['/packages/metadata.json'] = packagedIndex(ossOrigin, `${ossOrigin}/packages/python/ffmpeg.zip`)
        await assert.rejects(fetchFfmpegArtifact('win32', 'x64'), /不属于当前发布源的受信任路径/)
      })
    })
  } finally {
    if (previousIndex === undefined) delete process.env.VIDEOAGENTS_RUNTIME_INDEX_URL
    else process.env.VIDEOAGENTS_RUNTIME_INDEX_URL = previousIndex
    process.resourcesPath = previousResources
    rmSync(resources, {recursive: true, force: true})
  }
})
