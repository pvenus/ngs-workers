import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {lstat,mkdtemp,readFile,readdir,realpath,rm,symlink,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {createPlanningFolderDownloader} from './planning-folders.mjs';

const npcId='npc.ch1_dajeong',a='binari/concept',b='binari/npcs/'+npcId+'/planning';
const records={
 [a]:[['metadata.json',Buffer.from('{}')],['sections/design.md',Buffer.alloc(1_048_592,7)],['sections/expression/톤.md',Buffer.from('담백')]],
 [b]:[['대표.md',Buffer.from('대표')],['empty.json',Buffer.alloc(0)]],
};
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function fixture(records){
 const calls=[];
 async function call(tool,args){
  calls.push({tool,args});
  const current=records[args.scope];
  assert.ok(current);
  if(tool==='npc_folders_compare'){
   assert.deepEqual(args.files,[]);
   const groups=Object.create(null);
   for(const [path,bytes] of current){
    const folder=path.includes('/')?path.split('/')[0]:'';
    (groups[folder]??=[]).push({path,sha256:hash(bytes),size:bytes.length});
   }
   return {scope:args.scope,groups:Object.entries(groups).map(([folder,files])=>({folder,files}))};
  }
  assert.equal(tool,'npc_folder_file_chunk');
  const bytes=current.find(row=>row[0]===args.path)[1];
  assert.equal(args.sha256,hash(bytes));
  const end=Math.min(args.offset+1024*1024,bytes.length);
  return {...args,size:bytes.length,base64:bytes.subarray(args.offset,end).toString('base64'),nextOffset:end===bytes.length?null:end};
 }
 return {call,calls};
}
async function parent(t){
 const p=await realpath(await mkdtemp(join(tmpdir(),'planning-fixture-')));
 t.after(()=>rm(p,{force:true,recursive:true}));return p;
}
async function server(t,call){
 const listener=createServer(async(req,res)=>{
  try{
   assert.equal(req.method,'POST');assert.equal(req.url,'/mcp');
   const payload=JSON.parse(Buffer.concat(await Array.fromAsync(req)).toString());
   if(payload.method==='notifications/initialized'){res.writeHead(202).end();return;}
   const result=payload.method==='initialize'?{protocolVersion:'2025-06-18',capabilities:{tools:{}}}
    :{structuredContent:await call(payload.params.name,payload.params.arguments)};
   res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({jsonrpc:'2.0',id:payload.id,result}));
  }catch{res.writeHead(500).end();}
 });
 t.after(()=>{listener.closeAllConnections();listener.close();});
 listener.listen(0,'127.0.0.1');await once(listener,'listening');
 return 'http://127.0.0.1:'+listener.address().port+'/mcp';
}
test('downloads both exact PDS scopes with actual MCP transport, verifies all files and removes private scratch',async t=>{
 const tempParent=await parent(t),{call,calls}=fixture(records),url=await server(t,call);
 const result=await createPlanningFolderDownloader({url,tempParent})({npcId},async({root,files})=>{
  assert.equal((await lstat(root)).mode&0o777,0o700);
  assert.deepEqual(files.map(({scope,path})=>[scope,path]),[
   [a,'metadata.json'],[a,'sections/design.md'],[a,'sections/expression/톤.md'],[b,'empty.json'],[b,'대표.md']]);
  for(const {scope,path,file,sha256,size} of files){
   const data=await readFile(file);assert.equal(data.length,size);assert.equal(hash(data),sha256);
   assert.equal(await realpath(file),join(root,scope===a?'concept':'planning',path));
   assert.equal((await lstat(file)).mode&0o777,0o600);
  }
  return 42;
 });
 assert.equal(result,42);assert.deepEqual(await readdir(tempParent),[]);
 assert.deepEqual(calls.filter(row=>row.tool==='npc_folders_compare').map(row=>row.args.scope),[a,b,a,b]);
 assert.deepEqual(calls.filter(row=>row.args.path==='sections/design.md').map(row=>row.args.offset),[0,1024*1024]);
});
test('never hands off a stale inventory, invalid chunks, or hidden/traversing paths',async t=>{
 const tempParent=await parent(t);
 for(const variation of ['changed','digest','offset','zero-progress','hidden','traversal']){
  const {call}=fixture(records);let scans=0;
  const wrapped=async(tool,args)=>{
   const row=await call(tool,args);
   if(tool==='npc_folders_compare'&&args.scope===a){
    scans++;
    if(scans===2&&variation==='changed')row.groups[0].files[0].sha256='a'.repeat(64);
    if(scans===1&&variation==='hidden')row.groups[0].files[0].path='.deleted';
    if(scans===1&&variation==='traversal')row.groups[0].files[0].path='../outside';
   }
   if(tool==='npc_folder_file_chunk'&&args.path==='metadata.json'){
    if(variation==='digest')row.base64=Buffer.from('[]').toString('base64');
    if(variation==='offset')row.nextOffset=0;
    if(variation==='zero-progress'){row.base64='';row.nextOffset=0;}
   }
   return row;
  };
  await assert.rejects(createPlanningFolderDownloader({call:wrapped,tempParent})({npcId},()=>assert.fail('must not hand off')),
   /PLANNING_FOLDER_(SNAPSHOT_CHANGED|DIGEST_MISMATCH|RESPONSE_INVALID)/);
  assert.deepEqual(await readdir(tempParent),[]);
 }
});
test('rejects a local symlink and cleans scratch even when callback throws',async t=>{
 const tempParent=await parent(t),outside=join(tempParent,'outside.json');await writeFile(outside,'preserved');
 const {call}=fixture(records);
 const symlinkMcp=async(tool,args)=>{
  if(tool==='npc_folder_file_chunk'&&args.path==='metadata.json'){
   const folder=(await readdir(tempParent)).find(name=>name.startsWith('workflow-planning-folders-'));
   await symlink(outside,join(tempParent,folder,'concept','sections'));
  }
  return await call(tool,args);
 };
 await assert.rejects(createPlanningFolderDownloader({call:symlinkMcp,tempParent})({npcId},()=>assert.fail('unsafe handoff')),
  /PLANNING_FOLDER_LOCAL_PATH_INVALID/);
 assert.equal(await readFile(outside,'utf8'),'preserved');
 await assert.rejects(createPlanningFolderDownloader({call,tempParent})({npcId},()=>{throw Error('callback abort');}),/callback abort/);
 assert.deepEqual(await readdir(tempParent),['outside.json']);
 await assert.rejects(createPlanningFolderDownloader({call,tempParent})({npcId:'npc.foo/../bar'},()=>{}),/TARGET_INVALID/);
});

test('rejects media in either manifest before any file chunks are requested',async t=>{
 const tempParent=await parent(t);
 for(const scope of [a,b]){
  const {call,calls}=fixture({...records,[scope]:[...records[scope],['nested/media.PNG',Buffer.from('must not read')]]});
  await assert.rejects(createPlanningFolderDownloader({call,tempParent})({npcId},()=>assert.fail('must not hand off')),
   /PLANNING_FOLDER_MEDIA_NOT_ALLOWED/);
  assert.equal(calls.some(row=>row.tool==='npc_folder_file_chunk'),false);
  assert.deepEqual(await readdir(tempParent),[]);
 }
});
