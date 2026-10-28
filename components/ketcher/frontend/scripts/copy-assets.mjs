import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const origin = path.join(root, 'node_modules', 'ketcher-react', 'dist');
const target = path.join(root, 'public');
const resourceExt = new Set(['.svg', '.png', '.jpg', '.jpeg', '.gif', '.woff', '.woff2', '.ttf', '.webp', '.json', '.wasm']);
let copied = 0;
function visit(source, relative = '') {
  if (!fs.existsSync(source)) return;
  for (const item of fs.readdirSync(source, { withFileTypes: true })) {
    const next = path.join(source, item.name), rel = path.join(relative, item.name);
    if (item.isDirectory()) visit(next, rel);
    else if (resourceExt.has(path.extname(item.name).toLowerCase())) {
      const out = path.join(target, rel);
      fs.mkdirSync(path.dirname(out), { recursive: true });
      fs.copyFileSync(next, out);
      copied++;
    }
  }
}
visit(origin);
console.log('Copied Ketcher static assets:', copied);
