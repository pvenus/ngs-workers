import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,realpathSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {PlannerStore,buildPlan} from './vendor/planner/planner.mjs';
import {canonical,sha} from './vendor/planner/envelope.mjs';
import {AudioDefinitelyRejected,runAudioFromPath,nextAudioFromPath,audioResultRef} from './audio-worker.mjs';
import {validateWav} from './audio-wav.mjs';
import {audioPorts,audioPath,capabilitySchema,rejectBeforeWrite} from './audio-ports.mjs';
import {residentAudio} from './audio-cli.mjs';

const wav=(seconds=10)=>{
 const rate=8000,n=Math.round(rate*seconds)*2,b=Buffer.alloc(44+n);
 b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVE',8);b.write('fmt ',12);b.writeUInt32LE(16,16);
 b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(rate,24);b.writeUInt32LE(rate*2,28);
 b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(n,40);
 return b;
};
const caption='A breathy daegeum introduces a quiet motif. Piano answers softly before a resolved cadence.';
const result=(audio=wav(),bpm=80)=>({wav:audio,bpm,bpmBasis:bpm===null?'unavailable':'model'});
const source={conceptId:'sound-category',sectionSha256:'ba'.repeat(32),title:'사운드 기준',content:'### 음악\n피아노와 대금'};
function fixture(t){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-audio-')));
 const dbPath=join(dir,'work.sqlite'),store=new PlannerStore(dbPath),db=store.db;
 chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});
 let sequence=0;
 function add({ack=true,durationSeconds=10,text='',conceptText,lyricsRequest,targetDurationSeconds}={}){
  sequence++;
  const payload={schemaVersion:'ngs.audio-generate.v1',projectId:'binari',kind:'bgm',conceptId:conceptText===undefined?source.conceptId:'direct',
   title:'새 음원',brief:'짧고 조용하게',text,durationSeconds,...(conceptText===undefined?{}:{conceptText}),...(lyricsRequest===undefined?{}:{lyricsRequest}),...(targetDurationSeconds===undefined?{}:{targetDurationSeconds})};
  const row={officialRequestId:'request.official.'+sha('registered'+sequence),registeredAt:sequence+1000,
   projectId:'binari',requestType:'audio.generate',schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),status:'registered'};
  store.stage(row);
  if(ack)store.acknowledge(row.officialRequestId);
  const taskId=buildPlan(row).tasks[0].id,{officialRequestId}=row;
  const get=()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
  const run=(extra={})=>runAudioFromPath({dbPath,taskId,officialRequestId,pds:{read:async()=>source,publish:async()=>({ok:true,resultRef:audioResultRef('binari',officialRequestId)})},
   adapters:{bgm:async()=>result()},composeCaption:async()=>caption,...extra});
  return {row,get,run,ids:{taskId,officialRequestId}};
 }
 return {db,dbPath,store,add};
}

test('BGM reads exact claim, invokes HQ port once, publishes one WAV with model BPM',async t=>{
 const {add}=fixture(t),f=add();let calls=0,reads=0,writes=0;
 const resultRef=audioResultRef('binari',f.row.officialRequestId);
 const res=await f.run({
  pds:{
   read:async claim=>{
    reads++;assert.deepEqual(claim,{...f.ids,projectId:'binari',payloadSha256:f.row.payloadSha256,claimStamp:f.get().updated_at});
    assert.equal(f.get().status,'running');return source;
   },
   publish:async(claim,s,a)=>{
    writes++;assert.equal(claim.taskId,f.ids.taskId);
    assert.deepEqual(s,{conceptId:'sound-category',sectionSha256:'ba'.repeat(32)});
    assert.deepEqual(a,{sha256:sha(wav()),mimeType:'audio/wav',bytes:wav().length,base64:wav().toString('base64'),bpm:80});
    return {ok:true,resultRef};
   },
  },
  adapters:{bgm:async input=>{
   calls++;assert.equal(input.kind,'bgm');assert.equal(input.title,'새 음원');
   assert.equal(input.prompt,caption);
   assert(!('text' in input));assert.equal(input.durationSeconds,10);
   return result();
  }},
 });
 assert.deepEqual(res,{state:'completed',taskId:f.ids.taskId,resultRef});assert.equal(f.get().result_ref,resultRef);
 assert.equal(f.get().retry_count,0);assert.deepEqual([reads,calls,writes],[1,1,1]);
 assert.equal((await f.run()).state,'not_claimable');
});

