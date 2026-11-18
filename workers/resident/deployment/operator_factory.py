"""Pinned operator factory; only configuration, never request data, loads code.

The reviewed site bindings implement original NGS/PDS authority contracts. Missing
bindings or credentials fail closed. This module never starts a service on import.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
from workers.resident.deployment.role_factory import build_role_ports


def resolve_environment(value):
    if isinstance(value,dict):
        if set(value)=={'env'}:
            name=value['env']
            if not isinstance(name,str) or not name or not os.environ.get(name):
                raise ValueError('OPERATOR_REQUIRED_ENV_MISSING')
            return os.environ[name]
        return {k:resolve_environment(v) for k,v in value.items()}
    if isinstance(value,list):return [resolve_environment(v) for v in value]
    return value


def load_bindings(path,sha256):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.resolve()!=path:
        raise ValueError('OPERATOR_BINDINGS_PATH_INVALID')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=sha256:
        raise ValueError('OPERATOR_BINDINGS_PIN_MISMATCH')
    spec=importlib.util.spec_from_file_location('resident_site_bindings',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not callable(getattr(module,'build_bindings',None)):
        raise ValueError('OPERATOR_BINDINGS_FACTORY_REQUIRED')
    return module


def build_ports(role,settings):
    settings=resolve_environment(settings)
    module=load_bindings(settings['bindingsPath'],settings['bindingsSha256'])
    bindings=module.build_bindings(role,settings)
    if not isinstance(bindings,dict):raise ValueError('OPERATOR_BINDINGS_INVALID')
    return build_role_ports(role,settings,bindings)
