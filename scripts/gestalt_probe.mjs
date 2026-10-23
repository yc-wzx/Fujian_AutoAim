#!/usr/bin/env node
// Local simulator telemetry only. No camera, serial port or hardware is opened.
// Node >=22; commands are optional and are recorded with the telemetry.
import { readFileSync, mkdirSync, writeFileSync, createWriteStream, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  port: { type: 'string' },
  'port-file': { type: 'string' },
  seconds: { type: 'string', default: '5' },
  output: { type: 'string' },
  command: { type: 'string', multiple: true, default: [] },
  camera: { type: 'string' },
  bindings: { type: 'boolean', default: false },
  player: { type: 'string' },
  'expected-omega': { type: 'string' },
  'omega-tolerance': { type: 'string', default: '0.10' },
  'spin-omega': { type: 'string' },
  'spin-position': { type: 'string' },
  'spin-yaw': { type: 'string', default: '0' },
  'spin-hz': { type: 'string', default: '120' },
  'warmup-seconds': { type: 'string', default: '0' },
  'claim-player': { type: 'string' },
  'aim-yaw': { type: 'string' },
  'aim-pitch': { type: 'string' },
  continuous: { type: 'boolean', default: false },
  'stop-file': { type: 'string' },
} });
const port = Number(values.port ?? (values['port-file']
  ? readFileSync(values['port-file'], 'utf8').trim() : NaN));
const seconds = Number(values.seconds);
if (values.continuous && (!values['stop-file'] || !values.output))
  throw new Error('--continuous needs --stop-file and --output. Create the stop file to stop.');
if (values['stop-file'] && existsSync(values['stop-file']))
  throw new Error('Stop file already exists; no simulator commands were sent.');
const warmupSeconds = Number(values['warmup-seconds']);
if (!Number.isFinite(warmupSeconds) || warmupSeconds < 0 || warmupSeconds >= seconds)
  throw new Error('--warmup-seconds must be finite and in [0,seconds).');
const player = values.player === undefined ? null : Number(values.player);
const expectedOmega = values['expected-omega'] === undefined ? null : Number(values['expected-omega']);
const omegaTolerance = Number(values['omega-tolerance']);
const spinOmega = values['spin-omega'] === undefined ? null : Number(values['spin-omega']);
const spinPosition = values['spin-position']?.split(',').map(Number);
const spinYaw = Number(values['spin-yaw']), spinHz = Number(values['spin-hz']);
if (spinOmega !== null && (player === null || !Number.isFinite(spinOmega) || Math.abs(spinOmega) > 12 ||
    spinPosition?.length !== 3 || !spinPosition.every(Number.isFinite) ||
    !Number.isFinite(spinYaw) || !Number.isFinite(spinHz) || spinHz < 30 || spinHz > 120))
  throw new Error('Kinematic spin needs --player, --spin-omega in [-12,12], --spin-position x,y,z (cm), and --spin-hz in [30,120].');
if (spinOmega === null && values['spin-position'] !== undefined)
  throw new Error('--spin-position needs --spin-omega.');
const camera = values.camera === undefined ? null : JSON.parse(values.camera);
const claimPlayer = values['claim-player'] === undefined ? null : Number(values['claim-player']);
const aimYaw = Number(values['aim-yaw']), aimPitch = Number(values['aim-pitch']);
if (claimPlayer !== null && (!Number.isInteger(claimPlayer) || claimPlayer < 0 ||
    !Number.isFinite(aimYaw) || !Number.isFinite(aimPitch) || Math.abs(aimPitch) > 45))
  throw new Error('--claim-player requires a nonnegative player id, --aim-yaw and --aim-pitch in [-45,45].');
if (claimPlayer === null && (values['aim-yaw'] !== undefined || values['aim-pitch'] !== undefined))
  throw new Error('--aim-yaw/--aim-pitch require --claim-player. This diagnostic always sends fire=0.');
if (camera !== null && (typeof camera !== 'object' || Array.isArray(camera)))
  throw new Error('--camera must be a JSON object.');
