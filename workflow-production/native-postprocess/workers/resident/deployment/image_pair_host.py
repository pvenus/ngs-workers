"""Fixed-export image bindings. No claims, service activation or native execution."""
import importlib,sys
from pathlib import Path
from .host_media import trusted_json,file_hash
from ..backends.portrait_pair import native_pair_runner_factory
from ..backends.pose_direction import native_pose_runner_factory
from ..terminal_evidence import SourcePin

def bindings_from_host(config_path,*,cleanup):
 config=trusted_json(config_path)
 if set(config)!={'schemaVersion','workersRoot','ngsRoot','binary','binarySha256','outputRoots','filePins'}or config['schemaVersion']!='resident.image-pair-host.v1' or not callable(cleanup):raise ValueError('PAIR_HOST_CONFIG_SCHEMA')
 workers=Path(config['workersRoot']);ngs=Path(config['ngsRoot'])
 if workers!=Path(__file__).resolve().parents[3] or ngs.resolve(strict=True)!=ngs or ngs.is_symlink():raise ValueError('PAIR_HOST_RELEASE_SCOPE')
 binary=Path(config['binary'])
 if file_hash(binary)!=config['binarySha256']:raise ValueError('PAIR_HOST_BINARY_CHANGED')
 required={str(ngs/n)for n in ('local/services/npc_workflow/portrait_stage.py','local/services/npc_workflow/portrait_pairs.py','local/services/story_node_images/native.py','local/services/portrait/png_alpha.py','local/services/pose_requests/worker.py')}
 required.update(str(workers/n)for n in ('workers/resident/backends/portrait_pair.py','workers/resident/backends/image_native.py','workers/resident/adapters/portrait_pair_workspace.py','workers/resident/adapters/image_pds.py','workers/resident/deployment/role_factory.py','workers/resident/deployment/image_pair_host.py','workers/resident/backends/pose_direction.py','workers/resident/adapters/pose_direction_workspace.py'))
 if not required<=set(config['filePins']):raise ValueError('PAIR_HOST_SOURCE_PINS_REQUIRED')
 for path,sha in config['filePins'].items():
  p=Path(path)
  if p.resolve(strict=True)!=p or not (p.is_relative_to(workers)or p.is_relative_to(ngs)) or file_hash(p)!=sha:raise ValueError('PAIR_HOST_SOURCE_PIN_CHANGED')
 sys.path.insert(0,str(ngs))
 try:
  stage=importlib.import_module('local.services.npc_workflow.portrait_stage');pairs=importlib.import_module('local.services.npc_workflow.portrait_pairs');alpha=importlib.import_module('local.services.portrait.png_alpha')
  pose=importlib.import_module('local.services.pose_requests.worker')
  for module in (stage,pairs,alpha,pose):
   if not Path(module.__file__).resolve().is_relative_to(ngs):raise ValueError('PAIR_HOST_IMPORTED_RELEASE_CHANGED')
 finally:sys.path.remove(str(ngs))
 runner=native_pair_runner_factory(binary=str(binary),runner_type=stage.AggregatePortraitRunner,approved_output_roots=config['outputRoots'])
 return {'pose_direction_runner_factory':native_pose_runner_factory(binary=str(binary),runner_type=stage.AggregatePortraitRunner,approved_output_roots=config['outputRoots']),'pose_worker_module':pose,'pose_alpha_validator':alpha.validate_output_contract,'portrait_pair_runner_factory':runner,'portrait_pair_module':pairs,'portrait_alpha_validator':alpha.validate_stage_output,'cleanup':cleanup,'terminal_pins':tuple(SourcePin(path,sha)for path,sha in sorted(config['filePins'].items()))}
