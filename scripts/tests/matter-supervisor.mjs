import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';
import { copyState, supervise } from '../../charts/kube4ha/charts/homeassistant/files/matter/supervisor.mjs';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function call(socket, method, path) {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath: socket, method, path }, response => {
      response.resume();
      response.on('end', () => resolve(response.statusCode));
    });
    req.on('error', reject);
    req.end();
  });
}
function list(archive) {
  return execFileSync('tar', ['-tzf', archive], { encoding: 'utf8' }).trim().split('\n').sort();
}

async function fixture(t, { stopTimeout = 2000 } = {}) {
  const root = await mkdtemp('/tmp/kube4ha-matter-test-');
  const storage = join(root, 'data');
  const backup = join(root, 'backup');
  const socket = join(root, 'control.sock');
  const server = join(storage, 'server');
  await mkdir(join(server, 'wal'), { recursive: true });
  await mkdir(backup);
  await writeFile(join(server, 'wal/00000001.jsonl'), '{"ts":1,"ops":[]}\n');
  await writeFile(join(server, 'snapshot.json.gz'), 'snapshot');
  await writeFile(join(server, 'matter.lock'), '');
  await writeFile(join(server, 'matter.pid'), '1 token');
  await writeFile(join(server, 'snapshot.tmp.json.gz'), 'partial');
  const listener = createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const signals = join(root, 'signals');
  const code = `
    const http = require('node:http'), fs = require('node:fs');
    const server = http.createServer((req,res) => res.end('{}')).listen(${port}, '127.0.0.1');
    process.on('SIGTERM', () => {
      fs.appendFileSync(${JSON.stringify(signals)}, 'SIGTERM\\n');
      setTimeout(() => server.close(() => process.exit(0)), 150);
    });
  `;
  const supervisor = supervise({ command: [process.execPath, '-e', code], storage, backup, socket, image: 'fixture:1.0.0', port, stopTimeout, fatal: () => assert.fail('Unexpected process failure') });
  await supervisor.listen();
  t.after(async () => { await supervisor.close(); await rm(root, { recursive: true, force: true }); });
  for (let attempt = 0; attempt < 100 && await call(socket, 'GET', '/health') !== 200; attempt++) await delay(25);
  assert.equal(await call(socket, 'GET', '/health'), 200);
  return { root, storage, server, backup, socket, signals, supervisor };
}

test('snapshot archives live state without stopping Matter', async t => {
  const { server, backup, socket, signals } = await fixture(t);
  assert.equal(await call(socket, 'POST', '/snapshot'), 200);
  assert.equal(await call(socket, 'GET', '/health'), 200);
  await assert.rejects(readFile(signals), { code: 'ENOENT' });
  const archive = join(backup, 'latest.tar.gz');
  assert.deepEqual(list(archive), ['metadata.json', 'server/', 'server/snapshot.json.gz', 'server/wal/', 'server/wal/00000001.jsonl']);
  assert.equal(execFileSync('tar', ['-xOzf', archive, 'server/wal/00000001.jsonl'], { encoding: 'utf8' }), '{"ts":1,"ops":[]}\n');
  const metadata = JSON.parse(execFileSync('tar', ['-xOzf', archive, 'metadata.json']));
  assert.equal(metadata.format, 1);
  assert.equal(metadata.server_image, 'fixture:1.0.0');
  // The live tree keeps its runtime files; only the copy omits them.
  await readFile(join(server, 'matter.lock'));
});

test('concurrent snapshot requests are rejected while one is running', async t => {
  const { socket, backup } = await fixture(t);
  const results = await Promise.all([call(socket, 'POST', '/snapshot'), call(socket, 'POST', '/snapshot')]);
  assert.deepEqual(results.sort(), [200, 409]);
  await readFile(join(backup, 'latest.tar.gz'));
  assert.equal(await call(socket, 'POST', '/snapshot'), 200);
});

test('archive failure preserves the last snapshot and keeps Matter running', async t => {
  const { server, backup, socket, signals } = await fixture(t);
  assert.equal(await call(socket, 'POST', '/snapshot'), 200);
  const before = await readFile(join(backup, 'latest.tar.gz'));
  await symlink('/outside', join(server, 'unsafe'));
  assert.equal(await call(socket, 'POST', '/snapshot'), 500);
  assert.deepEqual(await readFile(join(backup, 'latest.tar.gz')), before);
  await unlink(join(server, 'unsafe'));
  assert.equal(await call(socket, 'GET', '/health'), 200);
  await assert.rejects(readFile(signals), { code: 'ENOENT' });
});

test('copy skips runtime and temporary files', async t => {
  const root = await mkdtemp('/tmp/kube4ha-matter-copy-');
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = join(root, 'server');
  await mkdir(join(source, 'wal'), { recursive: true });
  await writeFile(join(source, 'wal/00000001.jsonl'), 'a\n');
  await writeFile(join(source, 'wal/00000002.jsonl'), 'b\n');
  await writeFile(join(source, 'snapshot.json.gz'), 's');
  await writeFile(join(source, 'head.json.gz'), 'h');
  await writeFile(join(source, 'matter.lock'), '');
  await writeFile(join(source, 'matter.pid'), '1 token');
  await writeFile(join(source, 'snapshot.tmp.json.gz'), 'partial');
  await writeFile(join(source, 'wal/00000002.jsonl.gz.tmp'), 'compressing');
  await copyState(source, join(root, 'copy'));
  assert.deepEqual(execFileSync('find', ['copy', '-type', 'f'], { cwd: root, encoding: 'utf8' }).trim().split('\n').sort(),
    ['copy/head.json.gz', 'copy/snapshot.json.gz', 'copy/wal/00000001.jsonl', 'copy/wal/00000002.jsonl']);
  await symlink('/outside', join(source, 'wal/link'));
  await assert.rejects(copyState(source, join(root, 'copy2')), { message: 'Matter data contains a non-regular file' });
  await assert.rejects(readFile(join(root, 'copy2/snapshot.json.gz')), { code: 'ENOENT' });
});

test('shutdown stops Matter gracefully', async t => {
  const { socket, signals, supervisor } = await fixture(t);
  assert.equal(await call(socket, 'GET', '/health'), 200);
  await supervisor.close();
  assert.equal(await readFile(signals, 'utf8'), 'SIGTERM\n');
  await assert.rejects(call(socket, 'GET', '/health'));
});
