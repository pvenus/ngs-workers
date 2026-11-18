import tempfile,unittest
from pathlib import Path
from workers.resident.deployment.role_factory import build_role_ports,FrameBackendDispatch
from workers.resident.adapters.request_http import HttpRequests
from workers.resident.contracts import Claim
class FactoryTests(unittest.TestCase):
 def test_document_real_ports_no_start(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder).resolve();settings={'requestUrl':'http://127.0.0.1:65530','requestToken':'test-only','workerId':'doc','claimJournal':str(root/'claims'),'scratchRoot':str(root),'pdsUrl':'http://127.0.0.1:65531','codexBinary':'/nonexistent/fixture','codexSha256':'0'*64}
   ports=build_role_ports('document',settings,{})
   self.assertIsInstance(ports.requests,HttpRequests);self.assertEqual(list(root.iterdir()),[])
 def test_full_half_resource_routing(self):
  class Backend:
   def execute(self,*a):return 'chosen'
  b=FrameBackendDispatch(Backend(),Backend())
  c=Claim('r','a','f','frames.extract',(),{'scalePreset':'half'},'g','1')
  self.assertFalse(b.requires_model_for(c));self.assertEqual(b.execute(c,None,None),'chosen')
