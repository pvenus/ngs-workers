"""Independent role-process entrypoint. No deployment activates by default."""
import argparse,json,signal
from dataclasses import asdict
from pathlib import Path
from .contracts import PROFILES, ExecutionStopUnconfirmed
from .deployment.composition import verify,compose

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('role',choices=PROFILES)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=None
    if args.config:
        if args.config.is_symlink() or not args.config.is_file():parser.error('WORKER_CONFIG_INVALID')
        config=json.loads(args.config.read_text());verify(config,args.role)
    if args.check:
        print(json.dumps({'profile':asdict(PROFILES[args.role]),'loadsModel':False,'claimsRequest':False,
                          'startsService':False,'productionReady':False,'compositionPinned':config is not None},sort_keys=True));return
    if config is None:parser.error('FORMAL_REQUEST_PDS_BACKEND_ADAPTERS_NOT_INTEGRATED: explicit pinned config required')
    worker=compose(config,args.role)
    def stop(*_):
        worker.stop.set()
        if worker.active_cancel is not None:worker.active_cancel.set()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    stopped=True
    try:
        if args.once:worker.poll_once()
        else:
            while not worker.stop.is_set():
                worked=worker.poll_once()
                if not worked:worker.stop.wait(.5)
    except ExecutionStopUnconfirmed:
        stopped=False
        raise
    finally:
        if stopped:worker.shutdown()

if __name__=='__main__':main()
