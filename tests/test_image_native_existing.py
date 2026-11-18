"""Opt-in actual legacy runner + new daemon CLI using a synthetic CLI executable."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from workers.resident.backends.image_native import NativeImageProvider
from workers.resident.deployment.image_music import pinned_native_runner
from workers.resident.backends.image_music_workspace import digest
from tests.test_image_music_backends import png

@unittest.skipUnless(os.environ.get('NGS_NATIVE_SOURCE'),'explicit isolated source binding required')
class ExistingNativeTests(unittest.TestCase):
 def test_actual_runner_verifies_native_events_and_arguments(self):
  source=Path(os.environ['NGS_NATIVE_SOURCE']).resolve();sys.path.insert(0,str(source));self.addCleanup(lambda:sys.path.remove(str(source)))
  from local.services.story_node_images.native import NativeNodeImageRunner
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp).resolve();output=root/'native-output';output.mkdir()
   binary=root/'fixture-cli'
   binary.write_text('#!/opt/homebrew/bin/python3.12\n'+'''import sys,json,pathlib
args=sys.argv;root=pathlib.Path(args[args.index('--cd')+1]);request=json.loads(next((root/'native-journal').glob('*.arguments.json')).read_text())
path=root/'native-output/call-fixture.png'
path.write_bytes('''+repr(png())+''')
print(json.dumps({'type':'image_generation_call','id':'call-fixture','status':'completed'}))
print('codex_otel.log_only event.name="codex.tool_result" tool_name=imagegen tool_namespace=image_gen success=true arguments='+json.dumps(request)+' output={}',file=sys.stderr)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({'state':'generated','outputPath':str(path),'error':''}))
''');binary.chmod(0o700)
   request={'providerId':'codex.image_gen','toolName':'image_gen.imagegen','arguments':{'prompt':'isolated fixture','transparent_background':False}}
   claim={'index':0,'providerClaim':'fixture','requestHash':'fixturehash','providerRequest':request,'providerRequestHash':digest(request)}
   provider=NativeImageProvider(root,binary=binary,runner_builder=pinned_native_runner(NativeNodeImageRunner,approved_output_roots=[output]))
   # Scratch registration is explicitly replaced, so this fixture cannot modify
   # the selected operational scratch registry.
   with patch('local.services.story_node_images.native.register_workspace')as register:
    raw,proof=provider.run(claim,root,Event())
   self.assertEqual(raw,png());self.assertTrue(proof['actualToolOutput']);self.assertEqual(proof['providerRequestHash'],digest(request));register.assert_called_once()