if (!Number.isInteger(port) || port < 1 || port > 65535 ||
    !Number.isFinite(seconds) || seconds < 1 || seconds > 60) {
  throw new Error('Use --port N or --port-file PATH; --seconds must be in [1,60].');
}
if ((player !== null && !Number.isInteger(player)) ||
    (expectedOmega !== null && (!Number.isFinite(expectedOmega) || player === null)) ||
    !Number.isFinite(omegaTolerance) || omegaTolerance <= 0)
  throw new Error('--expected-omega needs --player; use a finite angular rate and positive tolerance.');
const maps = new Map(), watched = new Set(), samples = new Map(), records = [];
let outputStream = null;
if (values.continuous) {
  mkdirSync(dirname(resolve(values.output)), {recursive: true});
  outputStream = createWriteStream(resolve(values.output));
}
function record(value) {
  if (outputStream) outputStream.write(JSON.stringify(value) + '\n');
  else records.push(value);
}
const commandRequests = new Map(), commandFailures = [];
const attr = { pid: 10000035, hp: 10000003, team: 10000036, cls: 60000002,
  x: 10000107, y: 10000108, z: 10000109, yaw: 10000110,
  bullets: 63000002, damage: 63000000, match: 80000005 };
let requestId = 910000, messages = 0, malformed = 0, opened = false, completed = false;
let inputBindings = null;
let spinTimer = null, spinCommands = 0, spinStarted = null;
let claimTimer = null, aimTimer = null, claimReleaseSent = false;
let stopTimer = null, summaryTimer = null;
let measurementStart = 0;
const socket = new WebSocket(`ws://127.0.0.1:${port}/`);
const send = (method, params) => {
  const id = ++requestId;
  socket.send(JSON.stringify({type: 0, id, method, params}));
  return id;
};
function stopControls() {
  if (spinTimer) clearInterval(spinTimer);
  if (claimTimer) clearInterval(claimTimer);
  if (aimTimer) clearInterval(aimTimer);
  spinTimer = claimTimer = aimTimer = null;
}
function command(command) {
  record({sent_s: performance.now() / 1000, command});
  commandRequests.set(send('console.exec', {command}), command);
  if (commandRequests.size > 4096) commandRequests.delete(commandRequests.keys().next().value);
}
function watch(ids) {
  const fresh = [...new Set(ids)].filter(id => Number.isInteger(id) && id > 0 && !watched.has(id));
  if (!fresh.length) return;
  fresh.forEach(id => watched.add(id));
  send('attribute.watchAttributeMaps', {attribute_map_ids: fresh, watch_type: 2});
}
socket.addEventListener('message', event => {
  ++messages;
  let packet;
  try { packet = JSON.parse(String(event.data)); } catch { ++malformed; return; }
  const at = performance.now() / 1000;
  record({received_s: at, packet});
  if (packet.method === 'input.bindingsResult' && packet.params?.request_id === 'gestalt-probe-input')
    inputBindings = packet.params;
  if (packet.type === 1 && commandRequests.has(packet.id) && packet.result?.success === false)
    commandFailures.push({command: commandRequests.get(packet.id), result: packet.result});
  if (packet.method !== 'watchAttributeMaps.result') return;
  const results = packet.params?.watch_attribute_maps_results;
  if (!Array.isArray(results)) return;
  const discovered = [];
  for (const result of results) {
    const mid = result.attribute_map_id;
    if (!Number.isInteger(mid) || !result.attributes) continue;
    const state = maps.get(mid) ?? {};
    for (const [key, value] of Object.entries(result.attributes)) {
      const aid = Number(key);
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      state[aid] = value;
      if ((aid === 1000001 || (aid >= 0 && aid < 100000)) && Number.isInteger(value) && value > 0)
        discovered.push(value);
    }
    maps.set(mid, state);
    if (Object.hasOwn(result.attributes, String(attr.yaw))) {
      const history = samples.get(mid) ?? [];
      history.push({t: at, yaw_deg: state[attr.yaw], x_cm: state[attr.x], y_cm: state[attr.y]});
      if (values.continuous && history.length > 1800) history.splice(0, history.length - 1800);
      samples.set(mid, history);
    }
  }
  watch(discovered);
});

