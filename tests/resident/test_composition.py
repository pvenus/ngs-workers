import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from workers.resident.contracts import PROFILES
from workers.resident.deployment.composition import verify,compose
class CompositionTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name).resolve()
  self.factory=self.root/'factory.py';self.factory.write_text('raise RuntimeError("must not import on check")')
  self.config={'schemaVersion':'resident-deployment.v1','role':'document','workerId':'document.1','factoryPath':str(self.factory),'factorySha256':hashlib.sha256(self.factory.read_bytes()).hexdigest(),'capabilities':list(PROFILES['document'].capabilities),'activationApproved':False}
 def test_check_never_imports_factory(self):
  config=self.root/'config.json';config.write_text(json.dumps(self.config))
  p=subprocess.run([sys.executable,'-B','-m','workers.resident','document','--check','--config',str(config)],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr);self.assertFalse(json.loads(p.stdout)['startsService'])
 def test_default_no_activation(self):
  with self.assertRaisesRegex(ValueError,'ACTIVATION'):compose(self.config,'document')
 def test_pin_mismatch(self):
  self.factory.write_text('changed')
  with self.assertRaisesRegex(ValueError,'PIN'):verify(self.config,'document')
 def test_missing_ports(self):
  c={**self.config,'activationApproved':True}
  with self.assertRaisesRegex(ValueError,'PORTS'):compose(c,'document',loader=lambda *_:None)
 def test_no_config_cannot_run(self):
  p=subprocess.run([sys.executable,'-B','-m','workers.resident','document'],capture_output=True)
  self.assertNotEqual(p.returncode,0)
 def test_independent_process_once_wires_ports(self):
  marker=self.root/'claimed'
  self.factory.write_text('''from pathlib import Path
from workers.resident.deployment.composition import Ports
class Requests:
 def claim(self,*a):Path('''+repr(str(marker))+''').write_text('checked');return None
 def renew(self,*a):return True
 def checkpoint(self,*a):pass
 def complete(self,*a):pass
 def fail(self,*a):pass
 def uncertain(self,*a):pass
 def execution_unknown(self,*a):pass
 def release_resource(self,*a):pass
class PDS:
 def stage(self,*a):raise AssertionError('no job')
 def publish(self,*a):raise AssertionError('no job')
 def cleanup(self,*a):raise AssertionError('no job')
class Backend:
 def load(self):raise AssertionError('no model load')
 def unload(self):pass
 def execute(self,*a):raise AssertionError('no job')
def build_ports(role,settings):return Ports(Requests(),PDS(),Backend())
''')
  c={**self.config,'activationApproved':True,'factorySha256':hashlib.sha256(self.factory.read_bytes()).hexdigest()}
  path=self.root/'config.json';path.write_text(json.dumps(c))
  p=subprocess.run([sys.executable,'-B','-m','workers.resident','document','--once','--config',str(path)],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(marker.read_text(),'checked')
