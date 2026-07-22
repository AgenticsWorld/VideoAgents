const minimum = [22, 12, 0]
const current = process.versions.node.split('.').map(Number)

function compare(a, b) {
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    const difference = (a[index] || 0) - (b[index] || 0)
    if (difference) return difference
  }
  return 0
}

if (compare(current, minimum) < 0) {
  console.error(`\nVideoAgents Desktop requires Node.js >= ${minimum.join('.')}; current: ${process.versions.node}.`)
  console.error('This repository already provides .nvmrc. Run:')
  console.error('  nvm use')
  console.error('  npm ci')
  console.error('  npm run dev:desktop\n')
  process.exit(1)
}
