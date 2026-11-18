import tempfile,unittest,threading,urllib.error
from pathlib import Path
from http.server import HTTPServer
from workers.resident.adapters.document_draft_http import handler,HttpDrafts
from workers.resident.contracts import Claim
class HTTPTests(unittest.TestCase):
 def test_authenticated_draft_boundary(self):
  class Bridge:
   def authorize(self,c):return {'requestId':c.request_id,'parentOwnsApply':True,'publicationScope':'request-draft-candidate'}
   def submit(self,c,p):self.p=p
   def read(self,c):return self.p
  b=Bridge();server=HTTPServer(('127.0.0.1',0),handler(b,{'test-token':'doc-worker'},lambda c,w:w=='doc-worker'))
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory()as root:
    url='http://127.0.0.1:'+str(server.server_port)
    c=Claim('req','attempt','fence','document.edit',(),{'intent':'edit'},'grant','1')
    client=HttpDrafts(url,'doc-worker','test-token',journal_path=Path(root)/'claim.json')
    self.assertTrue(client.authorize(c)['parentOwnsApply']);client.submit(c,{'draft':'result'});self.assertEqual(client.read(c),{'draft':'result'})
    bad=HttpDrafts(url,'doc-worker','wrong',journal_path=Path(root)/'bad.json')
    with self.assertRaises(urllib.error.HTTPError):bad.read(c)
  finally:server.shutdown();server.server_close();thread.join()
 def test_composed_request_service_draft_route(self):
  from contextlib import nullcontext
  from types import SimpleNamespace
  from workers.resident.deployment.request_service import build_handler
  class Bridge:
   def authorize(self,c):return {'requestId':c.request_id,'parentOwnsApply':True,'publicationScope':'request-draft-candidate'}
  common=SimpleNamespace(transaction=lambda:nullcontext(),_lease=lambda c:{'workerId':'document-worker'})
  server=HTTPServer(('127.0.0.1',0),build_handler(common,{'test-token':('document-worker','document')},document_bridge=Bridge()))
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory()as root:
    client=HttpDrafts('http://127.0.0.1:'+str(server.server_port),'document-worker','test-token',journal_path=Path(root)/'claim')
    c=Claim('req','attempt','fence','document.edit',(),{},'grant','1')
    self.assertTrue(client.authorize(c)['parentOwnsApply'])
  finally:server.shutdown();server.server_close();thread.join()
