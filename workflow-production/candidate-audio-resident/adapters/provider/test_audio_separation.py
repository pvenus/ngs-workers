import base64
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import wave

spec=importlib.util.spec_from_file_location('separation',Path(__file__).with_name('audio-separation.py'))
target=importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)

def wav(rate=44100,n=44101):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setparams((2,2,rate,0,'NONE',''))
        w.writeframes(b'\0'*n*4)
    return out.getvalue()

def job(data):
    return {'kind':'separate','sourceSha256':hashlib.sha256(data).hexdigest(),'wavBase64':base64.b64encode(data).decode()}

class SeparationTest(unittest.TestCase):
    def test_exact_original_duration_nearest_frame_after_resample(self):
        def split(raw,rate,channels,frames,*_):
            self.assertEqual((len(raw),rate,channels,frames),(44101*4,44100,2,44101))
            return b'\x01\0'*(48001*2)
        result=target.run(job(wav()),split=split)
        self.assertEqual((result['bpm'],result['bpmBasis']),(None,'unavailable'))
        with wave.open(io.BytesIO(base64.b64decode(result['wavBase64'])),'rb') as w:
            self.assertEqual((w.getframerate(),w.getnframes(),w.getnchannels(),w.getsampwidth()),(48000,48001,2,2))

    def test_bad_digest_cancel_missing_material_and_invalid_outputs_fail_closed(self):
        value=job(wav(48000,3))
        never=lambda *_:self.fail()
        with self.assertRaisesRegex(ValueError,'HQ_SEPARATOR_SOURCE_INVALID'):
            target.run({**value,'sourceSha256':'0'*64},split=never)
        with self.assertRaisesRegex(RuntimeError,'AUDIO_HQ_CANCELLED'):
            target.run(value,cancelled=lambda:True,split=never)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError,'HQ_SEPARATOR_UNPROVISIONED'):
                target.run(value,root=Path(root))
        with self.assertRaisesRegex(RuntimeError,'HQ_SEPARATOR_DURATION_INVALID'):
            target.run(value,split=lambda *_:b'')
        with self.assertRaisesRegex(RuntimeError,'HQ_SEPARATOR_OUTPUT_UNCHANGED'):
            target.run(value,split=lambda raw,*_:raw)

    def test_shifted_context_consensus_preserves_aligned_transient_and_reduces_single_grid_error(self):
        import torch
        samples=torch.ones((2,54))*.03
        samples[:,18]=.8
        calls=[]
        def model(batch):
            calls.append(batch.shape[-1])
            stems=torch.zeros((1,4,2,batch.shape[-1]))
            stems[:,0]=batch
            stems[:,3]=1.0 # ignored entirely, regardless of magnitude
            if len(calls)==1:
                stems[:,0,:,6]+=.6 # interior, outside raised-cosine overlap
            return stems
        result=target._consensus(samples,model,1.,torch,lambda:False,segment=20,overlap=4)
        self.assertEqual(calls,[20,20,20,8,12,20,20,14])
        self.assertLessEqual(max(calls),20)
        expected=samples.clone()
        expected[:,6]+=.3
        torch.testing.assert_close(result,expected)