test('missing model BPM stays null; worker does not send a guessed or requested tempo',async t=>{
 const {add}=fixture(t),f=add();
 const resultRef=audioResultRef('binari',f.row.officialRequestId);
 assert.equal((await f.run({adapters:{bgm:async()=>result(wav(),null)},pds:{
  read:async()=>({...source,content:'### 컨셉 지시로 95 BPM'}),
  publish:async(claim,source,artifact)=>{assert.equal(artifact.bpm,null);return {ok:true,resultRef};},
 }})).state,'completed');
});

test('legacy non-BGM tasks are not polled, no other adapter can be configured; ack is required',async t=>{
 const {dbPath,db,store,add}=fixture(t),a=add({ack:false}),prior=add(),b=add();
 db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?').run('{"conceptId":"sound-category","kind":"effect"}',prior.ids.taskId);
 assert.equal((await a.run()).state,'not_claimable');
 assert.deepEqual({...nextAudioFromPath(dbPath)},b.ids);
 assert.equal((await b.run({adapters:{}})).state,'provider_unavailable');
 await assert.rejects(b.run({adapters:{effect:()=>{}}}),/AUDIO_PORTS_REQUIRED/);
 assert.equal(b.get().status,'pending');assert.equal(b.get().retry_count,0);
 store.acknowledge(a.row.officialRequestId);
 assert.equal((await a.run()).state,'completed');
});

test('tampered task input or official payload never starts a model',async t=>{
 const {add,db}=fixture(t),f=add();
 db.prepare("UPDATE workflow_tasks SET task_input='{}' WHERE task_id=?").run(f.ids.taskId);
 await assert.rejects(f.run(),/AUDIO_TASK_BINDING_INVALID/);assert.equal(f.get().status,'pending');
 db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?').run(canonical({kind:'bgm',conceptId:source.conceptId}),f.ids.taskId);
 db.prepare("UPDATE workflow_runs SET payload='{}' WHERE official_request_id=?").run(f.ids.officialRequestId);
 await assert.rejects(f.run(),/NGS_AUDIO_GENERATE_PAYLOAD_INVALID/);assert.equal(f.get().status,'pending');
});

test('selected active master must return same concept only; source/read failure precedes adapter',async t=>{
 const {add}=fixture(t),f=add();let n=0;
 const s=await f.run({pds:{read:async()=>({...source,conceptId:'other'}),publish:()=>n++},adapters:{bgm:()=>n++}});
 assert.equal(s.state,'failed');assert.equal(s.code,'AUDIO_MASTER_READ_FAILED');assert.equal(n,0);assert.equal(f.get().status,'failed');
});

test('missing/non-WAV/multi-candidate output cannot reach PDS',async t=>{
 const {add}=fixture(t);
 for(const bad of [null,[wav(),wav()],Buffer.from('RIFF...'),Buffer.concat([wav(),wav()]),wav(9.5)]){
  const f=add();let writes=0;
  assert.equal((await f.run({adapters:{bgm:async()=>result(bad)},pds:{read:async()=>source,publish:()=>writes++}})).code,'AUDIO_WAV_INVALID');
  assert.equal(writes,0);assert.equal(f.get().status,'failed');
 }
});

