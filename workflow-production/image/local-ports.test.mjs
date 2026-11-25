import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once,EventEmitter} from 'node:events';
import test from 'node:test';
import {armConnectionDeadline} from './connection-deadline.mjs';
import {localPorts,requestLocalJson} from './local-ports.mjs';

test('only NPC portrait basis uses a connection deadline with bounded bytes',async()=>{
 const calls=[],pds=localPorts({transport:async call=>{calls.push(call);return {};}});
 const command={officialRequestId:'request',taskId:'task',projectId:'binari',npcId:'npc.test',payloadSha256:'x',operation:'npc.portrait-representative',artifact:'{}'};
 const {signal}=new AbortController();
 await pds.readPortrait(command,100,signal);
 await pds.readCinematicSource({},signal);
 await pds.publishPortrait(command,100,{},signal);
 assert.deepEqual(calls.map(c=>[c.deadline,c.portraitRead,c.responseLimit]),[
  [5000,true,12*1024*1024],[5000,undefined,undefined],[120000,undefined,undefined]]);
 assert.equal(calls[0].signal,signal);
});
test('portrait connection setup remains bounded',t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const req=new EventEmitter(),code=Error('connect');let error;
 req.destroy=e=>{error=e;};
 armConnectionDeadline(req,5000,code);
 req.emit('socket',Object.assign(new EventEmitter(),{connecting:true}));
 t.mock.timers.tick(4999);assert.equal(error,undefined);
 t.mock.timers.tick(1);assert.equal(error,code);
});
test('portrait basis survives 130 seconds after connecting, remains cancelable and bounded',async t=>{
 const responses=new EventEmitter();
 const server=createServer((_,res)=>responses.emit('opened',res));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
 const params={port:server.address().port,path:'/read',method:'POST',body:{},deadline:5000,portraitRead:true};
 t.mock.timers.enable({apis:['setTimeout']});
 let opened=once(responses,'opened');
 const result=requestLocalJson(params);
 const [res]=await opened;
 t.mock.timers.tick(130_000);
 res.writeHead(200,{'Content-Type':'application/json'});res.end('{"ok":true}');
 assert.deepEqual(await result,{ok:true});
 const stop=new AbortController();
 opened=once(responses,'opened');
 const canceled=assert.rejects(requestLocalJson({...params,signal:stop.signal}),/DOCUMENT_WORKER_LOCAL_TRANSPORT/);
 await opened;
 stop.abort();await canceled;
 opened=once(responses,'opened');
 const over=assert.rejects(requestLocalJson({...params,responseLimit:5}),/DOCUMENT_WORKER_LOCAL_TRANSPORT/);
 const [tooBig]=await opened;
 tooBig.writeHead(200,{'Content-Type':'application/json'});tooBig.end('{"ok":true}');
 await over;
});
