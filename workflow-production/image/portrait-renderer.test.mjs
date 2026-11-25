import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {access,mkdir,mkdtemp,readFile,readdir,realpath,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {createPortraitRenderer,probePortraitRenderer} from './portrait-renderer.mjs';

const from=process.env.WORKFLOW_IMAGE_LEGACY_TEST_HOST_CONFIG;
const sha=x=>createHash('sha256').update(x).digest('hex');
const input={character:{npcId:'npc.ch1_fixture',slug:'ch1_fixture',name:'Fixture',role:'Guide',actId:'act1',chapterId:'ch1'},
 intent:'Keep the long coat.',sourceRefs:['never-fetch-this-marker'],planningContent:'The original guide in a blue coat.',
 conceptSources:{files:[{normalizedText:'Current character and expression context'}]}};
test('portrait native binding is explicit and fail-closed',async()=>{
 assert.equal(await probePortraitRenderer({hostConfig:'/not/a/file',hostConfigSha256:'a'.repeat(64)}),false);
 assert.throws(()=>createPortraitRenderer({hostConfig:'relative',hostConfigSha256:'a'.repeat(64)}),/HOST_BINDING_REQUIRED/);
 const renderer=createPortraitRenderer({hostConfig:'/not/a/file',hostConfigSha256:'a'.repeat(64)});
 await assert.rejects(renderer({...input,outputPath:'/external.png'}),/INPUT_INVALID/);
 await assert.rejects(renderer(input,{signal:AbortSignal.abort()}),/CANCELLED/);
});

async function fixture(t,kind){
 const root=await realpath(await mkdtemp(join(tmpdir(),'workflow-portrait-test-')));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const output=join(root,'fixture-output');await mkdir(output,{mode:0o700});
 const binary=join(root,'fixture-codex');
 const commandLog=join(root,'observed-input.json');
 const code=`#!/opt/homebrew/bin/python3.12
import struct,zlib,pathlib,json,sys
args=sys.argv
assert '--ephemeral' in args and args[args.index('--sandbox')+1]=='read-only'
instruction=sys.stdin.read()
journal=pathlib.Path(args[args.index('--cd')+1])/'native-journal'
request=json.loads(next(journal.glob('*.arguments.json')).read_text())
assert request['transparent_background'] is True
if ${JSON.stringify(kind)} in ('modern','opaque','pixel-center','pixel-direction','original-combat'):
 assert 'common-art-block' in request['prompt']
 assert 'pose-only-block' in request['prompt']
 assert 'Current character and expression context' not in request['prompt']
else:
 assert 'Current character and expression context' in request['prompt']
 assert 'The original guide in a blue coat.' in request['prompt']
assert 'never-fetch-this-marker' not in request['prompt']
assert 'image_gen__imagegen' in instruction
if ${JSON.stringify(kind)} in ('idle','direction','pixel-center','pixel-direction','original-combat'):
 import hashlib
 refs=request['referenced_image_paths']
 assert len(refs)==1
 local=pathlib.Path(refs[0]); assert local.is_file() and local.parent.name=='references'
 ref_raw=local.read_bytes()
 assert local.name==hashlib.sha256(ref_raw).hexdigest()+'.png'
 assert 'tools.view_image' in instruction
 if ${JSON.stringify(kind)}=='idle': assert 'noncombat idle reference' in request['prompt']
 elif ${JSON.stringify(kind)}=='direction': assert 'do not mirror or rotate the flat image' in request['prompt']
 elif ${JSON.stringify(kind)}=='original-combat': assert 'original full-body combat portrait' in request['prompt']
 elif ${JSON.stringify(kind)}=='pixel-center': assert 'central idle pixel sprite' in request['prompt'] and 'original representative portrait' in request['prompt']
 else: assert 'already downscaled and pixel-corrected canonical center idle sprite' in request['prompt'] and 'direction NW' in request['prompt']
pathlib.Path(${JSON.stringify(commandLog)}).write_text(json.dumps(request))
raw=b"\\x89PNG\\r\\n\\x1a\\n"
def pack(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d))
raw+=pack(b'IHDR',struct.pack('>IIBBBBB',5,5,8,6,0,0,0))
rows=b''.join(bytes([0])+b''.join(bytes([1,2,3,255 if ${JSON.stringify(kind)}=='opaque' or (x,y)==(2,2) else 0]) for x in range(5)) for y in range(5))
raw+=pack(b'IDAT',zlib.compress(rows))+pack(b'IEND',b'')
out=pathlib.Path(${JSON.stringify(output)})/'call-fixture.png'
out.write_bytes(raw)
print(json.dumps({'type':'image_generation_call','id':'call-fixture','status':'completed'}))
if ${JSON.stringify(kind)}=='wrong':request['transparent_background']=False
print('codex_otel.log_only event.name="codex.tool_result" tool_name=imagegen tool_namespace=image_gen success=true arguments='+json.dumps(request)+' output={}',file=sys.stderr)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({'state':'generated','outputPath':str(out),'error':''}))
`;
 await writeFile(binary,code,{mode:0o700});
 const config=JSON.parse(await readFile(from,'utf8'));
 config.binary=binary;config.binarySha256=sha(code);config.outputRoots=[output];
 const contents=JSON.stringify(config),hostConfig=join(root,'host.json');
 await writeFile(hostConfig,contents,{mode:0o600});
 return {options:{hostConfig,hostConfigSha256:sha(contents)},commandLog,output};
}

test('legacy AggregatePortraitRunner verifies actual native events and exact arguments, isolated executable only',{skip:!from},async t=>{
 const {options,commandLog,output}=await fixture(t,'normal');
 assert.equal(await probePortraitRenderer(options),true);
 assert.deepEqual(await readdir(output),[]);
 assert.equal(await probePortraitRenderer({...options,hostConfigSha256:'a'.repeat(64)}),false);
 const result=await createPortraitRenderer(options)(input);
 assert.deepEqual(result,await readFile(join(output,'call-fixture.png')));
 const sent=JSON.parse(await readFile(commandLog,'utf8'));
 assert.equal(sent.transparent_background,true);
 assert.doesNotMatch(sent.prompt,/never-fetch-this-marker/);
});

test('native proof mismatch cannot return bytes',{skip:!from},async t=>{
 const {options}=await fixture(t,'wrong');
 await assert.rejects(createPortraitRenderer(options)(input),/NATIVE_UNCONFIRMED/);
});


for(const kind of ['idle','direction'])test('native reference PNG is pinned, attached and removed from scratch: '+kind,{skip:!from},async t=>{
 const {options,commandLog}=await fixture(t,kind);
 const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
 const reference={assetId:sha(image)+'.png',sha256:sha(image),base64:image.toString('base64')};
 const render=createPortraitRenderer(options);
 const output=await render({...input,reference,...(kind==='direction'?{direction:'nw'}:{})});
 assert(output.length>0);
 const out=JSON.parse(await readFile(commandLog,'utf8'));
 await assert.rejects(access(out.referenced_image_paths[0]),{code:'ENOENT'});
 await assert.rejects(render({...input,reference:{...reference,base64:Buffer.alloc(image.length).toString('base64')}}),/NATIVE_UNCONFIRMED/);
});


for(const kind of ['modern','opaque'])test('modern renderer uses only two directives and enforces decoded representative alpha: '+kind,{skip:!from},async t=>{
 const {options,commandLog}=await fixture(t,kind);
 const command={character:input.character,artDirection:'common-art-block',poseDirection:'pose-only-block'};
 const render=createPortraitRenderer(options);
 if(kind==='opaque')await assert.rejects(render(command),/WORKFLOW_IMAGE_TRANSPARENCY_REJECTED/);
 else assert((await render(command)).length>0);
 const sent=JSON.parse(await readFile(commandLog,'utf8'));
 assert.match(sent.prompt,/이미지 제작 지시[^]*common-art-block[^]*포즈 제작 지시[^]*pose-only-block/);
 assert.doesNotMatch(sent.prompt,/conceptSources|planningContent|never-fetch-this-marker/);
});

for(const kind of ['pixel-center','pixel-direction'])test('native image tool gets exact attached style reference: '+kind,{skip:!from},async t=>{
 const {options,commandLog}=await fixture(t,kind);
 const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
 const reference={assetId:sha(image)+'.png',sha256:sha(image),base64:image.toString('base64')};
 const command={character:input.character,artDirection:'common-art-block',poseDirection:'pose-only-block',reference,
  imageStyle:'pixel-sprite.v1',posePreset:'idle',...(kind==='pixel-direction'?{direction:'nw'}:{})};
 const render=createPortraitRenderer(options);
 assert((await render(command)).length>0);
 const sent=JSON.parse(await readFile(commandLog,'utf8'));
 assert.match(sent.prompt,/mildly super-deformed[^]*clean readable sprite silhouette/);
 assert.match(sent.referenced_image_paths[0],new RegExp(reference.sha256+'\\.png$'));
 await assert.rejects(access(sent.referenced_image_paths[0]),{code:'ENOENT'});
 await assert.rejects(render({...command,reference:undefined}),/INPUT_INVALID/);
});


test('original combat attaches the representative and explicitly does not pixelize',{skip:!from},async t=>{
 const {options,commandLog}=await fixture(t,'original-combat');
 const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
 const reference={assetId:sha(image)+'.png',sha256:sha(image),base64:image.toString('base64')};
 const command={character:input.character,artDirection:'common-art-block',poseDirection:'pose-only-block',reference,
  imageStyle:'original-portrait.v1',posePreset:'combat'};
 const render=createPortraitRenderer(options);
 assert((await render(command)).length>0);
 const sent=JSON.parse(await readFile(commandLog,'utf8'));
 assert.match(sent.prompt,/original full-body combat portrait/);
 assert.match(sent.prompt,/No pixel-art treatment/);
 assert.equal(sent.referenced_image_paths.length,1);
 await assert.rejects(render({...command,direction:'nw'}),/INPUT_INVALID/);
});
