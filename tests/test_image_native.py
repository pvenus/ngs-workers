import json,os,tempfile,unittest
from pathlib import Path
from threading import Event
from workers.resident.backends.image_native import NativeImageCLI
from workers.resident.contracts import ExecutionStopUnconfirmed

class NativeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.stop=Event()
 def binary(self,body):
  p=self.root/'fixture-cli';p.write_text('#!/opt/homebrew/bin/python3.12\n'+body);p.chmod(0o700);return p
 def test_actual_subprocess_schema_output_and_exit(self):
  binary=self.binary("import sys,json,pathlib\na=sys.argv; pathlib.Path(a[a.index('--output-last-message')+1]).write_text(json.dumps({'state':'generated'}))\n")
  client=NativeImageCLI(binary,self.root,self.root/'native-cli',self.stop,grace=.1)
  result,events=client.run('fixture prompt',{'type':'object'},'node.image.fixture',minimal=True)
  self.assertEqual(result,{'state':'generated'});self.assertTrue(events.is_file())
 def test_timeout_joins_process_but_marks_remote_unknown(self):
  binary=self.binary('import time\ntime.sleep(20)\n')
  client=NativeImageCLI(binary,self.root,self.root/'native-cli',self.stop,grace=.1)
  with self.assertRaisesRegex(ExecutionStopUnconfirmed,'REMOTE_OUTCOME'):client.run('x',{},'node.image.fixture',timeout=.1,minimal=True)
  self.assertFalse((self.root/'native-cli/node.image.fixture/response.json').exists())
 def test_request_cannot_add_mcp_args_or_escape_name(self):
  binary=self.binary('raise AssertionError("must not execute")\n');client=NativeImageCLI(binary,self.root,self.root/'native-cli',self.stop)
  for kwargs in ({'mcp_args':['bad'],'minimal':True},{'minimal':False}):
   with self.assertRaisesRegex(ValueError,'CALL_INVALID'):client.run('x',{},'node.image.fixture',**kwargs)