test('publication transport lost / incorrect canonical ref preserves running; definitive prewrite failure permits retry',async t=>{
 const {add}=fixture(t);
 for(const error of [false,true]){
  const f=add(),r=await f.run({pds:{read:async()=>source,publish:async()=>{
   if(error)throw Error('secret unknowable');
   return {ok:true,resultRef:'canonical-files/different'};
  }}});
  assert.equal(r.state,'requires_reconciliation');assert.equal(f.get().status,'running');
  assert.equal((await f.run()).state,'not_claimable');
 }
 const e=add();
 const r=await e.run({pds:{read:async()=>source,publish:async()=>{throw new AudioDefinitelyRejected;}}});
 assert.equal(r.state,'failed');assert.equal(e.get().status,'failed');
});

test('exact aged failure retries once; delay and cap match common worker semantics',async t=>{
 const {add,db}=fixture(t),f=add();
 assert.equal((await f.run({adapters:{bgm:async()=>{throw Error('private prompt');}}})).state,'failed');
 assert.equal((await f.run()).state,'not_claimable');
 const age=()=>db.prepare("UPDATE workflow_tasks SET updated_at=CAST(unixepoch('subsec')*1000 AS INTEGER)-600001 WHERE task_id=?").run(f.ids.taskId);
 for(let retry=1;retry<=3;retry++){
  age();
  assert.equal((await f.run({adapters:{bgm:async()=>{throw Error('private');}}})).state,'failed');
  assert.equal(f.get().retry_count,retry);
 }
 age();assert.equal((await f.run()).state,'not_claimable');
});

test('cancellation before model or publication cannot send uncontrolled writes',async t=>{
 const {add}=fixture(t),f=add(),ac=new AbortController();
 const pre=await f.run({signal:ac.signal,pds:{read:async()=>{ac.abort();return source;},publish:()=>assert.fail('publish')}});
 assert.equal(pre.code,'AUDIO_CANCELLED');assert.equal(f.get().status,'failed');
});

test('resident not ready does not claim, unsupported provider can stay pending',async t=>{
 const {add,dbPath}=fixture(t),f=add(),lines=[],ac=new AbortController();
 await residentAudio({dbPath,pds:{capability:async()=>false},adapters:{bgm:async()=>wav()},signal:ac.signal,log:s=>lines.push(s),
  pause:async()=>ac.abort()});
 assert.equal(f.get().status,'pending');assert.deepEqual(lines,['{"service":"audio-worker","state":"not_ready"}']);
});

test('ports use only exact loopback readiness, claim, publication; only prewrite code means definitive reject',async()=>{
 let status=422,value={error:rejectBeforeWrite};const calls=[];
 const pds=audioPorts({transport:async call=>{
  calls.push(call);
  return call.method==='GET'?{status:200,value:{enabled:true,schemaVersion:capabilitySchema}}:{status,value};
 }});
 assert.equal(await pds.capability(),true);
 await assert.rejects(pds.publish({a:1},{b:2},{c:3}),AudioDefinitelyRejected);
 assert.deepEqual(calls[1].body,{claim:{a:1},source:{b:2},artifact:{c:3}});assert.equal(calls[1].path,audioPath);
 value={error:rejectBeforeWrite,extra:true};await assert.rejects(pds.publish({}, {}, {}),/AUDIO_PUBLICATION_UNCERTAIN/);
 status=500;value={error:rejectBeforeWrite};await assert.rejects(pds.publish({}, {}, {}),/AUDIO_PUBLICATION_UNCERTAIN/);
});

test('unreliable BPM or an invalid adapter outcome is rejected before PDS write',async t=>{
 const {add}=fixture(t);
 for(const invalid of [wav(),{wav:wav(),bpm:60,bpmBasis:'measured'},
  {wav:wav(),bpm:0,bpmBasis:'model'},{wav:wav(),bpm:null,bpmBasis:'model'}, {wav:wav(),bpm:99.8,bpmBasis:'model'}]){
  const f=add();
  assert.equal((await f.run({adapters:{bgm:async()=>invalid},pds:{read:async()=>source,publish:()=>assert.fail('untrusted result')}})).code,
               'AUDIO_MODEL_METADATA_INVALID');
  assert.equal(f.get().status,'failed');
 }
});

