import { mkdir, readFile, writeFile, copyFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import http from 'node:http';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'dist/site');
await mkdir(output, { recursive: true });
for (const name of ['index.html', 'styles.css']) {
  await copyFile(path.join(root, 'site', name), path.join(output, name));
}
await copyFile(path.join(root, 'docs/evidence/community-2026-11-13/full-local-transcript-resident.jpg'), path.join(output, 'resident.jpg'));
await writeFile(path.join(output, '.nojekyll'), '');
const html = await readFile(path.join(output, 'index.html'), 'utf8');
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
let checked = 0;
for (const [, value] of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
  if (/^https:\/\//.test(value)) continue;
  if (value.startsWith('#')) {
    if (!ids.has(value.slice(1))) throw new Error(`Missing anchor: ${value}`);
  } else {
    const target = path.resolve(output, value);
    if (!target.startsWith(output + path.sep)) throw new Error(`Unsafe asset: ${value}`);
    if (!(await stat(target)).isFile()) throw new Error(`Missing file: ${value}`);
  }
  checked++;
}
console.log(`Built dist/site; verified ${checked} local links/assets/anchors.`);

if (process.argv.includes('--serve')) {
  const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.jpg': 'image/jpeg' };
  const server = http.createServer(async (request, response) => {
    try {
      let name = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (name === '/townrecord') { response.writeHead(302, { Location: '/townrecord/' }); response.end(); return; }
      if (name.startsWith('/townrecord/')) name = name.slice('/townrecord'.length);
      if (name === '/') name = '/index.html';
      const target = path.resolve(output, '.' + name);
      if (!target.startsWith(output + path.sep)) throw new Error('Invalid path');
      const bytes = await readFile(target);
      response.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream' });
      response.end(bytes);
    } catch {
      response.writeHead(404, { 'Content-Type': 'text/plain' }); response.end('Not found');
    }
  });
  server.listen(18320, '127.0.0.1', () => console.log('Preview: http://127.0.0.1:18320/townrecord/ — Ctrl+C to stop'));
}
