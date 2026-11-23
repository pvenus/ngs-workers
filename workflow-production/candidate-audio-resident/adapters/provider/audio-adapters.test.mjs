import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import {createAudioAdapters} from './audio-adapters.mjs';
test('the sole HQ port accepts a WAV plus model BPM and rejects non-BGM before launch',async t=>{
 const root=await mkdtemp(join(tmpdir(),'hq-audio-port-'));t.after(()=>rm(root,{force:true,recursive:true}));
 const helper=join(root,'fake.py');
 await writeFile(helper,`import base64,io,json,sys,wave
value=json.load(sys.stdin)
assert len(sys.argv)==2
if value['durationSeconds'] is None: assert value['text'] in ('[Humming]\\n음—', ' x ')
else: assert value['durationSeconds']==10 and 'text' not in value
media=io.BytesIO()
with wave.open(media,'wb') as w:
 w.setparams((2,2,48000,0,'NONE',''))
 w.writeframes(b'\\x01\\x00\\x01\\x00'*20)
print(json.dumps({"wavBase64":base64.b64encode(media.getvalue()).decode(),"bpm":80,"bpmBasis":"model"}))
`);
 const adapters=createAudioAdapters({helper,hqBinding:join(root,'unused')});
 assert.deepEqual(Object.keys(adapters),['bgm','separate']);
 const input={kind:'bgm',title:'Title',prompt:'Daegeum piano.',durationSeconds:10};
 const result=await adapters.bgm(input);
 assert.equal(result.wav.readUInt32LE(24),48000);
 assert.deepEqual([result.bpm,result.bpmBasis],[80,'model']);
 const auto=await adapters.bgm({...input,durationSeconds:null,text:'[Humming]\n음—'});
 assert.deepEqual(auto.wav,result.wav);
 await assert.rejects(adapters.bgm({...input,text:'[Humming]'}),/INPUT_INVALID/);
 assert.deepEqual((await adapters.bgm({...input,durationSeconds:null,text:' x '})).wav,result.wav);
 await assert.rejects(adapters.bgm({...input,durationSeconds:null,text:'  '}),/INPUT_INVALID/);
 await assert.rejects(adapters.bgm({...input,kind:'effect'}),/INPUT_INVALID/);
 await assert.rejects(adapters.bgm({...input,durationSeconds:9}),/INPUT_INVALID/);
});

test('only a bounded sanitized diagnostic can survive an unsuccessful bridge exit',async t=>{
 const root=await mkdtemp(join(tmpdir(),'hq-diag-'));t.after(()=>rm(root,{force:true,recursive:true}));
 const helper=join(root,'fake.py');
 const adapters=createAudioAdapters({helper,hqBinding:join(root,'unused')});
 const input={kind:'bgm',title:'Title',prompt:'Daegeum piano.',durationSeconds:null};
 const cases=[
  ['AUDIO_HQ_DIAGNOSTIC stage=plan code=HQ_LM_AUTO_DURATION_INVALID','plan','HQ_LM_AUTO_DURATION_INVALID'],
  ['secret payload\nAUDIO_HQ_DIAGNOSTIC stage=plan code=HQ_LM_AUTO_DURATION_INVALID',undefined,undefined],
  ['x'.repeat(514),undefined,undefined],
 ];
 for(const [line,phase,code] of cases){
  await writeFile(helper,'import sys\nsys.stdin.read()\nprint('+JSON.stringify(line)+',file=sys.stderr)\nraise SystemExit(1)\n');
  await assert.rejects(adapters.bgm(input),e=>e.message==='AUDIO_ONE_PROVIDER_FAILED'&&e.providerPhase===phase&&e.providerCode===code&&!e.message.includes('secret'));
 }
});

