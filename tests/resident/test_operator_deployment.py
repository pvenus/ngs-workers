import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workers.resident.deployment.operator_factory import resolve_environment,load_bindings
from workers.resident.deployment.role_factory import PerClaimWorkspace
from workers.resident.deployment.composition import compose

class OperatorTests(unittest.TestCase):
 def test_required_env(self):
  with patch.dict(os.environ,{},clear=True):
   with self.assertRaisesRegex(ValueError,'ENV_MISSING'):resolve_environment({'token':{'env':'RESIDENT_TEST_TOKEN'}})
  with patch.dict(os.environ,{'RESIDENT_TEST_TOKEN':'fixture'}):
   self.assertEqual(resolve_environment({'token':{'env':'RESIDENT_TEST_TOKEN'}}),{'token':'fixture'})
 def test_pin_before_import(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d).resolve()/'binding.py';path.write_text('raise RuntimeError("must not import")')
   with self.assertRaisesRegex(ValueError,'PIN_MISMATCH'):load_bindings(str(path),'wrong')
 def test_bindings(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d).resolve()/'binding.py';path.write_text('def build_bindings(role,settings): return {}\n')
   self.assertEqual(load_bindings(str(path),hashlib.sha256(path.read_bytes()).hexdigest()).build_bindings('document',{}),{})
 def test_templates_inactive(self):
  for p in Path('configs/resident').glob('*.example.json'):
   c=json.loads(p.read_text());self.assertIs(c['activationApproved'],False)
   with self.assertRaises(ValueError):compose(c,c['role'])
 def test_pending_cleanup_releases_pointer_only(self):
  class Space:
   def stage(self,c):return '/fixture'
   def cleanup(self,c,w):return False
  port=PerClaimWorkspace(lambda c:Space());port.stage('one')
  self.assertFalse(port.cleanup('one','/fixture'));self.assertIsNone(port.active)
  self.assertEqual(port.stage('two'),'/fixture')
 def test_cleanup_exception_releases_pointer(self):
  class Space:
   def stage(self,c):return '/fixture'
   def cleanup(self,c,w):raise OSError('fixture')
  port=PerClaimWorkspace(lambda c:Space());port.stage('one')
  with self.assertRaises(OSError):port.cleanup('one','/fixture')
  self.assertIsNone(port.active);self.assertEqual(port.stage('two'),'/fixture')
