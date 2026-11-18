import json
from pathlib import Path
import tempfile
from threading import Event, Timer
import unittest
from workers.resident.contracts import Claim
from workers.resident.backends.document import DocumentBackend

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root/'work'; self.workspace.mkdir()
        self.binary = self.root/'fixture'
        self.claim = Claim('req','attempt','fence','document.edit',('pds:input',),{'intent':'edit'},'grant','1')
    def backend(self, body, **kw):
        self.binary.write_text('#!/usr/bin/env python3\nimport sys,json,time\nfrom pathlib import Path\n'+body)
        self.binary.chmod(0o700)
        def validate(value):
            if not isinstance(value,dict) or value.get('content') != 'ok':
                raise ValueError('INVALID_DOCUMENT')
        return DocumentBackend(self.binary,output_schema={'type':'object'},validate_output=validate,**kw)
    def test_success_and_replay_refused(self):
        b=self.backend("Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'content':'ok'}))\n")
        b.load(); paths=b.execute(self.claim,self.workspace,Event())
        self.assertEqual(json.loads(paths[0].read_text()),{'content':'ok'})
        with self.assertRaisesRegex(ValueError,'RECOVER_ONLY'): b.execute(self.claim,self.workspace,Event())
        self.assertIn('read-only',b.command(self.workspace))
        self.assertIn('mcp_servers={}',b.command(self.workspace))
    def test_cancel(self):
        b=self.backend('time.sleep(30)\n'); cancel=Event()
        timer=Timer(.2,cancel.set); timer.start()
        try:
            with self.assertRaisesRegex(ValueError,'CANCELLED'): b.execute(self.claim,self.workspace,cancel)
        finally: timer.cancel()
    def test_timeout(self):
        b=self.backend('time.sleep(30)\n',timeout=.1)
        with self.assertRaisesRegex(ValueError,'TIMEOUT'): b.execute(self.claim,self.workspace,Event())
    def test_tool_event_rejected(self):
        b=self.backend("print(json.dumps({'item':{'type':'mcp_tool_call'}}))\n")
        with self.assertRaisesRegex(ValueError,'BOUNDARY'): b.execute(self.claim,self.workspace,Event())
    def test_response_symlink_rejected(self):
        b=self.backend("Path(sys.argv[sys.argv.index('--output-last-message')+1]).symlink_to('/etc/hosts')\n")
        with self.assertRaisesRegex(ValueError,'RESPONSE'): b.execute(self.claim,self.workspace,Event())
    def test_existing_marker_symlink_rejected(self):
        b=self.backend('raise SystemExit(3)\n')
        (self.workspace/'started.json').symlink_to(self.root/'absent')
        with self.assertRaisesRegex(ValueError,'RECOVER_ONLY'): b.execute(self.claim,self.workspace,Event())
    def test_validator_rejects(self):
        b=self.backend("Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('{}')\n")
        with self.assertRaisesRegex(ValueError,'INVALID_DOCUMENT'): b.execute(self.claim,self.workspace,Event())
    def test_cancel_before_launch(self):
        b=self.backend('raise SystemExit(3)\n'); cancel=Event();cancel.set()
        with self.assertRaisesRegex(ValueError,'CANCELLED'): b.execute(self.claim,self.workspace,cancel)
        self.assertFalse((self.workspace/'started.json').exists())
