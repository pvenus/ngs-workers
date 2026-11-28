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
        self.exercise('mp4')

    def test_sequence_publishes_all_native_png_bytes_and_playback_without_encoding(self):
        self.exercise('sequence')

    def test_titled_sequence_uses_unedited_native_frames(self):
        self.exercise('named')

    def test_upscale_runs_from_formal_request_to_distinct_filex_derivative(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{'WORKFLOW_CINEMATIC_SEQUENCE_UPSCALE':'1'}):
            self.exercise('upscale')

    def exercise(self,kind):
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
        impl,schema,at,marker_name=WorkflowCinematic,READY_SCHEMA,'workflow_cinematic','cinematic-animation-ready.json'
        if kind in ('sequence','named','upscale'):
            from project_data_service.workflow_cinematic_sequence import WorkflowCinematicSequence,READY_SCHEMA as sequence_schema
            impl,schema,at,marker_name=WorkflowCinematicSequence,sequence_schema,'workflow_cinematic_sequence','cinematic-sequence-animation-ready.json'
        marker=f.root/marker_name
        marker.write_text(json.dumps({'schemaVersion':schema,'provider':'wan-i2v-a14b-q4_k_m',
                                      'checkedAt':time.time_ns()//1_000_000}))
        marker.chmod(0o600)
        config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':f.origin,
                'tokenFile':str(f.secret/'media'/'writer_token'),'writerScope':'cinematics'}
        service=impl(PrivatePlannerClaim(str(db),identity.st_dev,identity.st_ino),
            writer_factory=lambda:FilexCASTransport(config),readiness=lambda:renderer_ready(marker,schema=schema))
        pds=ThreadingHTTPServer(('127.0.0.1',0),PDS)
        setattr(pds,at,service)
        worker=threading.Thread(target=pds.serve_forever,daemon=True);worker.start()
        def cleanup():
            pds.shutdown();pds.server_close();worker.join(timeout=3)
        self.addCleanup(cleanup)
        child=subprocess.run(['node',str(DRIVER),str(services),str(db),str(f.root/'inbox'),
            f'http://127.0.0.1:{pds.server_port}',source,kind],capture_output=True,text=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        proof=json.loads(child.stdout); rn=proof['officialRequestId']
        self.assertEqual((sources/(source+'.png')).read_bytes(),still)
        self.assertFalse(list((f.data/'props').iterdir()))
        if kind in ('sequence','named','upscale'):
            path=root/'sequences'/rn
            manifest=json.loads((path/'manifest.json').read_bytes())
            self.assertEqual(manifest['frameCount'],45)
            self.assertEqual((manifest['width'],manifest['height']),(512,256))
            self.assertEqual(manifest['playback'],proof['playback'])
            for i,digest in enumerate(proof['hashes']):
                self.assertEqual(sha256((path/'frames'/f'{i:04d}.png').read_bytes()).hexdigest(),digest)
            self.assertFalse(list(path.glob('*.mp4')))
            if kind=='upscale':
                derived=root/'sequences'/proof['upscale']['id']
                dm=json.loads((derived/'manifest.json').read_bytes())
                self.assertEqual((dm['width'],dm['height']),(1024,512))
                self.assertEqual(dm['upscale']['sourceSequenceId'],rn)
                for i,digest in enumerate(proof['upscale']['hashes']):
                    self.assertEqual(sha256((derived/'frames'/f'{i:04d}.png').read_bytes()).hexdigest(),digest)

            task,ref='cinematic_sequence_render',f'binari/cinematics/sequences/{rn}/manifest.json'
        else:
            suffix=proof['artifactSha256']
            self.assertEqual(sha256((root/'results'/(rn+'.mp4')).read_bytes()).hexdigest(),suffix)
            self.assertEqual(json.loads((root/'results'/(rn+'.json')).read_bytes())['sha256'],suffix)
            task,ref='cinematic_render',f'binari/cinematics/results/{rn}.json'
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT task_key,status,result_ref,depends_on_task_id FROM workflow_tasks').fetchall(),
                             [(task,'completed',ref,None)]+([('cinematic_sequence_upscale','completed',
                              f"binari/cinematics/sequences/{proof['upscale']['id']}/manifest.json",None)] if kind=='upscale' else []))
            self.assertEqual(conn.execute('SELECT reason FROM workflow_runs').fetchone()[0],'RS_COMPLETED')


if __name__=='__main__':unittest.main()