// Fit only a contiguous receipt-time segment; gaps can alias a fast rotation.
// This checks scene motion, and is not an exposure timestamp for aiming.
function rotationFit(history = []) {
  let segment = [], theta = 0;
  for (const point of history) {
    const prior = segment.at(-1);
    if (prior) {
      const dt = point.t - prior.t;
      if (dt <= 0 || dt > .4) { segment = []; theta = 0; }
      else theta += ((point.yaw_deg - prior.yaw_deg + 540) % 360) - 180;
    }
    segment.push({...point, theta});
  }
  const span = segment.length > 1 ? segment.at(-1).t - segment[0].t : 0;
  if (span < 2 || segment.length < 10) return {valid: false, samples: segment.length, span_s: span};
  const t0 = segment[0].t;
  const mt = segment.reduce((sum, p) => sum + p.t - t0, 0) / segment.length;
  const my = segment.reduce((sum, p) => sum + p.theta, 0) / segment.length;
  const variance = segment.reduce((sum, p) => sum + (p.t - t0 - mt) ** 2, 0);
  const slope = segment.reduce((sum, p) => sum + (p.t - t0 - mt) * (p.theta - my), 0) / variance;
  const rms = Math.sqrt(segment.reduce((sum, p) => sum +
    (p.theta - my - slope * (p.t - t0 - mt)) ** 2, 0) / segment.length);
  const first = segment[0], last = segment.at(-1);
  return {valid: true, samples: segment.length, span_s: span,
    omega_rad_s: slope * Math.PI / 180, yaw_fit_rms_deg: rms,
    constant_rate_consistent: rms <= 5,
    endpoint_displacement_m: [first.x_cm, first.y_cm, last.x_cm, last.y_cm].every(Number.isFinite)
      ? Math.hypot(last.x_cm - first.x_cm, last.y_cm - first.y_cm) / 100 : null};
}

const timer = setTimeout(() => { completed = true; socket.close(); }, (seconds + 5) * 1000);
function finish() {
  if (completed) return;
  completed = true;
  stopControls();
  if (claimPlayer !== null && socket.readyState === WebSocket.OPEN) {
    command(`UEExec RBExtAim ${claimPlayer} ${aimYaw} ${aimPitch} 0`);
    command(`ExtAimClaim ${claimPlayer} 0`);
    claimReleaseSent = true;
  }
  setTimeout(() => socket.close(), 100);
}
await new Promise((done, reject) => {
  socket.addEventListener('open', () => {
    opened = true;
    measurementStart = performance.now() / 1000 + warmupSeconds;
    watch(Array.from({length: 256}, (_, i) => i + 1));
    if (values.bindings) send('input.queryBindings', {request_id: 'gestalt-probe-input'});
    for (const command of values.command) {
      record({sent_s: performance.now() / 1000, command});
      commandRequests.set(send('console.exec', {command}), command);
    }
    if (camera) {
      record({sent_s: performance.now() / 1000, camera});
      commandRequests.set(send('rgbCamera.applySettings', {camera}), 'rgbCamera.applySettings');
    }
    if (claimPlayer !== null) {
      // Bounded, non-firing pose probe. Renew the five-second lease separately
      // from vision inference; release it before the normal socket close.
      command(`ExtAimClaim ${claimPlayer} 1`);
      command(`UEExec RBTakeOver ${claimPlayer}`);
      const aim = () => command(`UEExec RBExtAim ${claimPlayer} ${aimYaw} ${aimPitch} 0`);
      aim();
      claimTimer = setInterval(() => command(`ExtAimClaim ${claimPlayer} 1`), 1000);
      aimTimer = setInterval(aim, 50);
    }
    if (spinOmega !== null) {
      // Explicit kinematic benchmark: prescribe the target transform, without
      // modifying game assets or feeding target truth to the vision estimator.
      spinStarted = performance.now();
      spinTimer = setInterval(() => {
        if (socket.readyState !== WebSocket.OPEN) return;
        const t = (performance.now() - spinStarted) / 1000;
        const yaw = ((spinYaw + spinOmega * t * 180 / Math.PI + 180) % 360 + 360) % 360 - 180;
        const command = `UEExec RBTeleport ${player} ${spinPosition.join(' ')} ${yaw.toFixed(5)}`;
        record({sent_s: performance.now() / 1000, command, kinematic_target_s: t});
        send('console.exec', {command});
        ++spinCommands;
      }, 1000 / spinHz);
    }
    clearTimeout(timer);
    if (!values.continuous) setTimeout(finish, seconds * 1000);
    if (values['stop-file']) stopTimer = setInterval(() => {
      if (existsSync(values['stop-file'])) finish();
    }, 250);
    if (values.continuous) summaryTimer = setInterval(() => {
      writeFileSync(resolve(values.output) + '.summary.json', JSON.stringify(buildSummary(), null, 2) + '\n');
    }, 5000);
  });
  socket.addEventListener('error', () => reject(new Error('Loopback simulator WebSocket failed.')));
  socket.addEventListener('close', () => { stopControls(); done(); });
});
clearTimeout(timer);
if (stopTimer) clearInterval(stopTimer);
if (summaryTimer) clearInterval(summaryTimer);
stopControls();
if (outputStream) await new Promise(done => outputStream.end(done));
const summary = buildSummary();
if (values.output) {
  const output = resolve(values.output);
  mkdirSync(dirname(output), {recursive: true});
  if (!values.continuous) writeFileSync(output, records.map(record => JSON.stringify(record)).join('\n') + '\n');
  writeFileSync(output + '.summary.json', JSON.stringify(summary, null, 2) + '\n');
}
console.log(JSON.stringify(summary, null, 2));
if (!opened || !completed || !summary.telemetry_ready || messages === 0 || malformed > 0 ||
    !summary.requested_player_found || commandFailures.length > 0 ||
    summary.omega_acceptance?.passed === false) process.exitCode = 2;

