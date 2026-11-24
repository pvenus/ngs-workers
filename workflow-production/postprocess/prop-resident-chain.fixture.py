"""Opt-in integration against the staged BackendServices fixture, never live.

Run with cwd at that isolated service tree; PYTHONPATH=data-service:filex-writer;
set WORKFLOW_PROP_TEST_PYTHON to a local Pillow interpreter. Planner inserts real workflow rows from the formal request fixture; the three worker modules run over live test-only PDS and
Filex HTTP listeners. Only Codex, diffusion and BiRefNet are body fixtures.
"""
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest import main

from test_prop_stage_authority_http import StageFixture
from prop_package_contract import digest

DRIVER=Path(__file__).with_name('prop-resident-chain.fixture.mjs')

class WorkerChain(StageFixture):
    def test_actual_static_worker_chain_create_regenerate(self):
        def run(oid,operation,title):
            child=subprocess.run(['node',str(DRIVER),str(self.db),oid,self.pds.rsplit(':',1)[1],operation,title],
                check=False,capture_output=True,text=True,timeout=40)
            self.assertEqual(child.returncode,0,child.stderr)
            result=json.loads(child.stdout)
            pin={'propId':self.payload['propId'],'sha256':result['published']['resultRef'].removeprefix('prop.package.'),
                 'revision':1 if operation=='prop.generate' else 2}
            manifest=self.api('/read',pin)['manifest']
            self.assertEqual(manifest['title'],title)
            self.assertEqual(digest(manifest),pin['sha256'])
            self.assertEqual(manifest['interaction']['pixelOpen'],
                {'action':'open','x':1,'y':1,'width':10,'height':5})
            self.assertEqual(manifest['animations'],[])
            self.assertEqual([manifest['media'][x]['width'] for x in ('detail','pixel')],[40,20])
            with sqlite3.connect(self.db) as db:
                tasks=db.execute('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE official_request_id=?',(oid,)).fetchall()
                self.assertEqual(len(tasks),5)
                self.assertTrue(all(v[0]=='completed' and v[1] and v[2]==0 for v in tasks))
            return pin, manifest
        pin1,m1=run(self.oid,'prop.generate','종')
        oid2='request.official.'+'c'*64
        payload={**self.payload,'schemaVersion':'ngs.prop-regenerate.v1','intent':'새 방식으로 등롱',
                 'source':{k:pin1[k] for k in ('revision','sha256')}}
        registered=subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const [old,pdsPort]=process.argv.slice(4);
const readPropPublication=async cand=>{
 const pin={propId:cand.scope.propId,revision:cand.scope.source?cand.scope.source.revision+1:1,sha256:cand.manifestSha256};
 const r=await fetch('http://127.0.0.1:'+pdsPort+'/v1/props/ownership/workflow-package/read',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(pin)});
 if(!r.ok)throw Error('fixture prop package read '+r.status);
 return await r.json();
};
const s=new PlannerStore(db,{propGenerateEnabled:true,propMutationsEnabled:true,readPropPublication});
const first=s.terminalReady().find(x=>x.official_request_id===old);
if(!first||!await s.verifyCompletion(first)||s.terminalAcknowledged(old)!==1)throw Error('fixture first settlement');
s.stage({officialRequestId:id,requestType:'prop.regenerate',projectId:'binari',schemaVersion:payload.schemaVersion,
        payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:2});
s.acknowledge(id);
s.close();""",str(self.db),oid2,json.dumps(payload,ensure_ascii=False),self.oid,self.pds.rsplit(':',1)[1]],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(registered.returncode,0,registered.stderr)
        pin2,m2=run(oid2,'prop.regenerate','새 종')
        self.assertEqual(m2['parentSha256'],pin1['sha256'])
        self.assertNotEqual(m2['media']['detail']['sha256'],m1['media']['detail']['sha256'])
        self.assertEqual(self.api('/read',pin1)['manifest'],m1)
        self.assertEqual(self.api('/read',pin2)['manifest'],m2)

if __name__=='__main__':main()
