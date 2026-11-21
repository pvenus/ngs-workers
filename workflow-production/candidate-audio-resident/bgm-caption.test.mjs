import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createCaptionComposer,validateCaption,guidance} from './bgm-caption.mjs';

const caption='A soft piano answers a breathy daegeum motif, and the duet ends on a warm cadence.';
const data={masterConcept:{title:'음원',content:'동양적 정서'},request:{brief:'대금 독주 후 피아노가 응답',durationSeconds:null,vocalMode:'instrumental'}};
test('caption rules permit bounded additions and preserve requested musical constraints',()=>{
 assert.match(guidance,/add a few compatible production choices/);
 assert.match(guidance,/Do not override explicit instrumentation/);
 assert.match(guidance,/Do not prescribe a length when the request is automatic/);
 assert.equal(validateCaption(caption),caption);
 for(const v of [null,'한국어 Only', '# heading', 'Two\nparagraphs', '[test](https://example.org)', 'a'.repeat(2001)])
  assert.throws(()=>validateCaption(v),{message:'AUDIO_CAPTION_OUTPUT_INVALID'});
});
test('Codex protocol passes only a structured caption from a finished tool-free turn', async t=>{
 const root=await mkdtemp(join(tmpdir(),'ngs-bgm-cli-test-'));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const writeCLI=async (name,events)=>{
  const command=join(root,name);
  await writeFile(command,'#!/bin/sh\ncat >/dev/null\n'+events.map(event=>'printf \'%s\\n\' \''+JSON.stringify(event)+'\'').join('\n')+'\n',{mode:0o700});
  return createCaptionComposer({codexPath:command});
 };
 const message={type:'item.completed',item:{type:'agent_message',text:JSON.stringify({caption})}};
 const completed={type:'turn.completed'};
 const clean=await writeCLI('clean',[message,completed]);
 assert.equal(await clean(data),caption);
 for(const [name,events] of [
  ['missing-completion',[message]],
  ['tool',[{type:'item.completed',item:{type:'command_execution'}},message,completed]],
  ['extra-key',[{type:'item.completed',item:{type:'agent_message',text:JSON.stringify({caption,duration:42})}},completed]],
 ]) {
  const bad=await writeCLI(name,events);
  await assert.rejects(bad(data),{message:'AUDIO_CAPTION_OUTPUT_INVALID'});
 }
});
