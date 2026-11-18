import copy,hashlib,json,os,subprocess,tempfile,unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from workers.resident.backends.frame_full import FullFrameBackend
from workers.resident.backends.frame_postprocess import DIRECTIONS
from workers.resident.adapters.frame_full_workspace import FullFrameWorkspace
from workers.resident.contracts import Claim
from PIL import Image,ImageDraw

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  src=self.root/'source';src.mkdir()
  for i,color in enumerate(('red','blue')):
   im=Image.new('RGB',(32,32),'white');ImageDraw.Draw(im).rectangle((4,4,27,27),fill=color);im.save(src/f'{i}.png')
  video=self.root/'source.mp4';subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-framerate','10','-i',str(src/'%d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
  self.raw=video.read_bytes();self.sha=hashlib.sha256(self.raw).hexdigest();self.asset=self.sha+'.mp4'
  self.npc={'id':'npc.fixture','creationBasis':{'requestId':'creation.fixture'},'animationSlots':{'idle/walk/'+d:{'assetId':self.asset,'direction':d}for d in DIRECTIONS}}
  self.current={'document':{'characters':[self.npc]},'revision':1,'sourceVersion':'v1'}
  self.client=SimpleNamespace(current=lambda:copy.deepcopy(self.current),video=lambda aid:self.raw)
  self.request={'schemaVersion':'ngs.npc-animation-frames-request.v1','projectId':'binari','npcId':'npc.fixture','posePreset':'idle','presetId':'walk','intent':'full','scalePreset':'full'}
  self.claim=Claim('frame.fixture','attempt.fixture','fence.fixture','frames.extract',tuple('pds:asset:'+self.asset for d in DIRECTIONS),self.request,'grant.fixture','1')
  scratch=self.root/'scratch';scratch.mkdir();self.port=FullFrameWorkspace(scratch,self.client)
 def backend(self):
  b=FullFrameBackend(python='/opt/homebrew/bin/python3.12',ffmpeg='/opt/homebrew/bin/ffmpeg',ffprobe='/opt/homebrew/bin/ffprobe',module_root=self.root,matting_model=self.root,tracking_model=self.root)
  b.set_model_lease(SimpleNamespace(role='postprocess',memory_bytes=1024))
  def fixture_model(source,folder,cancel):
   from workers.resident.backends.frame_full_alpha import extract_video
   os.environ['FRAME_FFMPEG']='/opt/homebrew/bin/ffmpeg';os.environ['FRAME_FFPROBE']='/opt/homebrew/bin/ffprobe'
   extraction,raw=extract_video(source,folder);alpha=[];out=folder/'alpha';out.mkdir()
   for i,path in enumerate(raw):
    im=Image.open(path).convert('RGBA');a=Image.new('L',im.size,0);ImageDraw.Draw(a).rectangle((4,4,27,27),fill=255);im.putalpha(a);p=out/f'frame-{i:08d}.png';im.save(p);alpha.append(str(p))
   return dict(rawFrames=[str(p)for p in raw],alphaFrames=alpha,frameCount=len(raw),fps=extraction['fps'],frameTimes=[float(f['ptsSeconds'])for f in extraction['frames']],trace=extraction)
  b._run=fixture_model;return b
 def test_full_keeps_all_frames_timing_first_last_raw_and_halves_only_canvas(self):
  folder=self.port.stage(self.claim);b=self.backend();b.execute(self.claim,folder,Event());m=json.loads((folder/'frame-manifest.json').read_text())
  self.assertEqual(m['canvas']['scale'],.5);self.assertEqual(m['canvas']['width'],(m['canvas']['sourceCanvasWidth']+1)//2)
  for d,row in m['directions'].items():
   self.assertEqual(row['frameCount'],2);self.assertEqual(row['fps'],10);self.assertEqual(row['sourceAssetId'],self.asset)
   self.assertEqual([f['index']for f in row['frames']],[0,1]);self.assertEqual([f['sourceTimestamp']for f in row['frames']],[0,.1]);self.assertEqual(row['frameCount']/row['fps'],.2)
   self.assertNotEqual(row['frames'][0]['rawAssetId'],row['frames'][-1]['rawAssetId'])
   for f in row['frames']:
    for field in ('rawAssetId','alphaAssetId'):
     with Image.open(folder/'artifacts'/f[field])as im:self.assertEqual(im.size,(32,32))
 def test_full_requires_model_admission_and_forbids_half(self):
  folder=self.port.stage(self.claim);b=self.backend();b.set_model_lease(None)
  with self.assertRaisesRegex(ValueError,'ADMISSION'):b.execute(self.claim,folder,Event())
  self.claim.parameters['scalePreset']='half'
  with self.assertRaisesRegex(ValueError,'FULL_REQUEST'):b.execute(self.claim,folder,Event())
 def test_source_pin_change_stops_publication(self):
  folder=self.port.stage(self.claim);self.backend().execute(self.claim,folder,Event());self.npc['animationSlots']['idle/walk/n']['assetId']='a'*64+'.mp4'
  with self.assertRaisesRegex(ValueError,'SOURCE_OR_TARGET_CHANGED'):self.port.publish(self.claim,folder,())
 def test_video_bytes_tamper_no_model(self):
  folder=self.port.stage(self.claim);(folder/'n.mp4').write_bytes(b'tampered')
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):self.backend().execute(self.claim,folder,Event())
 @unittest.skipUnless(os.environ.get('PDS_RELEASE_SOURCE'),'explicit isolated PDS fixture required')
 def test_actual_pds_full_publication_preserves_videos_other_npc_and_original_frames(self):
  import sys
  from dataclasses import replace
  from workers.resident.adapters.frame_full_workspace import FullFrameClient
  sys.path.insert(0,str(Path(__file__).resolve().parent/'resident'));self.addCleanup(lambda:sys.path.pop(0))
  from test_frame_publication import FramePublicationContract
  base=FramePublicationContract();base.setUp();self.addCleanup(base.doCleanups)
  current=base.fixture.domain.npcs.current();doc=copy.deepcopy(current['document'])
  base.fixture.domain.npcs.asset_put(self.asset,self.raw)
  for row in doc['characters'][0]['animationSlots'].values():row.update(assetId=self.asset,sha256=self.sha,bytes=len(self.raw),fps=10,frameCount=2)
  base.fixture.domain.npcs.save(current['revision'],'isolated decoded video fixture',doc)
  current=base.fixture.domain.npcs.current();before=copy.deepcopy(current['document'])
  request={**self.request,'npcId':base.packet['npcId'],'posePreset':'combat','presetId':'sword-attack'}
  claim=replace(self.claim,request_id=base.packet['requestId'],parameters=request,expected_revision=str(current['revision']))
  client=FullFrameClient(base.client.base,base.client.guard_provider,base.client.stage)
  # This publisher fixture has no legacy GET dispatcher; reads use its temp
  # authority, publication/receipt are actual PDS HTTP and Filex.
  client.current=base.fixture.domain.npcs.current;client.video=lambda aid:base.fixture.domain.npcs.asset_get(aid)
  port=FullFrameWorkspace(self.port.root,client);folder=port.stage(claim);out=self.backend().execute(claim,folder,Event());receipt=port.publish(claim,folder,out);receipt.validate(claim)
  self.assertEqual(port.recover(claim),receipt)
  after=base.fixture.domain.npcs.current()['document'];self.assertEqual(after['characters'][1],before['characters'][1]);self.assertEqual(after['characters'][0]['animationSlots'],before['characters'][0]['animationSlots'])
  frame_set=after['characters'][0]['animationFrameSets']['combat/sword-attack']
  self.assertEqual(sum(d['frameCount']for d in frame_set['directions'].values()),16)
  self.assertEqual(frame_set['canvas']['scale'],.5)

 def test_actual_pipeline_timeout_stops_child_before_return(self):
  from workers.resident.backends.frame_full import FullFrameBackend
  fake=self.root/'fixture-python';fake.write_text('#!/opt/homebrew/bin/python3.12\nimport time\ntime.sleep(20)\n');fake.chmod(0o700)
  b=FullFrameBackend(python=fake,ffmpeg='/opt/homebrew/bin/ffmpeg',ffprobe='/opt/homebrew/bin/ffprobe',module_root=self.root,matting_model=self.root,tracking_model=self.root,timeout=.1,grace=.1)
  folder=self.root/'cancel';folder.mkdir()
  with self.assertRaisesRegex(RuntimeError,'CANCELLED'):b._run(self.root/'source.mp4',folder,Event())
