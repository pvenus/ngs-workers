"""Scratch RS -> Planner -> animation -> PDS -> Filex -> Planner/RS, only inference mocked.

Run with NGS_CINEMATIC_SERVICES_ROOT and PYTHONPATH set to matching staged
BackendServices revisions. All network requests stay on ephemeral loopback
ports; no shared checkout, request DB, model, or live process is used.
"""
import json
import os
import sqlite3
import subprocess
import threading
import time
import unittest
from hashlib import sha256
from http.server import ThreadingHTTPServer
from pathlib import Path

import test_scope_registry
from test_prop_package_http import PDS
from test_workflow_native_media_http import image
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.prop_animation_ready import renderer_ready
from project_data_service.workflow_cinematic import WorkflowCinematic, READY_SCHEMA
from project_data_service.workflow_delete_claim import PrivatePlannerClaim

DRIVER=Path(__file__).resolve().with_name('cinematic-resident-chain.fixture.mjs')


class CinematicChain(unittest.TestCase):
    def setUp(self):
        # Reuse the repository's real, bearer-scoped Filex listener, not a publication fake.
        fixture=test_scope_registry.UnifiedFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture=fixture

    def test_cinematic_enters_and_leaves_the_formal_queue_without_prop_or_background_removal(self):
        f=self.fixture
        services=Path(os.environ['NGS_CINEMATIC_SERVICES_ROOT']).resolve(strict=True)
        root=f.data/'cinematics'; sources=root/'sources'; sources.mkdir()
        still=image(128,128,37); source=sha256(still).hexdigest()
        (sources/(source+'.png')).write_bytes(still)
        db=f.root/'planner.sqlite'
        # Worker and PDS use the exact same private planner inode for the whole test.
        db.touch(mode=0o600,exist_ok=False)
        init=subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
new PlannerStore(process.argv[1],{cinematicEnabled:true,readCinematicPublication:async()=>null}).close();""",str(db)],
                            cwd=services,capture_output=True,text=True,timeout=10)
        self.assertEqual(init.returncode,0,init.stderr)
        db.chmod(0o600); identity=db.stat()
        marker=f.root/'cinematic-animation-ready.json'
        marker.write_text(json.dumps({'schemaVersion':READY_SCHEMA,'provider':'wan-i2v-a14b-q4_k_m',
                                      'checkedAt':time.time_ns()//1_000_000}))
        marker.chmod(0o600)
        config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':f.origin,
                'tokenFile':str(f.secret/'media'/'writer_token'),'writerScope':'cinematics'}
        service=WorkflowCinematic(PrivatePlannerClaim(str(db),identity.st_dev,identity.st_ino),
            writer_factory=lambda:FilexCASTransport(config),readiness=lambda:renderer_ready(marker,schema=READY_SCHEMA))
        pds=ThreadingHTTPServer(('127.0.0.1',0),PDS)
        pds.workflow_cinematic=service
        worker=threading.Thread(target=pds.serve_forever,daemon=True);worker.start()
        def cleanup():
            pds.shutdown();pds.server_close();worker.join(timeout=3)
        self.addCleanup(cleanup)
        child=subprocess.run(['node',str(DRIVER),str(services),str(db),str(f.root/'inbox'),
            f'http://127.0.0.1:{pds.server_port}',source],capture_output=True,text=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        proof=json.loads(child.stdout); rn=proof['officialRequestId']
        png=source+'.png'; suffix=proof['artifactSha256']
        self.assertEqual((sources/png).read_bytes(),still)
        self.assertEqual(sha256((root/'results'/(rn+'.mp4')).read_bytes()).hexdigest(),suffix)
        self.assertEqual(json.loads((root/'results'/(rn+'.json')).read_bytes())['sha256'],suffix)
        self.assertFalse(list((f.data/'props').iterdir()))
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT task_key,status,result_ref,depends_on_task_id FROM workflow_tasks').fetchall(),
                             [('cinematic_render','completed',f'binari/cinematics/results/{rn}.json',None)])
            self.assertEqual(conn.execute('SELECT reason FROM workflow_runs').fetchone()[0],'RS_COMPLETED')


if __name__=='__main__':unittest.main()
