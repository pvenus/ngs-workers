"""Operator-pinned composition, never accepts code from a request payload."""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
from ..contracts import PROFILES
from ..runtime import ResidentWorker

@dataclass(frozen=True)
class Ports:
    requests: object
    pds: object
    backend: object
    residency: object = None

def verify(config,role):
    if not isinstance(config,dict) or config.get('schemaVersion')!='resident-deployment.v1' or config.get('role')!=role:
        raise ValueError('WORKER_DEPLOYMENT_INVALID')
    profile=PROFILES[role]
    if not isinstance(config.get('workerId'),str) or not config['workerId']:raise ValueError('WORKER_ID_REQUIRED')
    path=Path(config.get('factoryPath',''))
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.resolve()!=path:raise ValueError('WORKER_FACTORY_PATH_INVALID')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=config.get('factorySha256'):raise ValueError('WORKER_FACTORY_PIN_MISMATCH')
    if config.get('capabilities')!=list(profile.capabilities):raise ValueError('WORKER_CAPABILITIES_CHANGED')
    return path

def compose(config,role,*,loader=None):
    path=verify(config,role)
    if config.get('activationApproved') is not True:raise ValueError('WORKER_ACTIVATION_NOT_APPROVED')
    if loader is None:
        spec=importlib.util.spec_from_file_location('resident_operator_factory',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        loader=module.build_ports
    ports=loader(role,config.get('settings',{}))
    if not isinstance(ports,Ports):raise ValueError('WORKER_PORTS_INVALID')
    for obj,names in ((ports.requests,('claim','renew','checkpoint','complete','fail','uncertain','execution_unknown','release_resource')),
                      (ports.pds,('stage','publish','cleanup')),(ports.backend,('load','unload','execute'))):
        if any(not callable(getattr(obj,name,None)) for name in names):raise ValueError('WORKER_PORTS_INCOMPLETE')
    if (PROFILES[role].model or getattr(ports.backend,'requires_model',False)) and ports.residency is None:raise ValueError('MODEL_RESIDENCY_REQUIRED')
    return ResidentWorker(PROFILES[role],config['workerId'],ports.requests,ports.pds,ports.backend,residency=ports.residency)
