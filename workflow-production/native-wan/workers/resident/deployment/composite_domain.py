"""Explicit service-side domain composition. No intake, schema or loop starts on import.

Capabilities have exactly one owner. A worker profile spanning domains is rejected:
selectors may perform original ownership admission and must not be speculatively
called to choose among candidates. Failure/parent routing is supplied separately
because a parent request ID has no child capability.
"""
from threading import Event


class LoopGroup:
    def __init__(self, loops, poll_seconds=1):
        self.loops = tuple(loops)
        self.stop = Event()
        self.poll_seconds = poll_seconds
        self.last_errors = {}

    def tick(self):
        results = []
        for name, loop in self.loops:
            try:
                results.append((name, loop.tick()))
                self.last_errors.pop(name, None)
            except Exception as exc:
                # A failed parent must not prevent unrelated domains progressing.
                self.last_errors[name] = type(exc).__name__
        return results

    def serve(self):
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(self.poll_seconds)


def composite_domain_factory(bindings, *, failure_guard_reader, parent_loop=None):
    """bindings: (name, exact capabilities, inert domain factory).

    Host lifecycle runs each group under ThreadAuthority.scope. Every child
    callback still performs its original claim/hash/PDS checks; this router never
    weakens them or retries another domain after rejection.
    """
    bindings = tuple(bindings)
    owners = {}
    names = set()
    for name, capabilities, factory in bindings:
        if not name or name in names or not capabilities or not callable(factory):
            raise ValueError('DOMAIN_BINDING_INVALID')
        names.add(name)
        for capability in capabilities:
            if capability in owners:
                raise ValueError('DOMAIN_CAPABILITY_AMBIGUOUS')
            owners[capability] = name
    if not bindings or not callable(failure_guard_reader):
        raise ValueError('DOMAIN_FAILURE_ROUTER_REQUIRED')

    def build(common, bridge, model, terminal):
        domains = {name: factory(common, bridge, model, terminal)
                   for name, _, factory in bindings}
        required = ('selector', 'verify_publication',
                    'inspect_publication', 'verify_recovery_publication',
                    'cleanup', 'initialize_schema', 'preflight')
        for name, capabilities, _ in bindings:
            domain = domains[name]
            if any(not callable(domain.get(key)) for key in required):
                raise ValueError('DOMAIN_PORT_REQUIRED')
            declared = domain.get('capabilities')
            if declared is not None and set(declared) != set(capabilities):
                raise ValueError('DOMAIN_CAPABILITY_CHANGED')

        def owner(claim):
            name = owners.get(claim.capability)
            if name is None:
                raise ValueError('DOMAIN_CAPABILITY_UNSUPPORTED')
            return domains[name]

        def route(key):
            def call(claim, *args):
                method = owner(claim).get(key)
                if not callable(method):
                    raise ValueError('DOMAIN_PORT_UNSUPPORTED:' + key)
                return method(claim, *args)
            return call

        def selector(profile):
            selected = {owners[c] for c in profile.capabilities if c in owners}
            if not selected:
                return None
            if len(selected) != 1:
                raise ValueError('DOMAIN_PROFILE_AMBIGUOUS')
            name = next(iter(selected))
            value = domains[name]['selector'](profile)
            if value is not None and (owners.get(value.get('capability')) != name
                                     or value.get('capability') not in profile.capabilities):
                raise ValueError('DOMAIN_SELECTOR_OWNER_CHANGED')
            return value

        def initialize():
            for domain in domains.values():
                domain['initialize_schema']()

        def preflight():
            for domain in domains.values():
                if domain['preflight']() is not True:
                    raise ValueError('DOMAIN_PREFLIGHT_REJECTED')
            return True

        result = {key: route(key) for key in required if key not in
                  ('selector', 'initialize_schema', 'preflight')}
        result.update(selector=selector, initialize_schema=initialize,
                      preflight=preflight, capabilities=tuple(owners),
                      failure_guard_reader=failure_guard_reader)
        for key in ('guard_resolver', 'resume_publication', 'workspace_for_claim', 'media_source'):
            result[key] = route(key)
        for key in ('parent_loop', 'animation_batch_loop', 'media_ownership_loop', 'pose_batch_loop', 'workflow_retry_loop'):
            loops = [(name, domain[key]) for name, domain in domains.items()
                     if domain.get(key) is not None]
            if key == 'parent_loop' and parent_loop is not None:
                loops.append(('outer', parent_loop))
            result[key] = LoopGroup(loops) if loops or key == 'parent_loop' else None
        # Single service endpoint owners are explicit; collision never picks first.
        for key in ('document_bridge', 'portrait_pair_service', 'pose_direction_service',
                    'trusted_retained_worker'):
            values = [d[key] for d in domains.values() if d.get(key) is not None]
            if len(values) > 1:
                raise ValueError('DOMAIN_ENDPOINT_AMBIGUOUS:' + key)
            if values:
                result[key] = values[0]
        return result
    return build
