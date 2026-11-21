"""One native host dispatcher; operation factories remain authority-scope local.

This is composition, not activation. The operator calls install once at startup.
Lifecycle configure validates bindings but never replaces the global dispatcher.
"""
from dataclasses import dataclass
from threading import RLock
from .resident_operation_proof import OperationFactoryRouter, require
from .resident_frame_proof import checked

@dataclass(frozen=True)
class NativeOperationBinding:
    operation: str
    service_root: str

class ResidentNativeHost:
    def __init__(self, *, resolve_request, original_guard, builders):
        require(callable(resolve_request) and callable(original_guard), 'HOST_READERS')
        self.resolve_request, self.original_guard = resolve_request, original_guard
        self.builders = dict(builders)
        require(set(self.builders) <= {'planning','portrait','pose','animation','frames'} and self.builders, 'HOST_OPERATIONS')
        self._bindings, self._lock, self._installed = {}, RLock(), False
        self.router = OperationFactoryRouter(self._select, self.builders)

    def _binding(self, rid):
        binding = self.resolve_request(rid)
        require(isinstance(binding, NativeOperationBinding) and binding.operation in self.builders, 'HOST_BINDING')
        checked(binding.service_root)
        # Only immutable strings are cached. No service, factory or DB handles.
        with self._lock:
            old = self._bindings.get(rid)
            require(old is None or old == binding, 'HOST_BINDING_CHANGED')
            self._bindings[rid] = binding
        return binding

    def _select(self, guard):
        binding = self._binding(guard['requestId'])
        require(self.original_guard(guard['requestId']) == guard, 'HOST_ORIGINAL_GUARD')
        return binding.operation

    def configure(self, service, request_id):
        binding = self._binding(request_id)
        require(checked(service.root) == checked(binding.service_root), 'HOST_SERVICE_ROOT')
        self._check_install()
        return True

    def _check_install(self):
        if self._installed:
            from local.services import npc_scratch
            require(npc_scratch._FACTORY is self, 'HOST_DISPATCHER_REPLACED')

    def __call__(self, guard):
        self._check_install()
        output = self.router(dict(guard))
        self._check_install()
        return output

    producer = __call__

    def install(self, *, expected_factory=None):
        """Explicit bootstrap only. Fail if another startup replaced the factory."""
        from local.services import npc_scratch
        with self._lock:
            if self._installed:
                self._check_install()
                return self
            require(npc_scratch._FACTORY is expected_factory, 'HOST_INSTALL_CONFLICT')
            from local.services import npc_scratch_host
            require(npc_scratch_host._REQUEST_ROUTER in (None, self), 'HOST_ROUTER_INSTALL_CONFLICT')
            npc_scratch.configure(self)
            npc_scratch_host.register_request_router(self)
            self._installed = True
        return self