function buildSummary() {
const vehicles = [...maps].filter(([, m]) => m[attr.pid] !== undefined && m[attr.hp] !== undefined)
  .filter(([, m]) => player === null || m[attr.pid] === player)
  .map(([map_id, m]) => ({map_id, player_id: m[attr.pid], team: m[attr.team], cls: m[attr.cls],
    hp: m[attr.hp], x_cm: m[attr.x], y_cm: m[attr.y], z_cm: m[attr.z],
    bullets_total: m[attr.bullets], damage_total: m[attr.damage],
    raw_hits_total: m[63000003] ?? null, is_ai: m[50000088] ?? null,
    move_mode: m[50000089] ?? null, target_mode: m[50000090] ?? null,
    rotation: rotationFit(samples.get(map_id)?.filter(p => p.t >= measurementStart))}));
const summary = {opened, completed, port, messages, malformed, map_count: maps.size,
  continuous: values.continuous, stop_file: values['stop-file'] ?? null,
  match_status: [...maps.values()].find(m => m[attr.match] !== undefined)?.[attr.match] ?? null,
  vehicles, warmup_seconds: warmupSeconds, command_failures: commandFailures,
  timing: 'Local message receipt time; scene verification only, not exposure/impact timing.'};
summary.telemetry_ready = summary.map_count > 0 && summary.match_status !== null;
summary.prep_ready = summary.telemetry_ready && summary.match_status === 0;
summary.requested_player_found = player === null || vehicles.length === 1;
if (spinOmega !== null) summary.kinematic_spin = {player, omega_rad_s: spinOmega,
  position_cm: spinPosition, requested_hz: spinHz, commands: spinCommands,
  note: 'Prescribed transforms; does not model chassis motor dynamics. Verify actual yaw telemetry.'};
if (values.bindings) summary.input_bindings = inputBindings;
if (claimPlayer !== null) summary.non_firing_pose_probe = {
  player: claimPlayer, yaw_deg: aimYaw, pitch_deg: aimPitch, fire: 0,
  release_sent: claimReleaseSent,
  note: 'Release dispatch is recorded; verify actual mode restoration from telemetry.'};
if (expectedOmega !== null) {
  const target = vehicles.length === 1 ? vehicles[0] : null;
  summary.omega_acceptance = {expected_rad_s: expectedOmega, tolerance_rad_s: omegaTolerance,
    passed: !!target?.rotation.valid && target.rotation.constant_rate_consistent &&
      Math.abs(target.rotation.omega_rad_s - expectedOmega) <= omegaTolerance};
}
return summary;
}
