import {spawnSync} from 'node:child_process'
import {createRequire} from 'node:module'

const require = createRequire(import.meta.url)
const builder = require.resolve('electron-builder/cli.js')
const args = [builder, '--publish', 'never', ...process.argv.slice(2)]
const env = {
  ...process.env,
  // Local packages must never silently use a certificate from the developer's Keychain.
  CSC_IDENTITY_AUTO_DISCOVERY: 'false',
}
for (const name of ['CSC_LINK', 'CSC_KEY_PASSWORD', 'CSC_NAME', 'APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID']) {
  delete env[name]
}
const result = spawnSync(process.execPath, args, {env, stdio: 'inherit'})

if (result.error) throw result.error
process.exit(result.status ?? 1)
