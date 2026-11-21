"""Opt-in standalone Request host. --check verifies pins without importing code."""
import argparse,hashlib,importlib.util,json,signal,threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from .thread_authority import scoped_handler
from .operator_factory import resolve_environment

@dataclass(frozen=True)
class HostPorts:
 authority:object
 handler:object
 loops:object
 preflight:object
 initialize_schema:object
 startup:object=None

def verify(config):
 if config.get('schemaVersion')!='resident-request-host.v1':raise ValueError('REQUEST_HOST_CONFIG_INVALID')
 if config.get('host')!='127.0.0.1' or type(config.get('port'))is not int or not 1024<=config['port']<=65535:raise ValueError('REQUEST_HOST_LOOPBACK_REQUIRED')
 p=Path(config.get('factoryPath',''))
 if not p.is_absolute()or p.is_symlink()or not p.is_file()or p.resolve()!=p:raise ValueError('REQUEST_HOST_FACTORY_PATH')
 if hashlib.sha256(p.read_bytes()).hexdigest()!=config.get('factorySha256'):raise ValueError('REQUEST_HOST_FACTORY_PIN')
 return p

def load(config):
 path=verify(config)
 if config.get('activationApproved')is not True:raise ValueError('REQUEST_HOST_ACTIVATION_REQUIRED')
 spec=importlib.util.spec_from_file_location('resident_request_site',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 ports=module.build_request_host(resolve_environment(config.get('settings',{})))
 # -m executes this file as __main__; site factories import its canonical class.
 from workers.resident.deployment.request_host import HostPorts as CanonicalHostPorts
 if not isinstance(ports,CanonicalHostPorts)or any(not callable(p)for p in (ports.preflight,ports.initialize_schema))or(ports.startup is not None and not callable(ports.startup)):raise ValueError('REQUEST_HOST_PORTS_REQUIRED')
 return ports

def serve(config,ports,*,initialize_schema=False):
 # Preflight must verify old-consumer route exclusion/absence and all original
 # source/DB/PDS bindings. It is not an automatic permission grant.
 with ports.authority.scope():
  if ports.preflight()is not True:raise ValueError('REQUEST_HOST_HANDOFF_UNCONFIRMED')
 if initialize_schema:
  if config.get('schemaInitializationApproved')is not True:raise ValueError('REQUEST_HOST_SCHEMA_APPROVAL_REQUIRED')
  with ports.authority.scope():ports.initialize_schema()
 if ports.startup is not None:
  with ports.authority.scope():ports.startup()
 server=ThreadingHTTPServer((config['host'],config['port']),scoped_handler(ports.handler,ports.authority))
 server.daemon_threads=False
 def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
 previous={s:signal.signal(s,stop) for s in (signal.SIGTERM,signal.SIGINT)}
 try:
  with ports.loops:server.serve_forever(poll_interval=.25)
 finally:
  server.server_close()
  for s,h in previous.items():signal.signal(s,h)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);parser.add_argument('--check',action='store_true');parser.add_argument('--initialize-schema',action='store_true');args=parser.parse_args()
 if args.config.is_symlink():parser.error('REQUEST_HOST_CONFIG_SYMLINK')
 config=json.loads(args.config.read_text());verify(config)
 if args.check:print(json.dumps({'factoryPinned':True,'importsFactory':False,'opensDatabase':False,'startsService':False}));return
 serve(config,load(config),initialize_schema=args.initialize_schema)
if __name__=='__main__':main()
