import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,mkdtemp,readFile,readdir,realpath,rm,writeFile} from 'node:fs/promises';
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
assert 'Current character and expression context' in request['prompt']
assert 'The original guide in a blue coat.' in request['prompt']
assert 'never-fetch-this-marker' not in request['prompt']
assert 'image_gen__imagegen' in instruction
pathlib.Path(${JSON.stringify(commandLog)}).write_text(json.dumps(request))
raw=b"\\x89PNG\\r\\n\\x1a\\n"
def pack(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d))
raw+=pack(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))
raw+=pack(b'IDAT',zlib.compress(b'\\x00\\x00\\x00\\x00\\x00'))+pack(b'IEND',b'')
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
