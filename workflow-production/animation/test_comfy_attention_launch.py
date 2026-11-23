"""Tests the owned ComfyUI launch and sample graph without using the GPU."""
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'native-wan'))
from workers.resident.backends.animation import OwnedComfy


class ComfyAttentionLaunchTest(unittest.TestCase):
    def test_launch_uses_pytorch_attention_without_changing_retention(self):
        for retain in (False, True):
            with self.subTest(retain=retain), tempfile.TemporaryDirectory() as root:
                binding = dict(python='/fixture/python', bootstrap='/fixture/bootstrap.py',
                               comfyRoot='/fixture/comfy')
                session = OwnedComfy(binding, root, retain_main_model=retain)
                stub_psutil = SimpleNamespace(Process=lambda pid: SimpleNamespace(create_time=lambda: 0.0))
                with patch.object(session.opener, 'open', side_effect=OSError), \
                     patch('subprocess.Popen', return_value=SimpleNamespace(pid=12)) as launch, \
                     patch.object(os, 'getpgid', return_value=12), \
                     patch.dict(sys.modules, {'psutil': stub_psutil}):
                    session.ensure_ready(threading.Event())
                    self.addCleanup(session.log.close)
                args = launch.call_args.args[0]
                self.assertEqual(args.count('--use-pytorch-cross-attention'), 1)
                self.assertNotIn('--use-split-cross-attention', args)
                self.assertNotIn('--gpu-only', args)
                self.assertIn('--bf16-unet', args)
                self.assertIn('--cache-classic' if retain else '--cache-none', args)


    def test_production_lcm_sampler_uses_ten_steps(self):
        assets = pathlib.Path(__file__).resolve().parents[1] / 'native-wan' / (
            'workers/resident/backends/animation_assets')
        graph = json.loads((assets / 'sample.api.json').read_text())['prompt']
        scheduler = graph['19']
        sampler = graph['21']
        self.assertEqual(scheduler['class_type'], 'BasicScheduler')
        self.assertEqual(scheduler['inputs'], {
            'model': ['1', 0], 'scheduler': 'simple', 'steps': 10, 'denoise': 1.0,
        })
        self.assertEqual(sampler['class_type'], 'SamplerCustom')
        self.assertEqual(sampler['inputs']['sigmas'], ['19', 0])
        self.assertEqual(sampler['inputs']['sampler'], ['20', 0])
        self.assertEqual(graph['20']['inputs']['sampler_name'], 'lcm')
