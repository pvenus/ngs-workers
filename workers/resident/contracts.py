"""Typed ports for the future formal Request and PDS service adapters.

No local queue, direct database writer, raw source-file writer or production mock.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Mapping, Protocol


class ExecutionStopUnconfirmed(RuntimeError):
    """Backend cannot prove owned subprocesses stopped; retain workspace and grant."""


@dataclass(frozen=True)
class Profile:
    role: str
    capabilities: tuple[str, ...]
    resource: str
    model: bool
    capacity: int = 1


PROFILES = {
    'document': Profile('document', ('document.edit', 'document.create'), 'codex', False),
    'image': Profile('image', ('image.generate', 'image.edit'), 'image-api', False),
    'animation': Profile('animation', ('animation.generate',), 'video-global', True),
    'postprocess': Profile('postprocess', ('image.process', 'frames.extract'), 'cpu-image', False),
    'music': Profile('music', ('music.generate',), 'music-model', True),
}


@dataclass(frozen=True)
class Claim:
    request_id: str
    attempt_id: str
    fencing_token: str
    capability: str
    input_refs: tuple[str, ...]
    parameters: Mapping[str, Any]
    resource_grant: str
    expected_revision: str

    def validate(self, profile: Profile) -> None:
        required = (self.request_id, self.attempt_id, self.fencing_token,
                    self.resource_grant, self.expected_revision)
        if not all(isinstance(x, str) and x for x in required):
            raise ValueError('INCOMPLETE_FORMAL_CLAIM')
        if self.capability not in profile.capabilities:
            raise ValueError('UNSUPPORTED_CAPABILITY')
        if any(not isinstance(x, str) or not x for x in self.input_refs):
            raise ValueError('INVALID_INPUT_REFERENCE')


@dataclass(frozen=True)
class Receipt:
    request_id: str
    attempt_id: str
    fencing_token: str
    asset_refs: tuple[str, ...]
    revision: str
    verified: bool

    def validate(self, claim: Claim) -> None:
        if (self.request_id, self.attempt_id, self.fencing_token) != (
                claim.request_id, claim.attempt_id, claim.fencing_token):
            raise ValueError('RECEIPT_CLAIM_MISMATCH')
        if (self.verified is not True or not self.asset_refs
                or any(not isinstance(x, str) or not x for x in self.asset_refs)
                or not isinstance(self.revision, str) or not self.revision):
            raise ValueError('PUBLICATION_NOT_VERIFIED')


class Requests(Protocol):
    # Each claim atomically reserves both job and resource on the SERVER.
    # video-global capacity is 1 across ALL workers/manual/workflow requests.
    # Server validates capability, grant, fence and host memory budget.
    def claim(self, profile: Profile, worker_id: str) -> Claim | None: ...
    def renew(self, claim: Claim) -> bool: ...
    def checkpoint(self, claim: Claim, receipt: Receipt) -> None: ...
    def complete(self, claim: Claim, receipt: Receipt, cleanup_done: bool) -> None: ...
    def fail(self, claim: Claim, code: str, cleanup_done: bool) -> None: ...
    def uncertain(self, claim: Claim, code: str) -> None: ...
    def execution_unknown(self, claim: Claim, code: str) -> None: ...
    # Called only after backend has returned, never merely on lease expiry.
    def release_resource(self, claim: Claim) -> None: ...


class PDS(Protocol):
    def stage(self, claim: Claim) -> Path: ...
    # Must use claim.expected_revision CAS; no direct Filex path access.
    def publish(self, claim: Claim, workspace: Path, artifacts: tuple[Path, ...]) -> Receipt: ...
    # Must remove only this attempt's temporary workspace/leases.
    # workspace=None means stage failed: resolve by claim/attempt registry,
    # never assume nothing was allocated. Return True only after verification.
    def cleanup(self, claim: Claim, workspace: Path | None) -> bool: ...


class Backend(Protocol):
    def load(self) -> None: ...
    def unload(self) -> None: ...
    # Return only when all subprocesses exited. Observe cancel promptly.
    def execute(self, claim: Claim, workspace: Path, cancel: Event) -> tuple[Path, ...]: ...
