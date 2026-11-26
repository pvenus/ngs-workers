import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,mkdtempSync,readFileSync,realpathSync,rmSync,unlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import {createDocumentSlot,documentSlotIdentity} from './document-slot.mjs';
import {localPorts,heartbeatPath} from './local-ports.mjs';

test('document slots isolate process scratch, reject duplicate admission, and drain independently',t=>{
 const base=realpathSync(mkdtempSync(join(tmpdir(),'document-worker-slots-test-')));
 t.after(()=>rmSync(base,{recursive:true,force:true}));
 const first=createDocumentSlot(undefined,{base}),second=createDocumentSlot('2',{base}),duplicate=createDocumentSlot('1',{base});
 assert.equal(first.serverName,'document-worker');
 assert.equal(second.serverName,'document-worker-2');
 assert.equal(dirname(first.tmp),join(base,'1','scratch'));
 assert.equal(dirname(second.tmp),join(base,'2','scratch'));
 assert.notEqual(first.tmp,duplicate.tmp);
 assert.throws(()=>documentSlotIdentity('3'),/DOCUMENT_WORKER_SLOT_INVALID/);
 const release=first.tryEnter();
 assert.equal(typeof release,'function');
 assert.equal(duplicate.tryEnter(),null);
 assert.equal(readFileSync(join(base,'1','active'),'utf8'), process.pid+'\n');
 const pause=join(base,'1','pause');
 writeFileSync(pause,'');
 const releaseSecond=second.tryEnter();
 assert.equal(typeof releaseSecond,'function');
 assert.ok(existsSync(join(base,'1','active')));
 release();release();
 assert.equal(duplicate.tryEnter(),null);
 assert.ok(!existsSync(join(base,'1','active')));
 assert.ok(existsSync(join(base,'2','active')));
 unlinkSync(pause);
 duplicate.tryEnter()();
 releaseSecond();
 first.cleanup();
 assert.ok(!existsSync(first.tmp));
 assert.ok(existsSync(duplicate.tmp));
 assert.ok(existsSync(second.tmp));
});

test('OPS heartbeat identity is separately scoped to exactly the two document services',async()=>{
 const calls=[],pds=localPorts({transport:async opts=>{calls.push(opts);return {ok:true};}});
 assert.equal(await pds.heartbeat(),true);
 assert.equal(await pds.heartbeat(undefined,'document-worker-2'),true);
 assert.equal(await pds.heartbeat(undefined,'document-worker-3'),false);
 assert.deepEqual(calls.map(({path,body})=>[path,body.serverName]),[
  [heartbeatPath,'document-worker'],[heartbeatPath,'document-worker-2'],
 ]);
});
