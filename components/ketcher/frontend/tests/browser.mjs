import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const root = new URL('../', import.meta.url).pathname;
let browser;
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '4178', '--strictPort'], {
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
  page.on('pageerror', error => errors.push('PAGE: '+error.stack));
  page.on('console', message => { if (message.type()==='error') errors.push('CONSOLE: '+message.text()); });
  page.on('requestfailed', req => errors.push('NETWORK: '+req.url()+' '+(req.failure()?.errorText||'')));
  await page.goto('http://127.0.0.1:4178/', {waitUntil:'domcontentloaded'});
  try {
    await page.waitForFunction(() => !!window.ketcher && typeof window.ketcher.setMolecule === 'function',
      null, {timeout:45000});
  } catch (error) {
    console.error('Ketcher did not initialize. Browser errors:', errors.slice(-30));
    for (const detail of errors) {
      const match = detail.match(/\/assets\/([^/:]+\.js):(\d+):(\d+)/);
      if (!match) continue;
      const bundle = path.join(root, '..', 'dist', 'assets', match[1]);
      if (!fs.existsSync(bundle)) continue;
      const lines = fs.readFileSync(bundle,'utf8').split('\n');
      const line = lines[Number(match[2])-1] || '';
      const position = Number(match[3])-1;
      console.error('Failing bundle fragment:', line.slice(Math.max(0,position-300),position+300));
    }
    console.error('Visible browser body:', (await page.locator('body').innerText()).slice(0,3000));
    await page.screenshot({path:'ketcher-browser-failure.png',fullPage:true});
    throw error;
  }
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
  await page.waitForFunction(() => window.__panchemMessage?.value?.action === 'apply', null, {timeout:60000});
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
