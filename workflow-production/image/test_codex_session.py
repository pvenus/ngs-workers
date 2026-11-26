import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from codex_session import CodexSession, SessionError

SERVER = r'''
import json, os, sys, uuid
def send(obj):
 print(json.dumps(obj), flush=True)
for line in sys.stdin:
 req=json.loads(line); method=req['method']; p=req.get('params',{})
 if method=='initialized':continue
 out={}
 if method=='thread/start':
  assert p['ephemeral'] and p['sandbox']=='read-only' and p['approvalPolicy']=='never'
  tid=str(uuid.uuid4())
  out={'thread':{'id':tid}}
 elif method=='thread/unsubscribe':
  out={'status':'unsubscribed'}
 elif method=='turn/start':
  out={'turn':{'id':'turn'}}
 send({'id':req['id'],'result':out})
 if method!='turn/start':continue
 value=p['input'][0]['text']
 if value=='hang':continue
 if value=='fail':
  send({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'turn','status':'failed'}}})
  continue
 send({'method':'item/completed','params':{'threadId':'not-current','turnId':'turn','item':{'type':'agentMessage','text':'wrong'}}})
 send({'method':'item/completed','params':{'threadId':tid,'turnId':'turn','item':{
  'type':'agentMessage','text':json.dumps({'prompt':value,'pid':os.getpid()})}}})
 send({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'turn','status':'completed'}}})
'''


class CodexSessionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.binary = self.root / "fake-codex"
        self.binary.write_text("#!/opt/homebrew/bin/python3.12\n" + SERVER)
        self.binary.chmod(0o700)
        self.session = CodexSession("document", threading.Event())
        self.addCleanup(self.session.close)

    def run_job(self, prompt, timeout=1):
        return self.session.run(str(self.binary), self.root, prompt, {}, timeout, 1000)

    def test_reuses_only_connection_and_rotates(self):
        first, a, _ = self.run_job("one")
        second, b, _ = self.run_job("two")
        self.assertNotEqual(a, b)
        self.assertEqual(first["pid"], second["pid"])
        self.assertEqual(second["prompt"], "two")
        self.session.born = time.monotonic() - 301
        third, c, _ = self.run_job("three")
        self.assertNotIn(c, (a, b))
        self.assertNotEqual(first["pid"], third["pid"])
        self.assertEqual(self.session.serial, 2)
        self.session.born = time.monotonic() - 301
        self.session.expire_idle()
        self.assertIsNone(self.session.child)

    def test_completed_failure_is_not_replayed(self):
        with self.assertRaisesRegex(SessionError, "turn_failed"):
            self.run_job("fail")
        self.run_job("fresh")
        self.assertEqual(self.session.serial, 1)

    def test_uncertain_turn_discards_connection_and_does_not_replay(self):
        self.run_job("good")
        with self.assertRaisesRegex(SessionError, "timeout"):
            self.run_job("hang", .15)
        self.assertIsNone(self.session.child)
        self.run_job("fresh")
        self.assertEqual(self.session.serial, 2)

    def test_receipt_provenance_and_colored_trace_formats(self):
        tid = "11111111-1111-1111-1111-111111111111"
        other = "22222222-2222-2222-2222-222222222222"
        cid = "exec-11111111-1111-1111-1111-111111111111"
        args = {"prompt": "fixture", "transparent_background": False}
        header = ('\x1b[2m2026-11-26T00:00:00Z\x1b[0m  INFO codex_otel.{}: '
                  'event.name="codex.tool_result" tool_name=imagegen tool_namespace=image_gen success=true')
        raw = "\n".join([header.format("trace_safe") +
              f" tool_origin=builtin mcp_tool=false conversation.id={tid} call_id={cid}",
              header.format("log_only") + f" call_id={cid} arguments=" + json.dumps(args) + " output={}",
              header.format("trace_safe") + f" tool_origin=builtin mcp_tool=false conversation.id={other} call_id={cid}"])
        with tempfile.TemporaryFile() as tf:
            self.session.trace = tf
            tf.write(raw.encode()); tf.flush()
            receipts, status = self.session._image_receipts(tid, 0)
            self.assertEqual(status, "succeeded")
            self.assertNotIn(other.encode(), receipts)
            self.assertEqual(len(receipts.splitlines()), 2)
            self.assertIn(json.dumps(args).encode(), receipts)

    def test_transport_copies_are_identical_in_source(self):
        src = Path(__file__).parent
        peer = src.parent / 'document'
        if not peer.is_dir():
            self.skipTest("image-only package")
        for name in ("codex_session.py", "codex-session-client.mjs"):
            self.assertEqual((src/name).read_bytes(), (peer/name).read_bytes())


if __name__ == "__main__":
    unittest.main()
