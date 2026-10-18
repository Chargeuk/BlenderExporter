// Shared browser selection for readiness checks and real ENV conversions.
const fs = require('node:fs');
const path = require('node:path');

function browserPath(chromium, explicit) {
  let selected = explicit || process.env.BJS_ENV_CHROMIUM;
  const configPath = path.join(__dirname, 'runtime.local.json');
  if (!selected && fs.existsSync(configPath)) {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    if (config.schema_version !== 1 || typeof config.browser !== 'string' || !config.browser.trim())
      throw Error('Invalid runtime.local.json; run the explicit ENV converter setup/repair');
    selected = config.browser;
  }
  selected = selected ? path.resolve(selected) : chromium.executablePath();
  if (!fs.existsSync(selected) || !fs.statSync(selected).isFile())
    throw Error('Chromium missing: run setup.py, select --browser, or explicitly allow --download-browser');
  return selected;
}

function launchOptions(executablePath) {
  return {headless:true, executablePath,
    args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']};
}

module.exports = {browserPath, launchOptions};
