import contextlib, hashlib, importlib.util, io, json, pathlib, tempfile, threading, unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
helper=pathlib.Path(__file__).with_name('animation-native.py')
spec=importlib.util.spec_from_file_location('animation_native_fixture',helper)
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=pathlib.Path(self.tmp.name).resolve()
  for basename in ('job-Abc123','job-Abc124'):(self.root/basename).mkdir()
  self.model=object();self.stop=threading.Event()
  self.session=SimpleNamespace(process=self.model,unload=Mock(),cleanup_job=Mock(),terminal={})
  self.session.unload.side_effect=lambda:setattr(self.session,'process',None)
 def commands(self,values):
  return io.BytesIO(b''.join(json.dumps(x).encode()+b'\n' for x in values))
 def serve(self,cmd,render):
  output=io.StringIO()
  with patch.object(native,'render',side_effect=render),contextlib.redirect_stdout(output):
   native.serve(self.root,self.session,self.stop,None,None,stdin=self.commands(cmd))
  return [json.loads(x) for x in output.getvalue().splitlines()]
 def test_owned_engine_kept_until_explicit_joined_close(self):
  renders=[]
  def render(path,stop,session,run,image,*,retained):
   self.assertIs(session,self.session)
   self.assertIs(session.process,self.model)
   self.assertTrue(retained);renders.append(path.name)
   self.session.unload.assert_not_called()
   return {'frames':['fixture']}
  values=[{'op':'render','job':'job-Abc123'},{'op':'render','job':'job-Abc124'},{'op':'close'}]
  result=self.serve(values,render)
  self.assertEqual([x['kind'] for x in result],['rendered','rendered','closed'])
  self.assertTrue(result[-1]['joined']);self.session.unload.assert_called_once()
  self.assertEqual(renders,['job-Abc123','job-Abc124'])
 def test_failure_always_ends_session_and_reports_real_join(self):
  render=Mock(side_effect=ValueError('ANIMATION_PNG_FORMAT'))
  cmd=[{'op':'render','job':'job-Abc123'},{'op':'render','job':'job-Abc124'}]
  result=self.serve(cmd,render)
  self.assertEqual(result,[{'kind':'error','code':'ANIMATION_PNG_FORMAT','joined':True}])
  self.session.unload.assert_called_once();render.assert_called_once()
 def test_unknown_stop_does_not_call_unload_again_or_render_next(self):
  render=Mock(side_effect=ValueError('PRIVATE FILE'))
  self.session.unload.side_effect=PermissionError('PRIVATE FILE')
  cmd=[{'op':'render','job':'job-Abc123'},{'op':'render','job':'job-Abc124'}]
  result=self.serve(cmd,render)
  self.assertEqual(result,[{'kind':'error','code':'ANIMATION_PROCESS_STOP_UNCONFIRMED','joined':False}])
  self.session.unload.assert_called_once();render.assert_called_once()
 def test_eof_and_invalid_paths_fail_closed(self):
  for cmd in [[],[{'op':'render','job':'../../outside'}],[{'op':'render','job':'job-Abc125'}]]:
   self.session.unload.reset_mock()
   # Nonexistent compliant basename raises FileNotFoundError, with finally teardown.
   if cmd and cmd[0]['job']=='job-Abc125':
    with self.assertRaises(FileNotFoundError):self.serve(cmd,Mock())
   else:
    render=Mock();self.assertEqual(self.serve(cmd,render),[]);render.assert_not_called()
   self.session.unload.assert_called_once()
 def test_cancel_before_render_confirmed(self):
  self.stop.set();render=Mock()
  self.assertEqual(self.serve([{'op':'render','job':'job-Abc123'}],render),
                   [{'kind':'error','code':'ANIMATION_CANCELLED','joined':True}])
  render.assert_not_called();self.session.unload.assert_called_once()
 def test_each_attempt_uses_different_namespace_but_same_existing_seed(self):
  key='workflow.task.'+'a'*64
  ids=[]
  fake=Mock(side_effect=lambda output,job,*a,**k:(ids.append(job.copy()),{'sha256':'b'*64})[1])
  for name in ('job-Abc123','job-Abc124'):
   root=self.root/name
   (root/'job.json').write_text(json.dumps({'requestId':key,'fps':24,'frameCount':2}))
   result=native.render(root,self.stop,self.session,fake,object(),retained=True)
   self.assertEqual(result,{'sha256':'b'*64})
  self.assertNotEqual(ids[0]['requestId'],ids[1]['requestId'])
  self.assertEqual([x['seed'] for x in ids],[int(hashlib.sha256(key.encode()).hexdigest()[:8],16)]*2)
  self.assertEqual([x.args for x in self.session.cleanup_job.call_args_list],[(x['requestId'],'b'*64) for x in ids])
if __name__=='__main__':unittest.main()
