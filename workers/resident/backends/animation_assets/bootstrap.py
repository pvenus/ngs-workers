import sys,runpy,pathlib,importlib.abc,importlib.machinery,hashlib,json
from attention_chunk_cap import wrap_attention
C=pathlib.Path(__import__('os').environ['NGS_WAN_COMFY_ROOT'])
R=pathlib.Path(__file__).parent
hashes=json.loads((R/'upstream-source-hashes.json').read_text())
for relative,expected in hashes.items():
 actual=hashlib.sha256((C/relative).read_bytes()).hexdigest()
 if actual!=expected:raise RuntimeError('Upstream file changed: '+relative)
class AttentionLoader(importlib.abc.Loader):
 def __init__(self,original):self.original=original
 def create_module(self,spec):return self.original.create_module(spec)
 def exec_module(self,module):
  self.original.exec_module(module)
  module.efficient_dot_product_attention=wrap_attention(module.efficient_dot_product_attention,lambda value:print('ATTENTION_QUERY_CAP '+json.dumps(value),flush=True))
  print('ISOLATED_QUERY_CAP_INSTALLED 512; backend, KV chunks, precision and arithmetic unchanged; no upstream file edits.',flush=True)
class AttentionFinder(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname!='comfy.ldm.modules.attention':return None
  spec=importlib.machinery.PathFinder.find_spec(fullname,path)
  if spec and spec.loader:spec.loader=AttentionLoader(spec.loader)
  return spec
sys.meta_path.insert(0,AttentionFinder());sys.path.insert(0,str(C));sys.argv[0]=str(C/'main.py')
import folder_paths
folder_paths.add_model_folder_path('embeddings',__import__('os').environ['NGS_WAN_EMBEDDINGS_ROOT'],is_default=True)
runpy.run_path(str(C/'main.py'),run_name='__main__')
