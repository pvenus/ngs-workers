"""Independent workflow process launcher. No Request DB, model or worker imports.

Trusted factory supplies official Request API ports and an already dedicated
workflow executor. Activation remains explicitly gated; --check imports nothing.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import signal
from pathlib import Path
from threading import Event


def verify(config):
    if config.get('schemaVersion')!='workflow-execution-host.v1':
        raise ValueError('WORKFLOW_HOST_SCHEMA_INVALID')
    source=Path(config.get('factoryPath',''))
    if source.is_symlink()or not source.is_absolute()or not source.is_file():
        raise ValueError('WORKFLOW_FACTORY_PATH_INVALID')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=config.get('factorySha256'):
        raise ValueError('WORKFLOW_FACTORY_PIN_CHANGED')
    root=Path(config.get('stateRoot',''))
    if not root.is_absolute()or root.is_symlink()or not root.is_dir()or root.resolve()!=root:
        raise ValueError('WORKFLOW_STATE_ROOT_INVALID')
    interval=config.get('pollSeconds',1)
    if not isinstance(interval,(float,int))or isinstance(interval,bool)or not .1<=interval<=60:
        raise ValueError('WORKFLOW_POLL_INVALID')
    return source,root


def run(config,ports,*,stop=None):
    _,root=verify(config)
    if config.get('activationApproved')is not True:raise ValueError('WORKFLOW_ACTIVATION_REQUIRED')
    engine=ports['engine'];pending=ports['pending_runs'];preflight=ports['preflight']
    if not callable(pending)or not callable(preflight):raise ValueError('WORKFLOW_HOST_PORT_REQUIRED')
    if engine.path.resolve().parent!=root:raise ValueError('WORKFLOW_STATE_OUTSIDE_ROOT')
    stop=stop or Event()
    lockpath=root/'executor.lock'
    if lockpath.is_symlink():raise ValueError('WORKFLOW_LOCK_SYMLINK')
    with lockpath.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if preflight()is not True:raise ValueError('WORKFLOW_HANDOFF_REQUIRED')
        engine.initialize()
        while not stop.is_set():
            # API lists only formally accepted, execution-owner-bound parents.
            # It must retain running parents across this process's restart.
            items={item['runId']:item for item in engine.unfinished_runs()}
            for item in pending():
                prior=items.get(item['runId'])
                if prior is not None and prior!=item:raise ValueError('WORKFLOW_DISCOVERED_PARENT_CHANGED')
                items[item['runId']]=item
            for item in items.values():
                engine.register(item['runId'],item['request'],item['plan'])
                engine.tick(item['runId'])
            stop.wait(config.get('pollSeconds',1))


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--check',action='store_true')
    args=parser.parse_args(argv);config=json.loads(Path(args.config).read_text());source,_=verify(config)
    if args.check:
        print(json.dumps({'verified':True,'activationApproved':config.get('activationApproved')is True}));return
    if config.get('activationApproved')is not True:raise ValueError('WORKFLOW_ACTIVATION_REQUIRED')
    spec=importlib.util.spec_from_file_location('reviewed_workflow_site',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ports=module.build_workflow_host(config)
    stop=Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    run(config,ports,stop=stop)

if __name__=='__main__':main()
