// Own the Matter process so a native HA backup can stage a consistent snapshot
// without stopping the controller. The control socket lives in a node-local
// directory shared only with the HA pod; no network API is added.
import { spawn, execFile } from 'node:child_process';
import { createServer } from 'node:http';
import { chmod, copyFile, mkdir, mkdtemp, open, readFile, readdir, lstat, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { pathToFileURL } from 'node:url';
import { realpathSync } from 'node:fs';

const run = promisify(execFile);

// Runtime-only files. The lock and PID belong to the live process, and the
// storage drivers write every replaced file through a temporary name first.
function transient(name) {
  return name === 'matter.lock' || name === 'matter.pid' || /\.tmp(\.|$)/.test(name);
}

// Copy the storage tree while Matter keeps writing to it.
//
// matter.js persists key/value state as an append-only write-ahead log plus a
// snapshot that is replaced atomically by rename. While the server runs it
// never deletes log segments, only appends to the newest one and compresses
// rotated ones. Copying every log segment before the snapshot therefore yields
// a state the server can load: commits already covered by a newer snapshot are
// skipped on replay, and a torn final line is ignored with a warning.
// Subdirectories are copied before files so `wal/` precedes `snapshot.json.gz`.
async function copyTree(source, target) {
  await mkdir(target, { recursive: true });
  const entries = await readdir(source);
  const kinds = await Promise.all(entries.map(async name => {
    const stat = await lstat(join(source, name));
    if (stat.isDirectory()) return 'dir';
    if (stat.isFile()) return 'file';
    throw new Error('Matter data contains a non-regular file');
  }));
  const named = entries.map((name, index) => ({ name, kind: kinds[index] }));
  for (const { name } of named.filter(e => e.kind === 'dir').sort((a, b) => a.name.localeCompare(b.name))) {
    await copyTree(join(source, name), join(target, name));
  }
  for (const { name } of named.filter(e => e.kind === 'file' && !transient(e.name)).sort((a, b) => a.name.localeCompare(b.name))) {
    await copyFile(join(source, name), join(target, name));
  }
}

// A rotated log segment can be compressed and removed between listing and
// copying. That window is tiny; retrying the whole copy makes it harmless.
export async function copyState(source, target, attempts = 3) {
  for (let attempt = 1; ; attempt++) {
    try {
      await copyTree(source, target);
      return;
    } catch (error) {
      await rm(target, { recursive: true, force: true });
      if (error.code !== 'ENOENT' || attempt >= attempts) throw error;
    }
  }
}

export async function archiveState({ storage, backup, image }) {
  const source = join(storage, 'server');
  if (!(await readdir(source)).length) throw new Error('Matter data is empty');
  const work = await mkdtemp(join(backup, '.snapshot-'));
  try {
    await copyState(source, join(work, 'server'));
    if (!(await readdir(join(work, 'server'))).length) throw new Error('Matter data is empty');
    await writeFile(join(work, 'metadata.json'), JSON.stringify({
      format: 1, server_image: image, created_at: new Date().toISOString(),
    }), { mode: 0o600 });
    const archive = join(work, 'latest.tar.gz');
    await run('tar', ['-czf', archive, '-C', work, 'server', 'metadata.json'], { timeout: 30000 });
    await run('tar', ['-tzf', archive], { timeout: 30000 });
    await chmod(archive, 0o600);
    const file = await open(archive, 'r');
    try { await file.sync(); } finally { await file.close(); }
    await rename(archive, join(backup, 'latest.tar.gz'));
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

// ws allows 30 seconds for peers to acknowledge a close frame. Give that
// handshake time to finish before waiting for Matter's storage flush.
export function supervise({ command, storage, backup, socket, image, port, timeSync = false, backupEnabled = true, stopTimeout = 60000, fatal = () => process.exit(1) }) {
  let child;
  let snapshot;
  let timezoneUpdate;
  let timezone;
  const timezoneFile = join(storage, "kube4ha", "timezone.json");
  let closing = false;
  const expectedExits = new WeakSet();

  function start() {
    if (closing) return;
    child = spawn(command[0], command.slice(1), { stdio: 'inherit', env: { ...process.env,
      ...(timeSync ? { ENABLE_TIME_SYNC: timezone ? 'true' : 'false', TZ: timezone || 'UTC' } : {}),
    } });
    const started = child;
    started.on('error', () => { console.error('Matter process failed to start'); fatal(); });
    started.on('exit', () => {
      if (!expectedExits.has(started) && !closing) {
        console.error('Matter process exited unexpectedly');
        fatal();
      }
    });
  }

  async function stop() {
    const stopped = child;
    if (!stopped || stopped.exitCode !== null || stopped.signalCode !== null) {
      throw new Error('Matter process is not running');
    }
    expectedExits.add(stopped);
    const result = new Promise(resolve => stopped.once('close', (code, signal) => resolve({ code, signal })));
    let forced = false;
    const timer = setTimeout(() => { forced = true; stopped.kill('SIGKILL'); }, stopTimeout);
    stopped.kill('SIGTERM');
    let exit;
    try { exit = await result; } finally { clearTimeout(timer); child = undefined; }
    if (forced) {
      console.error('Matter exceeded its graceful shutdown timeout');
      throw new Error('Matter did not stop cleanly');
    }
    if (exit.code !== 0 || exit.signal) throw new Error('Matter did not stop cleanly');
  }

  function validateTimezone(value) {
    if (typeof value !== 'string' || !value || value.length > 128) throw new Error('Invalid timezone');
    return new Intl.DateTimeFormat('en', { timeZone: value }).resolvedOptions().timeZone;
  }

  async function changeTimezone(value) {
    await mkdir(join(storage, 'kube4ha'), { recursive: true, mode: 0o700 });
    const file = await open(`${timezoneFile}.tmp`, 'w', 0o600);
    try { await file.writeFile(JSON.stringify({ timezone: value })); await file.sync(); }
    finally { await file.close(); }
    await rename(`${timezoneFile}.tmp`, timezoneFile);
    // Stop must finish before a replacement may acquire the fabric lock.
    try { await stop(); }
    catch (error) { fatal(); throw error; }
    timezone = value;
    start();
    console.log(`Matter timezone set to ${timezone}; time synchronization enabled`);
  }

  async function healthy() {
    if (!child || child.exitCode !== null || child.signalCode !== null) return false;
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(1500) });
      await response.arrayBuffer();
      return response.ok;
    } catch { return false; }
  }

  const server = createServer(async (request, response) => {
    const reply = (status, body) => {
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(body));
    };
    if (request.method === 'GET' && request.url === '/health') {
      // A deliberate graceful restart can spend 30s closing WebSockets.
      // Do not let liveness interrupt it before the bounded stop completes.
      reply(!closing && (timezoneUpdate || await healthy()) ? 200 : 503, {});
      return;
    }
    if (timeSync && request.method === 'POST' && request.url === '/timezone') {
      let value;
      try {
        let body = '';
        for await (const chunk of request) {
          body += chunk;
          if (body.length > 1024) throw new Error('Request too large');
        }
        value = validateTimezone(JSON.parse(body).timezone);
      } catch { reply(400, { error: 'Invalid timezone' }); return; }
      if (closing || snapshot || timezoneUpdate) { reply(409, { error: 'Matter operation in progress' }); return; }
      if (value === timezone) { reply(200, { timezone }); return; }
      timezoneUpdate = changeTimezone(value);
      try { await timezoneUpdate; reply(200, { timezone }); }
      catch { reply(500, { error: 'Could not apply Matter timezone' }); }
      finally { timezoneUpdate = undefined; }
      return;
    }
    if (!backupEnabled) { reply(404, {}); return; }
    if (request.method !== 'POST' || request.url !== '/snapshot') { reply(404, {}); return; }
    if (closing || snapshot || timezoneUpdate) { reply(409, { error: 'Matter snapshot already in progress' }); return; }
    // Claim the operation before any await so concurrent requests are
    // rejected. Continue even if the HTTP client disconnects.
    snapshot = (async () => {
      if (!await healthy()) throw new Error('Matter is not ready');
      await archiveState({ storage, backup, image });
    })();
    try {
      await snapshot;
      console.log('Staged Matter state for the native Home Assistant backup');
      reply(200, { format: 1 });
    } catch {
      console.error('Matter snapshot failed; preserving the previous archive');
      reply(500, { error: 'Matter snapshot failed' });
    } finally { snapshot = undefined; }
  });

  async function listen() {
    if (timeSync) {
      try { timezone = validateTimezone(JSON.parse(await readFile(timezoneFile, 'utf8')).timezone); }
      catch (error) {
        if (error.code !== 'ENOENT') console.warn('Ignoring invalid saved Matter timezone; waiting for Home Assistant');
      }
      if (!timezone) console.log('Waiting for Home Assistant timezone before enabling time synchronization');
    }
    await rm(socket, { force: true });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socket, resolve); });
    await chmod(socket, 0o600);
    start();
  }

  async function close() {
    closing = true;
    server.close();
    if (timezoneUpdate) await timezoneUpdate.catch(() => {});
    if (snapshot) await snapshot.catch(() => {});
    if (child) await stop().catch(() => {});
    await rm(socket, { force: true });
  }
  return { listen, close };
}

// Kubernetes ConfigMap files are symlinks. Compare their resolved paths so
// invoking the mounted script actually starts the supervisor.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  process.umask(0o077);
  const controller = supervise({
    command: ['node', '--enable-source-maps', '/app/node_modules/matter-server/dist/esm/MatterServer.js'],
    storage: '/data', backup: '/backup', socket: '/run/kube4ha-matter/control.sock',
    image: process.env.MATTER_SERVER_IMAGE, port: process.env.PORT || '5580',
    timeSync: process.env.MATTER_TIME_SYNC_ENABLED === 'true',
    backupEnabled: process.env.MATTER_BACKUP_ENABLED !== 'false',
  });
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.once(signal, async () => { await controller.close(); process.exit(0); });
  }
  await controller.listen();
}
