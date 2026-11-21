"""Trusted composition only; no user-selected imports, commands or runtime paths."""
from ..backends.image_generation import ImageGenerationBackend
from ..backends.image_native import NativeImageProvider
from ..backends.music_generation import MusicGenerationBackend,hq_engine_factory

def image_backend(*,binary,runner_builder):
 if not callable(runner_builder):raise ValueError('IMAGE_NATIVE_RUNNER_BINDING_REQUIRED')
 return ImageGenerationBackend(lambda workspace:NativeImageProvider(workspace,binary=binary,runner_builder=runner_builder))

def music_backend(*,runtime_root,runner_path,python_path,lock_fds,lock_validator,enabled,timeout=1100):
 return MusicGenerationBackend(hq_engine_factory(runtime_root=runtime_root,runner_path=runner_path,
  python_path=python_path,lock_fds=lock_fds,lock_validator=lock_validator,enabled=enabled,timeout=timeout))

def pinned_native_runner(runner_type,*,approved_output_roots):
 """Deployment passes the existing reviewed runner class, not an import string.

 Its checked()/verified() logic and source-specific transparency contracts remain
 intact. Approved native tool output roots are service-configured read roots;
 they are never copied from the user DTO. Worker-written files stay in workspace.
 """
 from pathlib import Path
 roots=tuple(Path(p).resolve(strict=True)for p in approved_output_roots)
 if not roots or not callable(runner_type):raise ValueError('IMAGE_NATIVE_TRUSTED_RUNNER_REQUIRED')
 def build(client,workspace):
  return runner_type(client,workspace/'native-journal',roots,(workspace,))
 return build

def media_workspace(*,role,root,pds_url,pds_token,source_reader,cleanup):
 from ..adapters.image_pds import ImageWorkspace,MediaArtifactClient
 from ..adapters.music_pds import MusicWorkspace
 if role not in ('image','music')or not callable(source_reader)or not callable(cleanup):raise ValueError('MEDIA_DEPLOYMENT_BINDING_REQUIRED')
 cls=ImageWorkspace if role=='image'else MusicWorkspace
 return cls(root,MediaArtifactClient(pds_url,pds_token),source_reader,cleanup)

def portrait_source(*,pds_url,guard_provider,stage_request_id):
 from ..adapters.pds_animation import AnimationPublicationClient
 from ..adapters.image_pds import PortraitSource,PortraitContextReader
 client=AnimationPublicationClient(pds_url,guard_provider,stage_request_id)
 return PortraitSource(PortraitContextReader(client),client.asset)
