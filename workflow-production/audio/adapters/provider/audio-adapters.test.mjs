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
 assert.deepEqual(Object.keys(adapters),['bgm']);
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