test('WAV container rejects invalid PCM header, multiple data chunks, trailer and wrong duration',()=>{
 const altered=wav();altered.writeUInt16LE(24,34);
 const dup=Buffer.concat([wav(),wav().subarray(36)]);dup.writeUInt32LE(dup.length-8,4);
 for(const v of [altered,dup,Buffer.concat([wav(),Buffer.from('x')]),wav(3)])
  assert.throws(()=>validateWav(v,'bgm',10),/AUDIO_WAV_INVALID/);
});

test('Codex sees the selected source and brief; only its prose reaches ACE-Step',async t=>{
 const {add}=fixture(t),f=add();let steps=[];
 const content=source.content+'\n'+ '가'.repeat(2500);
 const r=await f.run({pds:{read:async()=>({...source,content}),publish:async()=>{
  steps.push('publish');return {ok:true,resultRef:audioResultRef('binari',f.row.officialRequestId)};
 }},composeCaption:async data=>{
  steps.push('caption');assert.equal(data.masterConcept.content,content);
  assert.equal(data.request.brief,'짧고 조용하게');return caption;
 },adapters:{bgm:async input=>{steps.push('ace');assert.equal(input.prompt,caption);return result();}}});
 assert.equal(r.state,'completed');assert.deepEqual(steps,['caption','ace','publish']);
});
test('invalid Codex output fails the task without rendering or publishing raw source',async t=>{
 const {add}=fixture(t),f=add();
 const r=await f.run({composeCaption:async()=>source.content,
  adapters:{bgm:()=>assert.fail('render')}});
 assert.equal(r.code,'AUDIO_CAPTION_FAILED');assert.equal(r.providerPhase,'caption');
 assert.equal(r.providerCode,'AUDIO_CAPTION_OUTPUT_INVALID');assert.equal(f.get().status,'failed');
});

test('auto duration forwards exact humming and publishes only length-validated PCM',async t=>{
 const {add}=fixture(t),text='[Humming]\n음— 음—',f=add({durationSeconds:null,text});let called=0;
 const run=await f.run({adapters:{bgm:async input=>{
  called++;assert.equal(input.durationSeconds,null);assert.equal(input.text,text);
  assert.equal(input.prompt,caption);return result(wav(73));
 }}});
 assert.equal(run.state,'completed');assert.equal(called,1);
 for(const duration of [9.9,120.1]){
  const wrong=add({durationSeconds:null});
  const bad=await wrong.run({adapters:{bgm:async()=>result(wav(duration))}});
  assert.equal(bad.code,'AUDIO_WAV_INVALID');
 }
});

test('resident loop retains one private bridge for full caption/render/publish and awaits its shutdown',async t=>{
 const {audioLifecycle}=await import('./adapters/provider/audio-resident-bridge.mjs');
 const {add,dbPath}=fixture(t),first=add(),second=add();
 const events=[],ctr=new AbortController;
 const adapters={bgm:async()=>{events.push('render');return result();}};
 Object.defineProperty(adapters,audioLifecycle,{value:{
  beginTask(){events.push('begin');},endTask(){events.push('end');},async close(){
   await Promise.resolve();events.push('closed');
  },
 }});
 const pds={capability:async()=>true,heartbeat:async()=>true,read:async()=>{events.push('read');return source;},
  publish:async claim=>{events.push('publish');return {ok:true,resultRef:audioResultRef('binari',claim.officialRequestId)};}};
 let pauses=0;
 await residentAudio({dbPath,pds,adapters,signal:ctr.signal,composeCaption:async()=>{events.push('caption');return caption;},log:()=>{},
  pause:async()=>{if(++pauses===2)ctr.abort();}});
 assert.equal(first.get().status,'completed');assert.equal(second.get().status,'completed');
 assert.deepEqual(events,[...Array(2)].flatMap(()=>['begin','read','caption','render','publish','end']).concat('closed'));
});

