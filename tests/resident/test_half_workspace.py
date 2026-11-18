import copy
from dataclasses import replace
import tempfile
from pathlib import Path
from threading import Event
import test_frame_publication
from workers.resident.adapters.frame_workspace import HalfFrameWorkspace,digest
from workers.resident.adapters.pds_frames import FramePublicationClient
from workers.resident.backends.frame_postprocess import FramePostprocessBackend
from workers.resident.contracts import Claim

class HalfWorkspaceContract(test_frame_publication.FramePublicationContract):
    def setUp(self):
        super().setUp()
        def dispatch(handler):
            if handler.path=="/api/npcs":return handler.respond(200,self.fixture.domain.npcs.current())
            if handler.path.startswith("/assets/portraits/"):
                raw=self.fixture.domain.npcs.asset_get(handler.path.rsplit("/",1)[1])
                handler.send_response(200);handler.send_header("Content-Length",str(len(raw)));handler.end_headers();handler.wfile.write(raw)
        self.fixture.domain.dispatch=dispatch
    def _half(self):
        import io
        from PIL import Image,ImageDraw
        from test_npc_pose_views import envelope
        im=Image.new('RGBA',(64,64),(0,0,0,0));ImageDraw.Draw(im).rectangle((16,16,47,47),fill=(70,100,140,255))
        out=io.BytesIO();im.save(out,format='PNG');a=envelope(out.getvalue());aid=a['sha256']+'.png'
        self.packet['artifacts']=[a];self.packet['manifest']['canvas'].update(width=64,height=64)
        for row in self.packet['manifest']['directions'].values():
            for frame in row['frames']:frame.update(assetId=aid,rawAssetId=aid,alphaAssetId=aid)
        self.client.publish(self.packet)
        c=self.client.current();npc=c['document']['characters'][0]
        full=npc['animationFrameSets']['combat/sword-attack']
        rid='half-workspace-request'
        guard={'npcId':npc['id'],'generationId':'a'*48,'requestId':rid,'leaseToken':'b'*64}
        client=FramePublicationClient(self.client.base,lambda:guard,rid,timeout=2)
        request={'schemaVersion':'ngs.npc-animation-frames-request.v1','projectId':'binari','npcId':npc['id'],
             'posePreset':'combat','presetId':'sword-attack','scalePreset':'half','intent':'fixture',
             'sourceFrameSet':{'slotKey':'combat/sword-attack','requestId':full['requestId'],'inputHash':full['inputHash'],'contentHash':digest(full)}}
        claim=Claim(rid,'attempt-half',1,'frames.extract',(),request,'grant-test',str(c['revision']))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        return HalfFrameWorkspace(Path(temp.name).resolve(),client),claim,copy.deepcopy(npc)
    def test_real_pds_half_stage_process_publish_cleanup(self):
        port,claim,before=self._half()
        workspace=port.stage(claim)
        artifacts=FramePostprocessBackend().execute(claim,workspace,Event())
        receipt=port.publish(claim,workspace,artifacts);receipt.validate(claim)
        self.assertEqual(port.recover(claim),receipt)
        after=port.client.current()['document']['characters'][0]
        self.assertEqual(before['animationSlots'],after['animationSlots'])
        self.assertEqual(before['animationFrameSets']['combat/sword-attack'],after['animationFrameSets']['combat/sword-attack'])
        self.assertEqual(after['animationFrameSets']['combat/sword-attack/half']['canvas']['scale'],.5)
        self.assertTrue(port.cleanup(claim,workspace));self.assertFalse(workspace.exists())
    def test_partial_stage_cleanup_and_existing_attempt_preserved(self):
        from workers.resident.runtime import ReconciliationRequired
        port,claim,_=self._half();workspace=port.stage(claim)
        with self.assertRaises(ReconciliationRequired):port.stage(claim)
        self.assertTrue(workspace.exists());self.assertTrue(port.cleanup(claim,None))
        wrong=replace(claim,expected_revision='999')
        with self.assertRaisesRegex(ValueError,'REVISION_CHANGED'):port.stage(wrong)
        self.assertTrue(port.cleanup(wrong,None))
    def test_target_changed_blocks_publication(self):
        port,claim,_=self._half();workspace=port.stage(claim)
        artifacts=FramePostprocessBackend().execute(claim,workspace,Event())
        current=port.client.current;original=current()
        changed=copy.deepcopy(original);changed['document']['characters'][0]['animationFrameSets']['combat/sword-attack/half']={'changed':True}
        port.client.current=lambda:changed
        with self.assertRaisesRegex(ValueError,'TARGET_OR_INCARNATION'):port.publish(claim,workspace,artifacts)
        self.assertFalse((workspace/'publication-intent.json').exists())
