"""HQ music draft publication through the same fenced PDS artifact transport."""
from .image_pds import ImageWorkspace,MediaArtifactClient
class MusicWorkspace(ImageWorkspace):
 capability_prefix='music.';mime='audio/wav';model=None
