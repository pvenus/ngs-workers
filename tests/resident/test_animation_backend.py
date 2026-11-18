import hashlib,json,tempfile,unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from workers.resident.backends.animation import AnimationBackend,digest
from workers.resident.contracts import Claim
class Session:
 instances=[]
 def __init__(self,*args):self.stopped=False;self.instances.append(self)
 def unload(self):self.stopped=True
class AnimationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);Session.instances=[]
  self.claim=Claim('request','attempt','fence','animation.generate',('pds:ref','pds:motion'),{'direction':'s'},'video-global-grant','revision')
  self.job={'frameCount':25,'fps':24,'appearancePrompt':'appearance','negativePrompt':'negative','motionPrompt':'walk','referenceType':'pixel','framingPolicy':{}}
  inputs={}
  for key,ref in [('reference','pds:ref'),('motion','pds:motion')]:
   p=self.root/key;p.write_bytes(key.encode());inputs[key]={'path':key,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'ref':ref}
  self.envelope={'claim':{'requestId':'request','attemptId':'attempt','fencingToken':'fence','expectedRevision':'revision','parametersHash':digest({'direction':'s'}),'inputRefs':['pds:ref','pds:motion']},'job':self.job,'inputs':inputs};self.save()
 def save(self):(self.root/'animation-job.json').write_text(json.dumps(self.envelope))
 def backend(self,engine):
  backend=AnimationBackend('/unused',engine=engine,session_factory=Session)
  with patch('workers.resident.backends.animation.binding',return_value={}):backend.load()
  return backend
 def test_lazy_fixture_execution_and_join_before_return(self):
  calls=[]
  def engine(root,job,session,cancel):
   calls.append(job);p=root/'video.mp4';p.write_bytes(b'fixture-output-not-real-video')
   return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'frameCount':25,'fps':24}
  backend=self.backend(engine);self.assertEqual(Session.instances,[])
  result=backend.execute(self.claim,self.root,Event());self.assertEqual(len(result),1);self.assertTrue(Session.instances[0].stopped)
  self.assertTrue(calls[0]['referencePath'].startswith(str(self.root.resolve())));backend.unload();backend.unload()
 def test_fence_hash_path_and_no_grant_rejected_before_engine(self):
  for changed in ('fence','hash','path'):
   with self.subTest(changed=changed):
    self.setUp()
    if changed=='fence':self.envelope['claim']['fencingToken']='other'
    if changed=='hash':self.envelope['inputs']['reference']['sha256']='f'*64
    if changed=='path':self.envelope['inputs']['reference']['path']='/etc/passwd'
    self.save();backend=self.backend(lambda *a:self.fail('inference'))
    with self.assertRaises(ValueError):backend.execute(self.claim,self.root,Event())
    self.assertEqual(Session.instances,[])
 def test_cancel_no_model_and_failure_always_stops(self):
  cancel=Event();cancel.set();backend=self.backend(lambda *a:self.fail('inference'))
  with self.assertRaisesRegex(ValueError,'CANCELLED'):backend.execute(self.claim,self.root,cancel)
  self.assertEqual(Session.instances,[])
  def fail(*args):raise RuntimeError('engine failed')
  backend=self.backend(fail)
  with self.assertRaisesRegex(RuntimeError,'engine failed'):backend.execute(self.claim,self.root,Event())
  self.assertTrue(Session.instances[0].stopped)
 def test_missing_global_grant_rejected(self):
  from dataclasses import replace
  backend=self.backend(lambda *a:self.fail('inference'))
  with self.assertRaisesRegex(ValueError,'INCOMPLETE_FORMAL_CLAIM'):backend.execute(replace(self.claim,resource_grant=''),self.root,Event())
 def test_outside_output_rejected_and_session_stopped(self):
  def engine(root,*args):return {'path':str(self.root/'reference'),'sha256':hashlib.sha256(b'reference').hexdigest(),'frameCount':25,'fps':24}
  with self.assertRaisesRegex(ValueError,'OUTPUT_INVALID'):self.backend(engine).execute(self.claim,self.root,Event())
  self.assertTrue(Session.instances[0].stopped)
 def test_stop_failure_is_not_relabelled_as_safe_failure(self):
  from workers.resident.contracts import ExecutionStopUnconfirmed
  class Unknown(Session):
   def unload(self):raise ExecutionStopUnconfirmed('stop unknown')
  backend=self.backend(lambda *a:(_ for _ in ()).throw(RuntimeError('engine error')));backend.session_factory=Unknown
  with self.assertRaises(ExecutionStopUnconfirmed):backend.execute(self.claim,self.root,Event())
 def test_own_process_is_joined_on_unload(self):
  import subprocess,sys
  from workers.resident.backends.animation import OwnedComfy
  process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
  OwnedComfy._stop(process);self.assertIsNotNone(process.poll())
 def test_conditioning_graph_is_fresh_per_job(self):
  from workers.resident.backends.animation_conditioning import graph_for
  graph=graph_for({**self.job,'requestId':'fixture-only'})
  self.assertEqual(graph['5']['inputs']['text'],'appearance');self.assertEqual(graph['7']['inputs']['text'],'walk')
 def test_separate_residency_root_survives_job_then_idle_unload_removes_only_session(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory()as residency:
   root=Path(residency).resolve();keep=root/'unrelated';keep.write_text('keep')
   def engine(output,job,session,cancel):
    p=output/'video.mp4';p.write_bytes(b'fixture-only');return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'frameCount':25,'fps':24}
   b=AnimationBackend('/unused',engine=engine,session_factory=Session,residency_root=root)
   b.set_model_lease(SimpleNamespace(role='animation',grant_id='model-grant',token='token'))
   with patch('workers.resident.backends.animation.binding',return_value={}):b.load()
   b.execute(self.claim,self.root,Event());session=b.session;self.assertFalse(session.stopped);retained=b.session_root;self.assertTrue(retained.exists())
   b.unload();self.assertTrue(session.stopped);self.assertFalse(retained.exists());self.assertEqual(keep.read_text(),'keep')
