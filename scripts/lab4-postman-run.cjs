// Execute actual Postman scripts/assertions without printing credentials or bodies.
// npm install --prefix .git/lab4-newman --no-audit --no-fund newman@6.2.1
// node scripts/lab4-postman-run.cjs <private-environment-path> [collection-path]
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const newman = require(path.join(root, '.git/lab4-newman/node_modules/newman'));
const environment = process.argv[2];
if (!environment) throw new Error('Provide the private environment path; see postman/README.md');
const collection = process.argv[3] || path.join(root, 'postman/lab4.postman_collection.json');
const run = newman.run({
  collection: JSON.parse(fs.readFileSync(collection, 'utf8')),
  environment: JSON.parse(fs.readFileSync(environment, 'utf8')),
  reporters: [], delayRequest: 200, timeoutRequest: 30000, timeoutScript: 10000,
}, (err, summary) => {
  if (err) {
    console.error('Postman execution error: ' + err.name);
    process.exitCode = 1;
    return;
  }
  for (const failure of summary.run.failures) {
    console.error('FAIL: ' + failure.source.name + ' / ' + (failure.error.test || failure.error.name));
  }
  const stats = summary.run.stats;
  console.log(`${stats.requests.total} requests; ${stats.assertions.total} assertions; ${summary.run.failures.length} failures`);
  process.exitCode = summary.run.failures.length ? 1 : 0;
});
run.on('request', (err, args) => {
  console.log((err ? 'NETWORK ERROR' : args.response.code) + ' ' + args.item.name);
});
