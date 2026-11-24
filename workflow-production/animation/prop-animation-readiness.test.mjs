import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,rm,readFile,lstat,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {realpath} from 'node:fs/promises';
import {propAnimationReadiness} from './prop-animation-readiness.mjs';

test('private readiness timestamp follows an actual Q8 probe and is removed on unknown or shutdown',async t=>{
 const dir=await realpath(await mkdtemp(join(tmpdir(),'prop-animation-ready-')));
 t.after(()=>rm(dir,{recursive:true,force:true}));
 const path=join(dir,'prop-animation-ready.json');
 let checked=0,known=true;
 const lease=propAnimationReadiness({path,now:()=>456,assertKnown(){if(!known)throw Error();},
  probe:async()=>{checked++;return {ready:true,model:'wan2.2-ti2v-5b',quantization:'q8_0'};}});
 assert.deepEqual(await Promise.all([lease.refresh(),lease.refresh()]),[true,true]);assert.equal(checked,1);
 assert.deepEqual(JSON.parse(await readFile(path)),{schemaVersion:'workflow.prop-animation-renderer-ready.v1',provider:'wan-ti2v-q8_0',checkedAt:456});
 assert.equal((await lstat(path)).mode&0o777,0o600);
 known=false;
 assert.equal(await lease.refresh(),false);
 await assert.rejects(lstat(path),{code:'ENOENT'});
 assert.equal(lease.ready(),false);
 known=true;assert.equal(await lease.refresh(),true);
 await lease.stop();await assert.rejects(lstat(path),{code:'ENOENT'});
 assert.equal(await lease.refresh(),false);
 await symlink('/private/no-writer-path',path);checked=0;
 const redirected=propAnimationReadiness({path,assertKnown(){},probe:async()=>{checked++;return {};}});
 assert.equal(await redirected.refresh(),false);assert.equal(checked,0);
 assert.equal((await lstat(path)).isSymbolicLink(),true);
});

test('older prop proof cannot be used as cinematic readiness; encoder probe failure only clears the cinematic proof',async t=>{
 const {cinematicAnimationReadiness}=await import('./prop-animation-readiness.mjs');
 const dir=await realpath(await mkdtemp(join(tmpdir(),'cinematic-animation-ready-')));
 t.after(()=>rm(dir,{recursive:true,force:true}));
 const propPath=join(dir,'prop-animation-ready.json'),path=join(dir,'cinematic-animation-ready.json');
 const probe=async()=>({ready:true,model:'wan2.2-ti2v-5b',quantization:'q8_0'});
 const prop=propAnimationReadiness({path:propPath,now:()=>42,assertKnown(){},probe});
 const wrong=cinematicAnimationReadiness({path:propPath,now:()=>42,assertKnown(){},probe});
 assert.equal(await prop.refresh(),true);assert.equal(await wrong.refresh(),false);
 assert.equal((await lstat(propPath)).isFile(),true);
 let encoder=true;
 const cinematic=cinematicAnimationReadiness({path,now:()=>42,assertKnown(){},probe:async()=>{
  if(!encoder)throw Error('NO_ENCODER');
  return probe();
 }});
 assert.equal(await cinematic.refresh(),true);
 assert.deepEqual(JSON.parse(await readFile(path)),{schemaVersion:'workflow.cinematic-animation-renderer-ready.v1',provider:'wan-ti2v-q8_0',checkedAt:42});
 encoder=false;assert.equal(await cinematic.refresh(),false);
 await assert.rejects(lstat(path),{code:'ENOENT'});assert.equal((await lstat(propPath)).isFile(),true);
});
