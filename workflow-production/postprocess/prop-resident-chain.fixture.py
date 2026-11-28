"""Opt-in integration against the staged BackendServices fixture, never live.

Run with cwd at that isolated service tree; PYTHONPATH=data-service:filex-writer;
set WORKFLOW_PROP_TEST_PYTHON to a local Pillow interpreter. Planner inserts real workflow rows from the formal request fixture; the three worker modules run over live test-only PDS and
Filex HTTP listeners. Only Codex, diffusion and BiRefNet are body fixtures; no model is called.
"""
import base64
import io
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest import main

from test_prop_stage_authority_http import StageFixture
from prop_package_contract import digest,wire
from test_prop_package_http import make
from project_data_service.workflow_prop_package import WRITER
from project_data_service.prop_animation_ready import SCHEMA,PROVIDER,renderer_ready

DRIVER=Path(__file__).with_name('prop-resident-chain.fixture.mjs')

class WorkerChain(StageFixture):
    def test_actual_static_worker_chain_create(self):
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
        pin,manifest=run(self.oid,'prop.generate','종')
        result=subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
const [db,id,pinRaw,port]=process.argv.slice(1);
const pin=JSON.parse(pinRaw);
const readPropPublication=async()=>{
 const r=await fetch('http://127.0.0.1:'+port+'/v1/props/ownership/workflow-package/read',
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pin)});
 if(!r.ok)throw Error('fixture prop package read '+r.status);
 return await r.json();
};
const s=new PlannerStore(db,{propGenerateEnabled:true,readPropPublication});
const first=s.terminalReady().find(x=>x.official_request_id===id);
if(!first||!await s.verifyCompletion(first)||s.terminalAcknowledged(id)!==1)throw Error('fixture first settlement');
s.close();""",str(self.db),self.oid,json.dumps(pin),self.pds.rsplit(':',1)[1]],
          capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.api('/read',pin)['manifest'],manifest)

    def test_reference_create_uses_published_detail_and_never_changes_source(self):
        prior=make('a',prop_id='base-ritual-tool')
        source=prior['manifest']
        self.filex_writer(WRITER,{k:prior[k] for k in ('candidate','manifest','media')})
        source_pin=self.pin(source)
        payload={**self.payload,'intent':'몸체와 자루를 보존하고 방울 하나만 추가','reference':source_pin}
        # Keep exactly one eligible fixture job. Recreate its exact plan with
        # the canonical pin rather than force-writing intermediate artifacts.
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM workflow_tasks WHERE official_request_id=?",(self.oid,))
            db.execute("DELETE FROM workflow_runs WHERE official_request_id=?",(self.oid,))
        registered=subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const s=new PlannerStore(db,{propGenerateEnabled:true,readPropPublication:async()=>null});
s.stage({officialRequestId:id,requestType:'prop.generate',projectId:'binari',schemaVersion:payload.schemaVersion,
 payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:2});
s.acknowledge(id);s.close();""",str(self.db),self.oid,json.dumps(payload,ensure_ascii=False)],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(registered.returncode,0,registered.stderr)
        child=subprocess.run(['node',str(DRIVER),str(self.db),self.oid,self.pds.rsplit(':',1)[1],
                              'prop.generate','방울 추가','no-profile',source['media']['detail']['sha256']],
                             capture_output=True,text=True,timeout=40)
        self.assertEqual(child.returncode,0,child.stderr)
        result=json.loads(child.stdout)
        pin={'propId':payload['propId'],'revision':1,'sha256':result['published']['resultRef'].removeprefix('prop.package.')}
        target=self.api('/read',pin)['manifest']
        self.assertEqual(target['title'],'방울 추가')
        self.assertIsNone(target['parentSha256'])
        self.assertEqual(target['requestSha256'],digest(payload))
        self.assertNotEqual(target['media']['detail']['sha256'],source['media']['detail']['sha256'])
        self.assertEqual(self.filex_writer(WRITER+'/read',source_pin)['manifest'],source)

    def test_actual_worker_append_preserves_original_and_publishes_both_timed_lanes(self):
        self.exercise_worker_append()

    def test_profile_runs_real_headroom_and_key_through_claim_scoped_workers(self):
        self.exercise_worker_append('candle-chroma-headroom-v1')

    def exercise_worker_append(self,profile=None):
        prior=make('a',prop_id='candle-brass-petal-unlit-clean') if profile else make('a')
        m1=prior['manifest']
        self.filex_writer(WRITER,{k:prior[k] for k in ('candidate','manifest','media')})
        source={k:v for k,v in self.pin(m1).items() if k!='propId'}
        marker=self.db.parent/'prop-animation-ready.json'
        marker.write_bytes(wire({'schemaVersion':SCHEMA,'provider':PROVIDER,'checkedAt':time.time_ns()//1_000_000}))
        marker.chmod(0o600)
        self.actual.append_readiness=lambda:renderer_ready(marker)
        oid='request.official.'+'d'*64
        payload={'schemaVersion':'ngs.prop-animation-generate.v1','projectId':'binari','propId':m1['propId'],
                 'source':source,'motionId':'gentle-flicker','prompt':'Only the flame flickers'}
        if profile:payload['renderProfile']=profile
        inserted=subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const s=new PlannerStore(db,{propGenerateEnabled:true,propAnimationEnabled:true,propMutationsEnabled:true,readPropPublication:async()=>null});
s.stage({officialRequestId:id,requestType:'prop.animation.generate',projectId:'binari',schemaVersion:payload.schemaVersion,
 payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:2});
s.acknowledge(id);s.close();""",str(self.db),oid,json.dumps(payload)],capture_output=True,text=True,timeout=15)
        self.assertEqual(inserted.returncode,0,inserted.stderr)
        child=subprocess.run(['node',str(DRIVER),str(self.db),oid,self.pds.rsplit(':',1)[1],'prop.animation.generate','gentle-flicker',*([profile] if profile else [])],
            capture_output=True,text=True,timeout=40)
        self.assertEqual(child.returncode,0,child.stderr)
        proof=json.loads(child.stdout)
        pin={'propId':m1['propId'],'revision':2,'sha256':proof['published']['resultRef'].removeprefix('prop.package.')}
        m2=self.api('/read',pin)['manifest']
        self.assertEqual(self.filex_writer(WRITER+'/read',self.pin(m1))['manifest'],m1)
        self.assertEqual(m2['parentSha256'],source['sha256'])
        for field in ('interaction','media','title','designContextSha256'):
            self.assertEqual(m2[field],m1[field])
        c=m2['animations'][-1]; self.assertEqual(c['id'],'gentle-flicker')
        for lane in ('detail','pixel'):
            self.assertEqual(c['media'][lane]['mediaType'],'image/apng')
            self.assertEqual(c['media'][lane]['frameDurationsMs'],[83,84,83])
            self.assertEqual(c['media'][lane]['width'],m1['media'][lane]['width'])
        if profile:
            from PIL import Image
            for lane in ('detail','pixel'):
                media=c['media'][lane]
                image_bytes=base64.b64decode(self.props.read(pin,media['assetId'])['base64'],validate=True)
                with Image.open(io.BytesIO(image_bytes)) as clip:
                    self.assertEqual(clip.n_frames,3)
                    self.assertEqual(clip.getpixel((0,0)),(0,0,0,0))
                    r,g,b,a=clip.getpixel((clip.width//2,clip.height//2))
                    self.assertTrue(r>240 and 40<g<180 and b<24 and a==255,(lane,r,g,b,a))
        with sqlite3.connect(self.db) as db:
            tasks=db.execute('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE official_request_id=?',(oid,)).fetchall()
            self.assertEqual(len(tasks),4);self.assertTrue(all(x=='completed' and y and z==0 for x,y,z in tasks))

if __name__=='__main__':main()