const fixtureInput={kind:'bgm',title:'Title',prompt:'Daegeum piano.',durationSeconds:10};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function residentFixture(t){
 const {readFile}=await import('node:fs/promises');
 const {spawn}=await import('node:child_process');
 const {audioLifecycle}=await import('./audio-resident-bridge.mjs');
 const root=await mkdtemp(join(tmpdir(),'hq-warm-'));
 const helper=join(root,'fake.py'),trace=join(root,'private-trace');
 let spawns=0;
 await writeFile(helper,`import base64,io,json,os,signal,sys,time,wave
assert sys.argv[2]=='--resident'
trace=sys.argv[1]
def mark(value):
 with open(trace,'a') as f:f.write(value+'\\n')
def closed(*_):
 mark('cancel')
 raise SystemExit(0)
signal.signal(signal.SIGTERM,closed)
mark('spawn')
for line in sys.stdin:
 q=json.loads(line)
 assert set(q)=={'id','input'} and q['id'].isnumeric()
 title=q['input']['title']
 mark(title)
 if title=='cancel me':
  while True:time.sleep(.01)
 if title=='provider fail':
  print(json.dumps({'id':q['id'],'ok':False,'error':'HQ_LM_GENERATION_FAILED','phase':'plan'}),flush=True)
  raise SystemExit(1)
 if title=='invalid reply':
  print(json.dumps({'id':'old','ok':True,'result':None}),flush=True)
  continue
 media=io.BytesIO()
 with wave.open(media,'wb') as w:
  w.setparams((2,2,48000,0,'NONE',''))
  w.writeframes(b'\\x01\\x00\\x01\\x00'*20)
 print(json.dumps({'id':q['id'],'ok':True,'result':{'wavBase64':base64.b64encode(media.getvalue()).decode(),'bpm':80,'bpmBasis':'model'}}),flush=True)
mark('idle-close')
`);
 const adapter=createAudioAdapters({helper,hqBinding:trace,resident:true,idleMs:50,
  spawnProcess(...args){spawns++;return spawn(...args);}});
 const lifecycle=adapter[audioLifecycle];
 t.after(async()=>{await lifecycle.close();await rm(root,{force:true,recursive:true});});
 async function logs(){try{return (await readFile(trace,'utf8')).trim().split('\n');}catch(e){if(e.code==='ENOENT')return [];throw e;}}
 async function see(event){
  for(let now=Date.now();Date.now()-now<2000;){
   if((await logs()).includes(event))return;
   await sleep(10);
  }
  assert.fail('missing child event '+event);
 }
 return {adapter,lifecycle,logs,see,spawns:()=>spawns};
}

test('resident child starts lazily, reuses exact worker, resets idle and never evicts during downstream task work',async t=>{
 const {adapter,lifecycle,logs,see,spawns}=await residentFixture(t);
 const {warmIdleMs}=await import('./audio-resident-bridge.mjs');
 assert.equal(warmIdleMs,5*60*1000);assert.equal(spawns(),0);assert.deepEqual(Object.keys(adapter),['bgm','separate']);
 lifecycle.beginTask();
 assert.equal((await adapter.bgm(fixtureInput)).bpm,80);
 await sleep(85); // Native task returned; PDS publication is still reserved.
 assert(!((await logs()).includes('idle-close')));
 assert.equal((await adapter.bgm({...fixtureInput,title:'next'})).bpm,80);
 assert.equal(spawns(),1);
 lifecycle.endTask();
 await see('idle-close');
 lifecycle.beginTask();
 assert.equal((await adapter.bgm({...fixtureInput,title:'after idle'})).bpm,80);
 assert.equal(spawns(),2);
 lifecycle.endTask();
});

