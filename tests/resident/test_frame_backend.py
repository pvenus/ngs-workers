"""Formal request -> HTTP pull -> real CPU backend -> isolated PDS-port fixture.

No operating NPC/video, real model generation or production Filex publication.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

@unittest.skipUnless(os.environ.get('NGS_RUNTIME_SOURCE') and importlib.util.find_spec('PIL'),
                     'selected NGS source and Pillow environment required')
class FrameBackendVertical(unittest.TestCase):
    def test_full_formal_request_runs_half_palette_backend_over_http(self):
        from test_official_contracts import OfficialCommon
        from workers.resident.adapters.request_api import handler
        from workers.resident.adapters.request_http import HttpRequests
        from workers.resident.backends.frame_postprocess import FramePostprocessBackend,DIRECTIONS
        from workers.resident.contracts import PROFILES,Receipt
        from workers.resident.runtime import ResidentWorker
        from PIL import Image,ImageDraw
        base=OfficialCommon('test_real_common_claim_heartbeat_complete');base.setUp()
        self.addCleanup(base.doCleanups)
        from local.services.frame_requests.service import FrameRequests
        from local.services.request_pipeline.common import digest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            domain=FrameRequests(base.c,root/'domain',None)
            request={'schemaVersion':'ngs.npc-animation-frames-request.v1','projectId':'binari',
                     'npcId':'character.fixture','posePreset':'idle','presetId':'walk',
                     'intent':'후처리 격리 검증','scalePreset':'full'}
            accepted=domain.accept('fixture.actor','formal.vertical',request)
            rid=accepted['requestId']
            base.items.append({'requestId':rid,'requestType':'npc.animation.frames.generate',
                'inputHash':digest(request),'capability':'frames.extract','inputRefs':['pds:fixture'],
                'parameters':request,'expectedRevision':'1'})
            http=ThreadingHTTPServer(('127.0.0.1',0),handler(base.bridge,
                                           {'fixture-token':('worker.frame','postprocess')}))
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            observed={}
            class StagedPDS:
                def stage(self,c):
                    work=root/'attempt';work.mkdir()
                    paths=[]
                    for color in ('red','blue'):
                        im=Image.new('RGBA',(64,64),(0,0,0,0))
                        ImageDraw.Draw(im).rectangle((16,16,47,47),fill=color)
                        import io
                        out=io.BytesIO();im.save(out,format='PNG');raw=out.getvalue()
                        p=work/(hashlib.sha256(raw).hexdigest()+'.png');p.write_bytes(raw);paths.append(str(p))
                    directions={d:{'rawFrames':paths,'alphaFrames':paths,'frameCount':2,
                                   'fps':10,'sourceAssetId':'fixture-video','frameTimes':[0,.1]} for d in DIRECTIONS}
                    (work/'frame-inputs.json').write_text(json.dumps({'requestId':c.request_id,
                        'attemptId':c.attempt_id,'expectedRevision':c.expected_revision,'directions':directions}))
                    return work
                def publish(self,c,work,artifacts):
                    manifest=json.loads((work/'frame-manifest.json').read_text());observed['manifest']=manifest
                    for row in manifest['directions'].values():
                        for f in row['frames']:
                            image=Image.open(work/'artifacts'/f['assetId']).convert('RGBA')
                            assert image.size==(20,20)
                            assert set(image.getchannel('A').getdata())<={0,255}
                            assert len({rgb[:3] for rgb in image.getdata() if rgb[3]})<=32
                    return Receipt(c.request_id,c.attempt_id,c.fencing_token,('fixture-frames',),'2',True)
                def cleanup(self,c,work):
                    import shutil
                    shutil.rmtree(work)
                    return not work.exists()
            try:
                client=HttpRequests('http://127.0.0.1:'+str(http.server_port),'worker.frame',
                                    'fixture-token',journal_path=root/'claim.json')
                worker=ResidentWorker(PROFILES['postprocess'],'worker.frame',client,StagedPDS(),
                                      FramePostprocessBackend(),heartbeat_seconds=.01)
                self.assertTrue(worker.poll_once())
                self.assertEqual(base.c.row(rid)['state'],'completed')
                manifest=observed['manifest']
                self.assertEqual(manifest['canvas']['scale'],.5)
                self.assertEqual(manifest['pixelPolicy']['colors'],32)
                self.assertEqual(manifest['pixelPolicy']['paletteScope'],'all-directions-all-frames')
                self.assertEqual(set(manifest['directions']),set(DIRECTIONS))
                self.assertEqual(sum(d['frameCount'] for d in manifest['directions'].values()),16)
                self.assertFalse((root/'attempt').exists())
            finally:http.shutdown();http.server_close();thread.join()

@unittest.skipUnless(importlib.util.find_spec('PIL'),'Pillow required')
class FrameOutputBoundary(unittest.TestCase):
    def test_output_symlinks_rejected_before_any_external_write(self):
        from workers.resident.backends.frame_postprocess import FramePostprocessBackend,DIRECTIONS
        from workers.resident.contracts import Claim
        from threading import Event
        from PIL import Image,ImageDraw
        import io
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);outside=base/'outside';outside.mkdir()
            marker=outside/'keep';marker.write_bytes(b'preserve')
            for target_name in ('artifacts','frame-manifest.json'):
                root=base/target_name.replace('.','-');root.mkdir()
                im=Image.new('RGBA',(64,64),(0,0,0,0));ImageDraw.Draw(im).rectangle((16,16,47,47),fill='red')
                data=io.BytesIO();im.save(data,format='PNG');raw=data.getvalue()
                path=root/(hashlib.sha256(raw).hexdigest()+'.png');path.write_bytes(raw)
                directions={d:{'rawFrames':[str(path),str(path)],'alphaFrames':[str(path),str(path)],
                    'frameCount':2,'fps':10,'sourceAssetId':'fixture'} for d in DIRECTIONS}
                (root/'frame-inputs.json').write_text(json.dumps({'requestId':'request.fixture',
                    'attemptId':'1','expectedRevision':'1','directions':directions}))
                (root/target_name).symlink_to(outside if target_name=='artifacts' else marker)
                c=Claim('request.fixture','1','1','frames.extract',(),{},'grant','1')
                with self.assertRaisesRegex(ValueError,'OUTPUT_ALREADY_EXISTS'):
                    FramePostprocessBackend().execute(c,root,Event())
                self.assertEqual(list(outside.iterdir()),[marker]);self.assertEqual(marker.read_bytes(),b'preserve')
