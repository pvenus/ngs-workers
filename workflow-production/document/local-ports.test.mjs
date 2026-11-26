import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import test from 'node:test';
import {localPorts,requestLocalJson,npcPlanningReadDeadlineMs} from './local-ports.mjs';

test('NPC planning snapshot reads have bounded 30-second deadlines without relaxing capability or publication',async()=>{
 const calls=[],pds=localPorts({transport:async call=>{calls.push(call);return {};}});
 const command={officialRequestId:'request',taskId:'task',projectId:'binari',npcId:'npc.test',payloadSha256:'x',artifact:'{}'};
 for(const stage of ['Overview','PortraitArt','Representative','Idle','Combat'])await pds['read'+stage](command,100);
 assert.equal(calls.length,5);
 assert.ok(calls.every(x=>x.deadline===30_000&&x.deadline===npcPlanningReadDeadlineMs&&x.npcPlanningRead===true
  &&x.path.endsWith('/read')&&x.body.inputMode==='folder-snapshot'));
 await pds.capability(undefined,'npc.plan-overview');
 await pds.publishOverview(command,100,{});
 assert.deepEqual(calls.slice(-2).map(x=>[x.deadline,x.npcPlanningRead]),[[5000,undefined],[120000,undefined]]);
});

test('only a planning PDS deadline reports its fixed code; other local transport stays generic',async t=>{
 const server=createServer(()=>{});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 const params={port:server.address().port,path:'/read',method:'POST',body:{},deadline:20};
 await assert.rejects(requestLocalJson({...params,npcPlanningRead:true}),{message:'NPC_PLANNING_PDS_READ_TIMEOUT'});
 await assert.rejects(requestLocalJson(params),{message:'DOCUMENT_WORKER_LOCAL_TRANSPORT',pdsTransportKind:'deadline_exceeded'});
});

test('only the exact preset rejection proof on the intended HTTP route permits ordinary failure',async t=>{
 let data;
 const server=createServer((req,res)=>{
  req.resume();res.writeHead(409,{'Content-Type':'application/json'}).end(JSON.stringify(data));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 const args={port:server.address().port,path:'/v1/npcs/ownership/workflow-preset',method:'POST',body:{},deadline:2000,presetPublication:true};
 data={error:'WORKFLOW_PRESET_REJECTED_BEFORE_WRITE'};
 await assert.rejects(requestLocalJson(args),{message:'WORKFLOW_PRESET_REJECTED_BEFORE_WRITE'});
 await assert.rejects(requestLocalJson({...args,path:'/unrelated'}),{message:'DOCUMENT_WORKER_LOCAL_TRANSPORT',pdsTransportKind:'http_error',pdsHttpStatus:409});
 data={error:'WORKFLOW_PRESET_NOT_CONFIRMED'};
 await assert.rejects(requestLocalJson(args),{message:'DOCUMENT_WORKER_LOCAL_TRANSPORT'});
});

test('closed port is unsent; an accepted request with a broken ACK remains ambiguous',async t=>{
 const peer=createServer((req,res)=>{req.resume();req.on('end',()=>res.destroy());});
 await new Promise(resolve=>peer.listen(0,'127.0.0.1',resolve));
 const port=peer.address().port;
 const args={port,path:'/v1/npcs/ownership/workflow-plan-overview',method:'POST',body:{},deadline:1500};
 await assert.rejects(requestLocalJson(args),{message:'DOCUMENT_WORKER_LOCAL_TRANSPORT',pdsTransportKind:'connection_reset'});
 await new Promise(resolve=>peer.close(resolve));
 await assert.rejects(requestLocalJson(args),{message:'DOCUMENT_PDS_CONNECT_REFUSED_BEFORE_SEND'});
});
