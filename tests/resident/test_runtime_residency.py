import time
import test_runtime
from workers.resident.contracts import ExecutionStopUnconfirmed

class RuntimeResidencyTests(test_runtime.RuntimeTests):
    def setUp(self):
        super().setUp()
        class Residency:
            def __init__(self):self.acquired=0;self.renewed=0;self.released=[];self.valid=True
            def acquire(self,p,w):self.acquired+=1;return 'model-lease'
            def renew(self,l):self.renewed+=1;return self.valid
            def release(self,l):self.released.append(l)
        self.m=Residency();self.w.residency=self.m
        self.b.set_model_lease=lambda lease:setattr(self.b,'model_lease',lease)
    def test_idle_memory_lease_kept_until_unload(self):
        self.w.poll_once();self.assertEqual(self.m.released,[])
        before=self.m.renewed;time.sleep(.025);self.assertGreater(self.m.renewed,before)
        self.now=10;self.w.poll_once()
        self.assertEqual(self.m.released,['model-lease']);self.assertIsNone(self.b.model_lease)
    def test_unconfirmed_unload_never_releases_memory(self):
        self.w.poll_once()
        def broken():raise ExecutionStopUnconfirmed('still running')
        self.b.unload=broken;self.now=10
        with self.assertRaises(ExecutionStopUnconfirmed):self.w.poll_once()
        self.assertEqual(self.m.released,[])
        self.w.model_done.set();self.w.model_thread.join()
    def test_idle_model_lease_loss_stops_and_unloads(self):
        self.w.poll_once();self.m.valid=False;time.sleep(.03)
        self.assertTrue(self.w.stop.is_set());self.w.poll_once()
        self.assertEqual(self.b.unloads,1);self.assertEqual(self.m.released,['model-lease'])
    def test_full_frame_model_is_admitted_but_half_cpu_does_not_load(self):
        self.w.profile=test_runtime.PROFILES['postprocess']
        self.r.claims=[test_runtime.claim('postprocess')]
        self.b.requires_model=True
        self.b.requires_model_for=lambda c:False
        self.w.poll_once();self.assertEqual(self.m.acquired,0);self.assertEqual(self.b.loads,0)
        self.r.claims=[test_runtime.claim('postprocess',2)]
        self.b.requires_model_for=lambda c:True
        self.w.poll_once();self.assertEqual(self.m.acquired,1);self.assertEqual(self.b.loads,1)
    def tearDown(self):
        self.w.model_done.set()
        if self.w.model_thread is not None:self.w.model_thread.join()
