"""Current postprocess service/process observation; never modifies host state."""

import hashlib
import json
import os
import plistlib
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from ..adapters.frame_workspace import digest
from ..adapters.failure_fence import digest as claim_digest
from .host_recovery_site import owned_read


def _need(condition):
    if not condition:
        raise ValueError('POSTPROCESS_QUIESCENCE_UNVERIFIED')


class MacPostprocessQuiescence:
    def __init__(self, *, ops_config, desired_file, scratch_root,
                 runner=subprocess.run, clock=time.time):
        self.ops_config, self.desired_file = ops_config, desired_file
        self.scratch_root = Path(scratch_root)
        _need(self.scratch_root.is_absolute() and self.scratch_root != Path('/'))
        self.runner, self.clock = runner, clock

    def _run(self, *args):
        try:
            return self.runner(list(args), capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('POSTPROCESS_QUIESCENCE_UNVERIFIED') from None

    def _service(self):
        raw_config = owned_read(self.ops_config)
        raw_desired = owned_read(self.desired_file)
        rows = [r for r in json.loads(raw_config)['registeredResidents'] if r.get('id') == 'postprocess']
        _need(len(rows) == 1 and json.loads(raw_desired).get('postprocess') is False)
        spec = rows[0]
        label = 'com.vinus.resident.postprocess'
        path = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
        _need(spec['label'] == label and spec['plistPath'] == str(path))
        raw = owned_read(path)
        plist = plistlib.loads(raw)
        args = plist['ProgramArguments']
        _need(hashlib.sha256(raw).hexdigest() == spec['plistSha256']
              and plist.get('Label') == label and args == spec['programArguments']
              and any(args[i:i+3] == ['-m', 'workers.resident', 'postprocess'] for i in range(len(args))))
        job = self._run('/bin/launchctl', 'print', f'gui/{os.getuid()}/{label}')
        _need(job.returncode == 113 and not job.stdout.strip()
              and re.search(r'Could not find service "' + re.escape(label) + r'"(?:\s|$)', job.stderr))
        return digest([hashlib.sha256(a).hexdigest() for a in (raw_config, raw_desired, raw)])

    def _identity(self):
        host = self._run('/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice')
        matches = re.findall(r'"IOPlatformUUID"\s*=\s*"([0-9a-fA-F-]{36})"', host.stdout)
        _need(host.returncode == 0 and not host.stderr.strip() and len(matches) == 1)
        machine = hashlib.sha256(str(UUID(matches[0])).encode()).hexdigest()
        boot = self._run('/usr/sbin/sysctl', '-n', 'kern.boottime')
        match = re.fullmatch(r'\{ sec = (\d+), usec = (\d+) \}.*', boot.stdout.strip())
        _need(boot.returncode == 0 and not boot.stderr.strip() and match is not None)
        return machine, digest([int(match[1]), int(match[2])])

    def _absence(self, attempt):
        q = self._run('/bin/ps', '-ww', '-axo', 'pid=,ppid=,pgid=,command=')
        _need(q.returncode == 0 and not q.stderr.strip())
        rows = {}
        for line in q.stdout.splitlines():
            m = re.fullmatch(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S.*)', line)
            _need(m is not None)
            pid, ppid = int(m[1]), int(m[2])
            _need(pid not in rows and m[4] not in ('?', '-'))
            rows[pid] = (ppid, m[4])
        _need(1 in rows and os.getpid() in rows)
        # The observer itself knows the attempt path. Exclude only ourselves,
        # not ancestors or other siblings which may also refer to that path.
        for pid, (_, command) in rows.items():
            if pid == os.getpid():
                continue
            _need(not (str(attempt) in command
                       or re.search(r'\bworkers\.resident\s+postprocess(?:\s|$)', command)
                       or re.search(r'\bframe_full_alpha_cli\.py(?:\s|$)', command)))

    def __call__(self, claim):
        _need(claim.capability == 'frames.extract' and claim.request_id.startswith('frames.req.')
              and isinstance(claim.attempt_id, str) and bool(claim.attempt_id))
        _need(self.scratch_root.resolve(strict=True) == self.scratch_root and self.scratch_root.is_dir())
        attempt = self.scratch_root / digest([claim.request_id, claim.attempt_id])
        before = self.clock()
        service = self._service()
        host, boot = self._identity()
        self._absence(attempt)
        _need((host, boot) == self._identity() and service == self._service())
        observed = self.clock()
        _need(0 <= observed - before <= 5)
        return {
            'schemaVersion': 'resident.postprocess-quiescence.v1',
            'claimHash': claim_digest(asdict(claim)),
            'serviceIdentity': digest([host, boot, service, 'resident-postprocess']),
            'serviceStopped': True,
            'processesAbsent': True,
        }
