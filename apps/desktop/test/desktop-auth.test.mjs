import assert from 'node:assert/strict'
import test from 'node:test'
import {serviceRegion} from '../dist/desktop-auth.js'

const BUILD = {schema: 1, channel: 'release', version: '1.0.0', buildHash: 'test'}

function withServiceEnvironment(value, run) {
  const previousEnvironment = process.env.VIDEOAGENTS_SERVICE_ENV
  const previousDistribution = process.env.VIDEOAGENTS_DISTRIBUTION
  const previousApiOrigin = process.env.VIDEOAGENTS_SERVICE_API_ORIGIN
  try {
    if (value === undefined) delete process.env.VIDEOAGENTS_SERVICE_ENV
    else process.env.VIDEOAGENTS_SERVICE_ENV = value
    delete process.env.VIDEOAGENTS_DISTRIBUTION
    delete process.env.VIDEOAGENTS_SERVICE_API_ORIGIN
    run()
  } finally {
    if (previousEnvironment === undefined) delete process.env.VIDEOAGENTS_SERVICE_ENV
    else process.env.VIDEOAGENTS_SERVICE_ENV = previousEnvironment
    if (previousDistribution === undefined) delete process.env.VIDEOAGENTS_DISTRIBUTION
    else process.env.VIDEOAGENTS_DISTRIBUTION = previousDistribution
    if (previousApiOrigin === undefined) delete process.env.VIDEOAGENTS_SERVICE_API_ORIGIN
    else process.env.VIDEOAGENTS_SERVICE_API_ORIGIN = previousApiOrigin
  }
}

test('development service environment uses every Agentics development endpoint', () => {
  withServiceEnvironment('dev', () => {
    assert.deepEqual(serviceRegion(BUILD), {
      environment: 'dev',
      distribution: 's3',
      apiOrigin: 'https://devapi.agentics.world',
      ssoOrigin: 'https://devsso.agentics.world',
      openrouterWrapperUrl: 'https://devapi.agentics.world/wrapper/openrouter',
      redirectUri: 'videoagents://agentics.world',
    })
  })
})

test('production service environment keeps every Agentics production endpoint', () => {
  withServiceEnvironment('production', () => {
    assert.deepEqual(serviceRegion(BUILD), {
      environment: 'production',
      distribution: 's3',
      apiOrigin: 'https://api.agentics.world',
      ssoOrigin: 'https://sso.agentics.world',
      openrouterWrapperUrl: 'https://api.agentics.world/wrapper/openrouter',
      redirectUri: 'videoagents://agentics.world',
    })
  })
})

test('local and dev builds default to the development service environment', () => {
  withServiceEnvironment(undefined, () => {
    assert.equal(serviceRegion({...BUILD, channel: 'local'}).environment, 'dev')
    assert.equal(serviceRegion({...BUILD, channel: 'dev'}).ssoOrigin, 'https://devsso.agentics.world')
  })
})
