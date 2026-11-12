"""Compatibility alias for existing source-tree tests."""
import sys
from imageforge_mcp import http_server
sys.modules[__name__] = http_server
