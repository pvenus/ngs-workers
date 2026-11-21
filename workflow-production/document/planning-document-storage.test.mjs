import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {resolvePlanningDocument,resolvePlanningRole} from './planning-document-storage.mjs';
const id='category.preset.'+'a'.repeat(32),scope='binari/npcs/npc.fixture/planning',role='portrait.art';
const content='# Title\r\n\r\n## Complete\r\n본문\r\n';
const sha=x=>createHash('sha256').update(x).digest('hex'),match={id,planningRole:role},marker={contentFormat:'markdown.v1',contentSha256:sha(content)};
const make=(meta,body)=>({scopes:['binari/concept',scope],files:[{scope,path:id+'.json',text:JSON.stringify(meta)},
 ...(body?[{scope,path:id+'/content.md',text:body,sha256:sha(body)}]:[])]});
test('native exact body bytes and old pure JSON are allowed; missing/hash/mixed body fail closed',()=>{
 assert.equal(resolvePlanningRole(make({...match,...marker},content),role).content,content);
 assert.equal(resolvePlanningDocument(make({...match,content},null),id).sourceSha256,sha(content));
 for(const row of [make({...match,...marker},null),make({...match,...marker},content+'change'),
  make({...match,...marker,content},content),make({...match,content},content),make({...match,contentSha256:sha(content)},content)])
  assert.throws(()=>resolvePlanningDocument(row,id),/WORKFLOW_PLAN_STORAGE_BINDING/);
});
