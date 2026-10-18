// No npm install/browser launch: verify persisted selection and both override routes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ENV runtime with spaces-'));
const previous = process.env.BJS_ENV_CHROMIUM;
try {
  const helper = path.join(temp, 'runtime.cjs');
  fs.copyFileSync(path.join(__dirname, '../src/babylon_js/workflow_tools/env_converter/runtime.cjs'), helper);
  const runtime = require(helper);
  const browsers = ['default Chromium.exe', 'saved Chrome.exe', 'environment Chrome.exe', 'explicit Chrome.exe']
    .map(name=>{const file=path.join(temp,name);fs.writeFileSync(file,'fixture');return file;});
  const chromium = {executablePath:()=>browsers[0]};
  delete process.env.BJS_ENV_CHROMIUM;
  assert.equal(runtime.browserPath(chromium), browsers[0]);
  const config = path.join(temp, 'runtime.local.json');
  fs.writeFileSync(config, JSON.stringify({schema_version:1,browser:browsers[1]}));
  assert.equal(runtime.browserPath(chromium), browsers[1]);
  process.env.BJS_ENV_CHROMIUM = browsers[2];
  assert.equal(runtime.browserPath(chromium), browsers[2]);
  assert.equal(runtime.browserPath(chromium, browsers[3]), browsers[3]);
  assert.equal(runtime.launchOptions(runtime.browserPath(chromium)).executablePath, browsers[2]);
  delete process.env.BJS_ENV_CHROMIUM;
  fs.writeFileSync(config, JSON.stringify({schema_version:1,browser:path.join(temp,'missing.exe')}));
  assert.throws(()=>runtime.browserPath(chromium), /Chromium missing/);
  fs.writeFileSync(config, JSON.stringify({schema_version:2,browser:browsers[1]}));
  assert.throws(()=>runtime.browserPath(chromium), /Invalid runtime.local.json/);
  assert.equal(runtime.browserPath(chromium,browsers[3]),browsers[3]);
  console.log('ENV_RUNTIME_SELECTION_PASSED');
} finally {
  if (previous === undefined) delete process.env.BJS_ENV_CHROMIUM;
  else process.env.BJS_ENV_CHROMIUM = previous;
  // This unique fixture folder is the only recursive cleanup target.
  assert.equal(path.dirname(temp), os.tmpdir());
  fs.rmSync(temp, {recursive:true,force:true});
}
