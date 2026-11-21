"""Local model child for native PNG input. No video, extraction or PDS access."""
import hashlib,json,os,sys
from pathlib import Path

def main():
 root=Path(sys.argv[2]);source=json.loads(Path(sys.argv[1]).read_text())
 if source.get('schemaVersion')!='ngs.native-alpha-local-input.v1':raise ValueError('NATIVE_ALPHA_INPUT')
 raw=sorted((root/'raw').glob('frame-*.png'))
 hashes=[hashlib.sha256(p.read_bytes()).hexdigest()for p in raw]
 if not hashes or hashes!=source['rawSha256']:raise ValueError('NATIVE_ALPHA_INPUT_CHANGED')
 # Shared learned matting/tracking implementation, not the legacy video extractor.
 sys.path.insert(0,os.environ['FRAME_ALPHA_MODULE_ROOT'])
 from workers.resident.backends.frame_full_alpha_cli import run_alpha
 key=source['modelKey']
 run_alpha(root,key,{'sourceSha256':source['sequenceHash']})
 summary=json.loads((root/'alpha-summary.json').read_text())
 (root/'pipeline-result.json').write_text(json.dumps(summary))
if __name__=='__main__':main()
