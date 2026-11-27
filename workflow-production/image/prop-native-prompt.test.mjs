import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

test('native prop prompts separate subject appearance from green canvas production without editing source data',()=>{
 const native=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const output=execFileSync('python3',['-B','-c',String.raw`
import copy,hashlib,json,runpy,sys
fn=runpy.run_path(sys.argv[1])['prop_image_prompt']
def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(data): return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def field(key,text): return {'id':key,'text':text,'sha256':sha(text.encode())}
master_fields=[field('visualStyle','{"schemaVersion":"ngs.character-style-contract.v1","styleBlock":"value"}'),
               field('visualProhibitions','{"schemaVersion":"ngs.character-style-constraints.v1","renderingExclusions":"value"}')]
md='Approved subject and prop shape.'
sources=[{'role':'master-design','categoryId':'expression','fields':master_fields,'sha256':sha(canonical(master_fields))},
         {'role':'prop-design','categoryId':'category.add.26710502f5713d99018b77a29384d5da','markdown':md,'sha256':sha(md.encode())}]
context=sha(canonical([[r[k] for k in ('role','categoryId','sha256')] for r in sources]))
phrases=['투명한 단색 분리 배경','투명한 단색 무장면 배경','투명한 단색 분리 배경에 단일 오브젝트만 배치한다','투명 배경 위에',
         'transparent background','transparent single-color canvas','backdrop must be transparent']
material='투명한 유리와 반투명 천, transparent crystal and translucent paper.'
for phrase in phrases:
 intent=f'{phrase}, {material}'
 request={'projectId':'binari','propId':'test-prop','state':'base','lane':'detail','intent':intent,'sources':sources,'contextSha256':context}
 for extra in [{'background':'green-screen.v1'},
               {'background':'green-screen.v1','presentationProfile':'spirit-v2','lane':'pixel','reference':{}}]:
  green_request={**request,**extra}; original=copy.deepcopy(green_request)
  prompt=fn(green_request)
  assert '#00FF00' in prompt and '1536×1024' in prompt and material in prompt and md in prompt
  assert phrase not in prompt.split('[3 Exact request]')[-1]
  assert green_request==original
 original=copy.deepcopy(request)
 prompt=fn(request)
 assert phrase in prompt.split('[3 Exact request]')[-1] and material in prompt and '#00FF00' not in prompt
 assert request==original
print('ok')
`,native],{encoding:'utf8'});
 assert.equal(output.trim(),'ok');
});