test('a fault, wrong request id or cancellation never replays the pending job and joins before the next task',async t=>{
 const {adapter,logs,see,spawns}=await residentFixture(t);
 await assert.rejects(adapter.bgm({...fixtureInput,title:'provider fail'}),e=>e.message==='AUDIO_ONE_PROVIDER_FAILED'&&
  e.providerPhase==='plan'&&e.providerCode==='HQ_LM_GENERATION_FAILED');
 assert.equal(spawns(),1);
 await assert.rejects(adapter.bgm({...fixtureInput,title:'invalid reply'}),/AUDIO_HQ_RECEIPT_INVALID/);
 assert.equal(spawns(),2);
 const ctr=new AbortController;
 const pending=adapter.bgm({...fixtureInput,title:'cancel me'},{signal:ctr.signal});
 const checked=assert.rejects(pending,/AUDIO_ONE_CANCELLED/);
 await see('cancel me');ctr.abort();await checked;
 assert.equal(spawns(),3);await see('cancel');
 const trace=await logs();
 for(const title of ['provider fail','invalid reply','cancel me'])assert.equal(trace.filter(x=>x===title).length,1);
 assert.equal((await adapter.bgm(fixtureInput)).bpm,80);assert.equal(spawns(),4);
});

test('the shipped Python resident entry point speaks exact Node protocol and only closes its provider after lease ends',async t=>{
 const {copyFile,readFile}=await import('node:fs/promises');
 const {audioLifecycle}=await import('./audio-resident-bridge.mjs');
 const root=await mkdtemp(join(tmpdir(),'hq-warm-shipped-'));
 const helper=join(root,'audio-hq-cli.py'),trace=join(root,'private-trace');
 await copyFile(new URL('./audio-hq-cli.py',import.meta.url),helper);
 await writeFile(join(root,'music_boundary.py'),`from contextlib import contextmanager
from types import SimpleNamespace
HQ_PROFILE='fixture'
def _music_boundary_from_binding(profile,file):
 @contextmanager
 def resident_music(config):
  def mark(s):
   with open(file,'a') as f:f.write(s+'\\n')
  mark('load')
  try:
   yield lambda *args:mark('generate')
  finally:
   mark('unload')
 assert profile=='fixture'
 return SimpleNamespace(resident_music=resident_music), {}
`);
 await writeFile(join(root,'hq_bgm.py'),`import base64,io,wave
from music_boundary import HQ_PROFILE
def render_hq(value,binding,cancelled,*,connect):
 provider,config=connect(HQ_PROFILE,binding)
 provider.generate_music()
 b=io.BytesIO()
 with wave.open(b,'wb') as w:
  w.setparams((2,2,48000,0,'NONE',''))
  w.writeframes(b'\\0\\0\\0\\0'*12)
 return {'wavBase64':base64.b64encode(b.getvalue()).decode(),'bpm':80,'bpmBasis':'model'}
def render_cover_hq(value,binding,cancelled,*,connect):
 import hashlib
 assert set(value)=={'kind','sourceSha256','wavBase64'}
 assert hashlib.sha256(base64.b64decode(value['wavBase64'])).hexdigest()==value['sourceSha256']
 result=render_hq(value,binding,cancelled,connect=connect)
 return {**result,'bpm':None,'bpmBasis':'unavailable'}
`);
 const adapter=createAudioAdapters({helper,hqBinding:trace,resident:true}),lifecycle=adapter[audioLifecycle];
 t.after(async()=>{await lifecycle.close();await rm(root,{force:true,recursive:true});});
 const {createHash}=await import('node:crypto');
 lifecycle.beginTask();const first=await adapter.bgm(fixtureInput);assert.equal(first.bpm,80);lifecycle.endTask();
 lifecycle.beginTask();assert.equal((await adapter.bgm({...fixtureInput,title:'second'})).bpm,80);lifecycle.endTask();
 const sourceSha256=createHash('sha256').update(first.wav).digest('hex');
 lifecycle.beginTask();assert.equal((await adapter.separate({sourceSha256,sourceWav:first.wav})).bpmBasis,'unavailable');lifecycle.endTask();
 assert.equal(await readFile(trace,'utf8'),'load\ngenerate\ngenerate\ngenerate\n');
 await lifecycle.close();
 assert.equal(await readFile(trace,'utf8'),'load\ngenerate\ngenerate\ngenerate\nunload\n');
});
