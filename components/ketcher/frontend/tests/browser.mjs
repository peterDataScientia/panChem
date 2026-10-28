import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const root = new URL('../', import.meta.url).pathname;
let browser;
const server = spawn('npm', ['run', '--', 'preview', '--', '--host', '127.0.0.1', '--port', '4178', '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe']
});
let serverLog = '';
for (const stream of [server.stdout, server.stderr]) {
  stream.on('data', chunk => { serverLog += chunk.toString(); });
}
async function waitForServer(url) {
  for (let n = 0; n < 80; n++) {
    if (server.exitCode !== null) throw new Error('Vite preview ended: ' + serverLog);
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Vite preview failed to start: ' + serverLog);
}
try {
  await waitForServer('http://127.0.0.1:4178/');
  const executablePath = execFileSync('bash', ['-lc', 'command -v google-chrome || command -v chromium || command -v chromium-browser'], {encoding:'utf-8'}).trim();
  assert.ok(executablePath, 'Chrome/Chromium must be available on GitHub runner');
  browser = await chromium.launch({executablePath, headless:true, args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1220,height:850}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:4178/', {waitUntil:'domcontentloaded'});
  await page.waitForFunction(() => !!window.ketcher && typeof window.ketcher.setMolecule === 'function',
    {timeout:120000});
  assert.ok(await page.getByRole('button', {name:'Apply structure'}).count(), 'Apply button missing');
  const result = await page.evaluate(async () => {
    await window.ketcher.setMolecule('CCO');
    const smiles = await window.ketcher.getSmiles();
    const molfile = await window.ketcher.getMolfile('v2000');
    return {smiles, molfile};
  });
  assert.ok(result.smiles.trim().length>0, 'Ketcher did not return SMILES');
  assert.ok(result.molfile.includes('V2000'), 'Ketcher did not return MOL V2000');
  await page.evaluate(() => {
    window.__panchemMessage = null;
    window.addEventListener('message', event => {
      if (event.data?.type === 'streamlit:setComponentValue') {
        window.__panchemMessage = event.data;
      }
    });
  });
  await page.getByRole('button', {name:'Apply structure'}).click();
  await page.waitForFunction(() => window.__panchemMessage?.value?.action === 'apply', {timeout:60000});
  const event = await page.evaluate(() => window.__panchemMessage);
  assert.ok(event.value.smiles.length>0);
  assert.ok(event.value.molfile.includes('V2000'));
  assert.ok(event.value.sdf.includes('$$$$'));
  if (errors.length) console.log('Browser warnings:', errors.slice(0,3));
  console.log('Ketcher browser edit/export → Streamlit event: PASSED');
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
}
