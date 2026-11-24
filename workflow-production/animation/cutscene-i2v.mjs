/** One full-scene I2V clip. Renderer ownership belongs to the resident animation worker. */
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {mkdir, mkdtemp, open, realpath, rm, stat, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {maxImageBytes, outputArtifact, png} from './i2v-worker.mjs';

export const MAX_CUTSCENE_MP4_BYTES = 8 * 1024 * 1024;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
function require(ok, code) { if (!ok) throw Error('CUTSCENE_' + code); }

async function encoderPath(name) {
  require(name === 'ffmpeg' || name === 'ffprobe', 'ENCODER_SCOPE');
  const tool = await realpath('/opt/homebrew/bin/' + name);
  require(new RegExp('^/opt/homebrew/(?:bin/|Cellar/ffmpeg/[A-Za-z0-9._-]+/bin/)' + name + '$').test(tool), 'ENCODER_SCOPE');
  const s = await stat(tool);
  require(s.isFile() && [0, process.getuid()].includes(s.uid) && !(s.mode & 0o022) && (s.mode & 0o111), 'ENCODER_SCOPE');
  return tool;
}

export async function probeCutsceneEncoder() {
  await Promise.all([encoderPath('ffmpeg'), encoderPath('ffprobe')]);
}

async function callEncoder(name, args, signal) {
  const tool = await encoderPath(name);
  const deadline = AbortSignal.any([signal, AbortSignal.timeout(120_000)]);
  deadline.throwIfAborted();
  return await new Promise((resolve, reject) => {
    const child = spawn(tool, args, {signal: deadline, killSignal: 'SIGKILL', env: {LANG:'C', PATH:'/usr/bin:/bin'},
      stdio: ['ignore', 'pipe', 'ignore']});
    let failed = false, size = 0;
    const chunks = [];
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > 65_536) { failed = true; child.kill('SIGKILL'); }
      else chunks.push(chunk);
    });
    // Wait for close (including an aborted child) before removing its working files.
    child.once('error', () => { failed = true; });
    child.once('close', status => {
      if (failed || status !== 0 || deadline.aborted) reject(Error('CUTSCENE_ENCODER_FAILED'));
      else resolve(Buffer.concat(chunks, size).toString('utf8'));
    });
  });
}

function rate(value) {
  if (typeof value !== 'string' || !/^[1-9]\d{0,9}\/[1-9]\d{0,9}$/.test(value)) return NaN;
  const [n, d] = value.split('/').map(Number);
  return n / d;
}

export async function encodeCutscene(output, dir, signal, invoke = callEncoder) {
  signal.throwIfAborted();
  const {width, height, fps, frames} = outputArtifact(output, '0'.repeat(64));
  // Same geometry from the model: no resize, crop, segmentation, or alpha conversion.
  require(width % 2 === 0 && height % 2 === 0, 'GEOMETRY');
  const folder = join(dir, 'cutscene-rgb');
  await mkdir(folder, {mode: 0o700});
  for (let i = 0; i < frames.length; i++) {
    signal.throwIfAborted();
    await writeFile(join(folder, String(i).padStart(4, '0') + '.png'), output.frames[i], {flag: 'wx', mode: 0o600});
  }
  const path = join(dir, 'cutscene.mp4');
  await invoke('ffmpeg', ['-nostdin','-n','-v','error','-f','image2','-framerate',String(fps),'-start_number','0',
    '-i',join(folder,'%04d.png'),'-map','0:v:0','-frames:v',String(frames.length),'-c:v','libx264',
    '-crf','18','-pix_fmt','yuv420p','-movflags','+faststart','-an','-sn','-dn',path], signal);
  const p = JSON.parse(await invoke('ffprobe', ['-v','error','-count_frames','-show_entries',
    'stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_frames,duration:format=format_name',
    '-of','json',path], signal));
  const v = p?.streams?.[0];
  require(Array.isArray(p?.streams) && p.streams.length === 1 && v && v.codec_type === 'video' && v.codec_name === 'h264' &&
    v.width === width && v.height === height && v.pix_fmt === 'yuv420p' && rate(v.avg_frame_rate) === fps &&
    v.nb_read_frames === String(frames.length) && Math.abs(Number(v.duration)-frames.length/fps)<0.001 &&
    p.format?.format_name?.split(',').includes('mp4'), 'ENCODER_PROOF');
  const f = await open(path, 'r');
  let bytes;
  try { const info = await f.stat(); require(info.isFile() && info.size >= 24 && info.size <= MAX_CUTSCENE_MP4_BYTES, 'FILE_SIZE');
    bytes = await f.readFile(); require(bytes.length === info.size && bytes.toString('ascii',4,8) === 'ftyp', 'FILE_TYPE'); }
  finally { await f.close(); }
  return {bytes, sha256: digest(bytes), mimeType:'video/mp4', width,height,fps,frameCount:frames.length};
}

export const validCutscenePrompt = v => typeof v === 'string' && v.isWellFormed() && v === v.trim() &&
  [...v].length >= 1 && [...v].length <= 1000 && Buffer.byteLength(v) <= 2000 &&
  !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|\bembedding:/iu.test(v);

/**
 * The caller supplies the shared, serialized runtime (modelSlot.runI2V), never starts
 * a second model worker. Returned bytes are ready for normal publication by the caller.
 */
export function createCutsceneRenderer({renderI2V, encode = encodeCutscene}) {
  require(typeof renderI2V === 'function' && typeof encode === 'function', 'CONFIGURATION');
  return async function renderCutscene({source, motionPrompt, signal = new AbortController().signal}) {
    signal.throwIfAborted();
    png(source, maxImageBytes);
    require(validCutscenePrompt(motionPrompt), 'PROMPT');
    const sourceSha256 = digest(source);
    const dir = await mkdtemp(join(tmpdir(), 'workflow-cutscene-'));
    let cleanup = true;
    try {
      signal.throwIfAborted();
      let raw;
      try { raw = await renderI2V({dir,source,motionPrompt,signal}); }
      catch (err) { cleanup = err?.rendererJoined === true; throw err; }
      signal.throwIfAborted();
      return {...await encode(raw,dir,signal), sourceSha256};
    } finally {
      if (cleanup) await rm(dir,{recursive:true,force:true});
    }
  };
}
