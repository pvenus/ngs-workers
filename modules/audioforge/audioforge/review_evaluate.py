"""Explicit offline batch evaluator; no automatic generation or persistent models."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, re, signal, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from .review_data import TARGETS, atomic_json, load_json, measure_audio, safe_file, validate_score
from .config import DATA_DIR, ARTIFACT_DIR

CLI='/opt/homebrew/bin/llama-mtmd-cli'
BLIND='Listen to the entire attached audio. In concise English describe only audible observations: foreground and background instrument families (uncertain identities as uncertain), rhythm, mood, early/middle/late changes, vocals or speech, and audible noise or distortion. Do not infer a title, genre origin, intended use, exact meter, key, or specific traditional instrument without evidence. Do not invent timestamps or events. Separate observations from uncertain interpretations and give alternative interpretations when unsure. You have no production brief. Limit to 180 words.'
ANCHORS=[
('brief_fit',20,'Purpose and emotion match the brief: 0 persistent conflict, 10 partial/inconsistent fit, 20 consistent persuasive support.'),
('development',20,'Development and repetition: 0 disconnected or purposeless repetition, 10 basic repetition/change but weak transitions, 20 natural purpose-appropriate phrase/density/harmonic flow; no required climax.'),
('roles_texture',15,'Musical roles and texture: 0 clashing or muddy roles, 7.5 partly distinct but competing, 15 clear complementary foreground/support/space; more instruments is not better.'),
('coherence',15,'Rhythm/melody/harmony coherence: 0 persistent unintended clashes, 7.5 some unstable transitions, 15 connected pulses/phrases/pitch relationships; fixed meter or strong melody not required.'),
('game_fit',20,'Gameplay fit/fatigue: 0 distracting and conflicts with use, 10 usable but competing/fatiguing features, 20 supports intended attention with few fatigue signs in this short clip; long play and real game mix untested.'),
('perceptual_clarity',10,'Audible clarity/defects: 0 dominant tearing/noise/dropouts, 5 some harshness/masking/instability, 10 clear roles without prominent audible defects; do not estimate digital peaks or LUFS by ear.')]

def capture(args): return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True).stdout

def swap_mb():
    raw=capture(['/usr/sbin/sysctl','vm.swapusage']); match=re.search(r'used = ([0-9.]+)M',raw)
    if not match: raise RuntimeError('cannot monitor swap')
    return float(match[1])

def infer(audio, prompt, out, tokens):
    out.mkdir(exist_ok=False); (out/'prompt.txt').write_text(prompt)
    model=DATA_DIR/'models/qwen25-omni-7b-q4'
    cmd=[CLI,'-m',str(model/'Qwen2.5-Omni-7B-Q4_K_M.gguf'),'--mmproj',str(model/'mmproj-Qwen2.5-Omni-7B-Q8_0.gguf'),'--audio',str(audio),'-f',str(out/'prompt.txt'),'-c','4096','-n',str(tokens),'-b','256','-ub','128','-t','4','--device','MTL0','-ngl','all','--temp','0','--seed','42','--offline','--perf']
    atomic_json(out/'command.json',cmd)
    baseline=swap_mb(); samples=[]; started=time.monotonic(); stop=None
    if baseline>=1536: raise RuntimeError('preflight swap >=1536 MiB; inference deferred')
    processes=capture(['/bin/ps','-axo','pid,ppid,rss,comm']); (out/'processes-before.txt').write_text(processes)
    if any(('llama-mtmd-cli' in line or line.rstrip().endswith('/sa3')) for line in processes.splitlines()): raise RuntimeError('another model process exists')
    with (out/'stdout.txt').open('w') as stdout,(out/'stderr.txt').open('w') as stderr:
        p=subprocess.Popen(['/usr/bin/time','-l']+cmd,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
        try:
            while p.poll() is None:
                used=swap_mb(); elapsed=time.monotonic()-started
                samples.append({'seconds':round(elapsed,3),'swap_used_mb':used})
                if used-baseline>=512 or used>=1536: stop='memory safety: swap threshold'
                if elapsed>180: stop='180 second timeout'
                if stop: break
                time.sleep(1)
        finally:
            if p.poll() is None:
                p.terminate() # time forwards signals inconsistently; target this owned process group too.
                try: os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    stderr=(out/'stderr.txt').read_text(); raw=(out/'stdout.txt').read_text()
    metrics={'returncode':p.returncode,'stop_reason':stop,'swap_before_mb':baseline,'swap_after_mb':swap_mb(),'wall_seconds_wrapper':round(time.monotonic()-started,3),'samples':samples}
    for key,pattern in [('wall_seconds',r'([\d.]+) real'),('max_rss_bytes',r'(\d+)\s+maximum resident set size'),('peak_footprint_bytes',r'(\d+)\s+peak memory footprint')]:
        match=re.search(pattern,stderr); metrics[key]=float(match[1]) if match else None
    metrics['audio_encoded']=bool(re.search(r'mtmd batch encoding done',stderr))
    atomic_json(out/'metrics.json',metrics)
    if p.returncode or stop or not metrics['audio_encoded'] or not raw.strip(): raise RuntimeError(f'inference incomplete: {metrics}')
    return raw,metrics

def score_prompt(track, blind):
    template={'scores':[{'criterion_id':x[0],'score':None,'evidence':[{'segment':'whole','start_seconds':None,'end_seconds':None,'observation':'audible event','interpretation':'relationship to criterion'}],'reason':'brief explanation','confidence':'low','uncertainty':['uncertainty or alternative interpretation']} for x in ANCHORS]}
    return ('Evaluate this entire attached audio under the following common 100-point rubric. Reply ONLY with one compact JSON object in English, following the schema below. Scores are subjective model estimates, not objective quality or human preference. Use 0.5-point increments from zero to each maximum. If evidence is insufficient use null, not zero. Never fill an item using only the brief. Compare your independent blind observations to the intended brief. A mismatch is a possible issue, not a confirmed defect: allow model mishearing and alternative interpretations. Do not copy expected instruments or events as heard facts. No exact traditional instrument, meter, key, or timestamp without evidence. Unknown timestamps must be null. No bonus for a famous tune, many instruments, or strong melody. Do not penalize natural ending silence automatically. No claim about seamless loops or long-term fatigue from this short sample. No DSP numbers. Keep each observation and interpretation to one short sentence, each reason and uncertainty to one short sentence. Provide all six criteria, not a total. EVERY uncertainty array MUST contain at least one nonempty sentence about a limitation or alternative interpretation, never an empty array.\nRUBRIC:\n'+json.dumps(ANCHORS)+'\nINTENDED BRIEF (expectations only):\n'+json.dumps(track['brief'],ensure_ascii=False)+'\nINDEPENDENT BLIND OBSERVATIONS (may contain model errors):\n'+blind+'\nOUTPUT SCHEMA:\n'+json.dumps(template))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--ids',nargs='*',default=TARGETS); parser.add_argument('--dsp-only',action='store_true'); args=parser.parse_args()
    root=DATA_DIR/'reviews/bgm-local-v1'; root.mkdir(parents=True,exist_ok=True)
    lock=(root/'evaluation.lock').open('w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    rubric=load_json(DATA_DIR/'reviews/sori-bgm-rubric-v1/rubric.json'); tracks={t['artifact_id']:t for t in rubric['tracks']}
    atomic_json(root/'rubric-snapshot.json',rubric)
    for aid in args.ids:
        audio=safe_file(ARTIFACT_DIR,aid,'audio.wav')
        folder=root/aid; folder.mkdir(exist_ok=True); result_path=folder/'result.json'
        if args.dsp_only and result_path.exists():
            existing=load_json(result_path);existing['dsp']=measure_audio(audio);atomic_json(result_path,existing);continue
        if result_path.exists() and load_json(result_path).get('status')=='evaluated': print(aid,'already evaluated',flush=True);continue
        audio=safe_file(ARTIFACT_DIR,aid,'audio.wav'); meta=load_json(safe_file(ARTIFACT_DIR,aid,'metadata.json'))
        if meta.get('kind')!='music' or meta.get('synthetic_test_artifact') is not False: raise ValueError('real BGM required')
        if aid not in tracks and isinstance(meta.get('brief'),dict) and meta['brief']:
            tracks[aid]={'brief':meta['brief'],'title':meta.get('title') or aid}
        result={'artifact_id':aid,'status':'unreviewed','total_score':None,'score':None,'audio_processed':False,'dsp':measure_audio(audio),'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'rubric_version':rubric['version'],'evaluator':{'model':'Qwen2.5-Omni-7B Q4_K_M','projector':'Q8_0','revision':'89b785438c8901d4635e42f50480ba5985a1bbf1','runtime':capture([CLI,'--version']).strip(),'device':'MTL0 Apple M4','prompt_version':'giban-english-compact-v1 (sori rubric 1.0)','input_transformations':'Original full stereo PCM16 WAV supplied unchanged; llama mtmd internally decodes/resamples for its audio encoder, internal transformed samples not exported.','license_status':'evaluation only; original Apache-2.0 versus GGUF qwen-research metadata conflict unresolved'},'score_reliability':'low','quality_status':'needs_review','quality_warnings':['형식 검증과 음악 채점 타당성은 다릅니다. 원시 모델 점수의 기준별 근거를 사람이 확인해야 합니다.'],'created_at':datetime.now(timezone.utc).isoformat(),'brief':tracks.get(aid,{}).get('brief'),'title':tracks.get(aid,{}).get('title'),'limitations':['Model inference only; not verified by human listening.','No final selection, automatic rejection or regeneration.','DSP and subjective scores are separate.']}
        atomic_json(result_path,result)
        if args.dsp_only or aid not in tracks: continue
        run=folder/datetime.now().strftime('run-%Y%m%d-%H%M%S');run.mkdir()
        try:
            print(aid,'blind start',flush=True); blind,bm=infer(audio,BLIND,run/'blind',384)
            result.update(blind_observation=blind.strip(),blind_metrics=bm,audio_processed=True,input_coverage_seconds=result['dsp']['duration_seconds'],blind_prompt=BLIND)
            atomic_json(result_path,result)
            prompt=score_prompt(tracks[aid],blind); result['scoring_prompt']=prompt
            print(aid,'score start',flush=True); raw,sm=infer(audio,prompt,run/'score',1600)
            result.update(score_raw=raw,score_metrics=sm)
            score=validate_score(raw,rubric['criteria'],result['dsp']['duration_seconds']);result.update(score=score,total_score=score['total_score'],status='evaluated' if score['total_score'] is not None else 'incomplete')
        except Exception as e:
            result.update(status='incomplete',total_score=None,error=str(e))
        result['run_directory']=str(run); atomic_json(result_path,result)
        print(aid,result['status'],result['total_score'],result.get('error',''),flush=True)
        if 'memory safety' in result.get('error','') or 'preflight swap' in result.get('error',''): break
if __name__=='__main__': main()