test('full-length target preserves literal lyrics and publishes only results of at least 60 seconds',async t=>{
 const {add}=fixture(t);
 assert.throws(()=>add({durationSeconds:10,targetDurationSeconds:60}),/NGS_AUDIO_GENERATE_PAYLOAD_INVALID/);
 assert.throws(()=>add({durationSeconds:null,targetDurationSeconds:59}),/NGS_AUDIO_GENERATE_PAYLOAD_INVALID/);
 const text='  첫 줄\n둘째 줄  \n';
 for(const seconds of [52,60]){
  const f=add({durationSeconds:null,targetDurationSeconds:60,text});
  let published=0;
  const run=await f.run({pds:{read:async()=>source,publish:async()=>{
   published++;return {ok:true,resultRef:audioResultRef('binari',f.row.officialRequestId)};
  }},adapters:{bgm:async input=>{
   assert.equal(input.targetDurationSeconds,60);
   assert.equal(input.durationSeconds,null);assert.equal(input.text,text);
   return result(wav(seconds));
  }}});
  if(seconds===52){
   assert.equal(run.state,'failed');assert.equal(run.code,'AUDIO_FULL_DURATION_NOT_MET');
   assert.equal(f.get().status,'failed');assert.equal(f.get().result_ref,null);assert.equal(published,0);
  }else{
   assert.equal(run.state,'completed');assert.equal(published,1);
   assert.deepEqual([run.targetDurationSeconds,run.actualDurationSeconds,run.targetReached],[60,60,true]);
  }
 }
});


test('direct concept binds only exact registered text and original supplied lyric bytes',async t=>{
 const {add}=fixture(t),conceptText='달과 강\n물가',text='  이어서 부르는 노래\n둘째 줄  \n';
 const f=add({durationSeconds:null,text,conceptText});
 const direct={conceptId:'direct',title:'직접 입력',content:conceptText,sectionSha256:sha(conceptText)};
 const output=await f.run({pds:{read:async()=>direct,publish:async(_claim,binding)=>{
  assert.deepEqual(binding,{conceptId:'direct',sectionSha256:direct.sectionSha256});
  return {ok:true,resultRef:audioResultRef('binari',f.row.officialRequestId)};
 }},
 composeLyrics:async()=>{assert.fail('literal input must not reach lyric composer');},
 composeCaption:async b=>{assert.deepEqual(b.masterConcept,{title:direct.title,content:conceptText});return caption;},
 adapters:{bgm:async input=>{assert.equal(input.text,text);return result();}}});
 assert.equal(output.state,'completed');
});

test('generated Korean lyrics are passed to BGM unmodified and never replaced with a caption',async t=>{
 const {add}=fixture(t),lyricsRequest='흐르는 강에 대한 노랫말';
 const f=add({durationSeconds:null,lyricsRequest}),lyrics='  달이 비추는 강\n살며시 흘러가네  \n';
 let invoked=0;
 const output=await f.run({composeLyrics:async b=>{
  invoked++;assert.equal(b.lyricsRequest,lyricsRequest);assert.equal(b.masterConcept.content,source.content);return lyrics;
 },composeCaption:async b=>{assert.match(b.request.vocalMode,/vocal text/);assert(!JSON.stringify(b).includes(lyrics));return caption;},
 adapters:{bgm:async input=>{assert.equal(input.text,lyrics);assert.equal(input.prompt,caption);return result();}}});
 assert.equal(output.state,'completed');assert.equal(invoked,1);
});

test('invalid lyric output fails before caption, render or publish',async t=>{
 const {add}=fixture(t),f=add({durationSeconds:null,lyricsRequest:'새벽의 노랫말'});
 const output=await f.run({composeLyrics:async()=> 'This is not Korean',
  composeCaption:async()=>assert.fail('should not compose caption'),
  adapters:{bgm:async()=>assert.fail('should not render')}});
 assert.equal(output.code,'AUDIO_LYRIC_FAILED');assert.equal(output.providerCode,'AUDIO_LYRIC_OUTPUT_INVALID');
 assert.equal(f.get().status,'failed');
});
