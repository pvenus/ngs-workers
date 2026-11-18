import hashlib,json,struct,tempfile,unittest,wave,zlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from workers.resident.contracts import Claim
from workers.resident.backends.image_music_workspace import digest
from workers.resident.backends.image_generation import ImageGenerationBackend,validate_png
from workers.resident.backends.music_generation import MusicGenerationBackend,hq_engine_factory

def png():
 def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b'')

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.cancel=Event();self.calls=[]
 def claim(self,cap='image.generate',parameters=None,refs=()):
  return Claim('request.fixture','attempt.fixture','fence.fixture',cap,refs,parameters or {'intent':'original'},'grant.fixture','revision.fixture')
 def packet(self,c,files=(),execution=None):
  value={'schemaVersion':'workers.execution.v1','requestId':c.request_id,'attemptId':c.attempt_id,'capability':c.capability,'expectedRevision':c.expected_revision,'requestHash':digest(dict(c.parameters)),'inputRefs':list(c.input_refs),'inputFiles':list(files),'execution':execution or {}}
  (self.root/'execution.json').write_text(json.dumps(value));return value
 def image(self,refs=()):
  c=self.claim('image.edit'if refs else'image.generate',refs=tuple('pds:'+p.name for p in refs))
  descriptor={'providerId':'codex.image_gen','toolName':'image_gen.imagegen','arguments':{'prompt':'officially compiled prompt','transparent_background':True,**({'referenced_image_paths':[str(p)for p in refs]}if refs else{})}}
  native={'index':0,'providerClaim':'native.fixture','requestHash':'original-native-hash','providerRequest':descriptor,'providerRequestHash':digest(descriptor)}
  self.packet(c,[{'ref':ref,'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for ref,p in zip(c.input_refs,refs)],{'providerClaim':native})
  def run(n,w,cancel):
   self.calls.append(n);raw=png();return raw,{**{k:n[k]for k in ('index','providerClaim','requestHash','providerRequestHash')},'sha256':hashlib.sha256(raw).hexdigest(),'actualToolOutput':True}
  b=ImageGenerationBackend(lambda root:SimpleNamespace(run=run));b.load();return c,b
 def test_image_valid_pinned_native_proof_and_no_repeat(self):
  c,b=self.image();out=b.execute(c,self.root,self.cancel);self.assertEqual(len(out),2);self.assertEqual(out[0].read_bytes(),png());self.assertEqual(len(self.calls),1)
  with self.assertRaisesRegex(ValueError,'ALREADY_EXISTS'):b.execute(c,self.root,self.cancel)
  self.assertEqual(len(self.calls),1)
 def test_reference_bytes_pinned_before_provider(self):
  p=self.root/'ref.png';p.write_bytes(png());c,b=self.image((p,));p.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):b.execute(c,self.root,self.cancel)
  self.assertFalse(self.calls)
 def test_formal_claim_mismatch_and_output_symlink_rejected(self):
  c,b=self.image()
  with self.assertRaisesRegex(ValueError,'BINDING'):b.execute(replace(c,attempt_id='other'),self.root,self.cancel)
  (self.root/'artifacts').symlink_to('/tmp')
  with self.assertRaisesRegex(ValueError,'ALREADY_EXISTS'):b.execute(c,self.root,self.cancel)
  self.assertFalse(self.calls)
 def test_native_descriptor_tamper_and_external_reference_rejected(self):
  c,b=self.image();p=json.loads((self.root/'execution.json').read_text());p['execution']['providerClaim']['providerRequest']['arguments']['prompt']='injected';(self.root/'execution.json').write_text(json.dumps(p))
  with self.assertRaisesRegex(ValueError,'PROVIDER_BINDING'):b.execute(c,self.root,self.cancel)
  self.assertFalse(self.calls)
 def test_cancel_before_and_after_provider_no_artifact_return(self):
  c,b=self.image();self.cancel.set()
  with self.assertRaisesRegex(RuntimeError,'LEASE_LOST'):b.execute(c,self.root,self.cancel)
  self.assertFalse(self.calls);self.cancel.clear();original=b.factory
  def factory(root):
   run=original(root).run
   def call(*a):
    result=run(*a);self.cancel.set();return result
   return SimpleNamespace(run=call)
  b.factory=factory
  with self.assertRaisesRegex(RuntimeError,'LEASE_LOST'):b.execute(c,self.root,self.cancel)
  self.assertFalse(list((self.root/'artifacts').iterdir()))
 def test_bad_native_proof_does_not_publish(self):
  c,b=self.image();b.factory=lambda root:SimpleNamespace(run=lambda *a:(png(),{'actualToolOutput':False}))
  with self.assertRaisesRegex(ValueError,'PROOF'):b.execute(c,self.root,self.cancel)
  self.assertFalse(list((self.root/'artifacts').iterdir()))
 def test_input_symlink_and_manifest_symlink_rejected(self):
  c,b=self.image();(self.root/'execution.json').rename(self.root/'real.json');(self.root/'execution.json').symlink_to(self.root/'real.json')
  with self.assertRaisesRegex(ValueError,'SYMLINK'):b.execute(c,self.root,self.cancel)
 def test_png_validation_crc_and_truncation(self):
  validate_png(png())
  for data in (b'notpng',png()[:-1],png()[:-1]+b'x'):
   with self.assertRaises(ValueError):validate_png(data)
 def music(self,request=None):
  request=request or {'prompt':'original music prompt','duration_seconds':10,'seed':42,'model':'acestep-xl-sft-lm4b','kind':'music','generation_settings':{'bpm':80}}
  c=self.claim('music.generate',request);self.packet(c)
  def generate(prompt,duration,seed,model,output,cancelled,settings):
   self.calls.append((prompt,duration,seed,model,settings))
   with wave.open(str(output),'wb')as out:out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000);out.writeframes(b'\0'*int(duration*48000*4))
   return {'audio_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'synthetic_test_artifact':False,'model':model,'privatePath':'not exported'}
  b=MusicGenerationBackend(lambda folder:SimpleNamespace(generate=generate));b.set_model_lease(SimpleNamespace(role='music',memory_bytes=1));b.load();return c,b
 def test_music_preserves_existing_hq_dto_and_verified_audio(self):
  c,b=self.music();original=dict(c.parameters);out=b.execute(c,self.root,self.cancel);self.assertEqual(len(out),2);self.assertEqual(c.parameters,original);self.assertEqual(self.calls,[('original music prompt',10,42,'acestep-xl-sft-lm4b',{'bpm':80})]);self.assertNotIn('privatePath',out[1].read_text())
 def test_music_legacy_or_unknown_settings_not_silently_downgraded(self):
  c,b=self.music({'prompt':'music','duration_seconds':10,'model':'acestep-v15-turbo','kind':'music'})
  with self.assertRaisesRegex(ValueError,'HQ_'):b.execute(c,self.root,self.cancel)
  self.assertFalse(self.calls)
 def test_unbound_services_fail_closed_without_importing_model(self):
  import sys
  for backend in (ImageGenerationBackend(),MusicGenerationBackend()):
   with self.assertRaisesRegex(RuntimeError,'NOT_BOUND'):backend.load()
  self.assertNotIn('torch',sys.modules);self.assertNotIn('acestep',sys.modules)
 def test_factory_cannot_be_activated_without_operator_locks(self):
  with self.assertRaisesRegex(ValueError,'TRUSTED_BINDING'):hq_engine_factory(runtime_root='/missing',runner_path='/missing',python_path='/missing',lock_fds=(),lock_validator=lambda:None,enabled=True)

 def test_image_unknown_provider_retains_workspace_and_resource(self):
  from workers.resident.contracts import PROFILES,ExecutionStopUnconfirmed
  from workers.resident.runtime import ResidentWorker
  from tests.resident.test_runtime import FakeRequests,FakePDS
  c,b=self.image()
  def run(*a):raise TimeoutError('provider outcome unknown')
  b.factory=lambda root:SimpleNamespace(run=run)
  r=FakeRequests([c]);p=FakePDS(self.root);p.stage=lambda claim:self.root
  worker=ResidentWorker(PROFILES['image'],'fixture.worker',r,p,b)
  with self.assertRaises(ExecutionStopUnconfirmed):worker.poll_once()
  self.assertTrue(worker.stop.is_set());self.assertEqual(len(r.unknown),1)
  self.assertFalse(r.released);self.assertFalse(p.cleaned);self.assertFalse(p.published)
  self.assertTrue((self.root/'execution.json').exists())
 def test_image_nonmodel_profile_runs_without_preload(self):
  from workers.resident.contracts import PROFILES
  from workers.resident.runtime import ResidentWorker
  from tests.resident.test_runtime import FakeRequests,FakePDS
  c,b=self.image();b.unload();r=FakeRequests([c]);p=FakePDS(self.root);p.stage=lambda claim:self.root
  ResidentWorker(PROFILES['image'],'fixture.worker',r,p,b).poll_once()
  self.assertEqual(len(r.completed),1);self.assertEqual(len(p.published),1)
  self.assertEqual(len(p.cleaned),1);self.assertEqual(len(r.released),1)
 def test_music_unconfirmed_child_exit_propagates(self):
  from workers.resident.contracts import ExecutionStopUnconfirmed
  c,b=self.music()
  def run(*a):raise ExecutionStopUnconfirmed('child join timed out')
  b.factory=lambda root:SimpleNamespace(generate=run)
  with self.assertRaisesRegex(ExecutionStopUnconfirmed,'child join'):b.execute(c,self.root,self.cancel)
 def test_music_unknown_error_is_not_cleanup_safe_even_if_cancelled(self):
  from workers.resident.contracts import ExecutionStopUnconfirmed
  c,b=self.music()
  def run(*a):self.cancel.set();raise TimeoutError('unknown process state')
  b.factory=lambda root:SimpleNamespace(generate=run)
  with self.assertRaisesRegex(ExecutionStopUnconfirmed,'MUSIC_PROVIDER_RESULT_UNCONFIRMED'):b.execute(c,self.root,self.cancel)

 def test_music_real_backend_runtime_residency_and_unknown_retention(self):
  from workers.resident.contracts import PROFILES,ExecutionStopUnconfirmed
  from workers.resident.runtime import ResidentWorker
  from tests.resident.test_runtime import FakeRequests,FakePDS
  c,b=self.music();b.unload();b.set_model_lease(None)
  lease=SimpleNamespace(role='music',memory_bytes=100)
  released=[];memory=SimpleNamespace(acquire=lambda p,w:lease,renew=lambda l:True,release=lambda l:released.append(l))
  r=FakeRequests([c]);p=FakePDS(self.root);p.stage=lambda c:self.root
  worker=ResidentWorker(PROFILES['music'],'fixture.music',r,p,b,residency=memory,idle_seconds=1,heartbeat_seconds=.01,clock=lambda:0)
  self.addCleanup(worker.model_done.set)
  worker.poll_once();self.assertEqual(len(r.completed),1);self.assertIs(b.model_lease,lease);self.assertFalse(released)
  worker._unload();self.assertEqual(released,[lease]);self.assertIsNone(b.model_lease)
 def test_music_unknown_cannot_unload_or_clear_memory_lease(self):
  from workers.resident.contracts import ExecutionStopUnconfirmed
  c,b=self.music()
  def run(*a):raise TimeoutError('child outcome unknown')
  b.factory=lambda root:SimpleNamespace(generate=run)
  with self.assertRaises(ExecutionStopUnconfirmed):b.execute(c,self.root,self.cancel)
  with self.assertRaises(ExecutionStopUnconfirmed):b.unload()
  with self.assertRaises(ExecutionStopUnconfirmed):b.set_model_lease(None)
  self.assertIsNotNone(b.model_lease)
 def test_music_rejects_absent_or_wrong_role_model_lease(self):
  b=MusicGenerationBackend(lambda root:None)
  with self.assertRaisesRegex(ValueError,'ADMISSION_REQUIRED'):b.load()
  with self.assertRaisesRegex(ValueError,'LEASE_INVALID'):b.set_model_lease(SimpleNamespace(role='animation',memory_bytes=1))
 def test_music_runtime_unknown_preserves_memory_and_resource(self):
  from workers.resident.contracts import PROFILES,ExecutionStopUnconfirmed
  from workers.resident.runtime import ResidentWorker
  from tests.resident.test_runtime import FakeRequests,FakePDS
  c,b=self.music();b.unload();b.set_model_lease(None)
  def run(*a):raise TimeoutError('unknown')
  b.factory=lambda root:SimpleNamespace(generate=run)
  lease=SimpleNamespace(role='music',memory_bytes=100);released=[]
  memory=SimpleNamespace(acquire=lambda p,w:lease,renew=lambda l:True,release=lambda l:released.append(l))
  r=FakeRequests([c]);p=FakePDS(self.root);p.stage=lambda c:self.root
  worker=ResidentWorker(PROFILES['music'],'fixture.music',r,p,b,residency=memory,heartbeat_seconds=.01)
  self.addCleanup(worker.model_done.set)
  with self.assertRaises(ExecutionStopUnconfirmed):worker.poll_once()
  worker._unload()
  self.assertFalse(released);self.assertFalse(r.released);self.assertFalse(p.cleaned);self.assertTrue(worker.stop.is_set())
