"""MiniMax H3 Vision: a simple prompt-and-gallery studio for MiniMax H3 videos and MOSS-Image pictures, inside ComfyUI.

It needs no other custom node pack: its video graphs use ComfyUI's own MiniMax H3 nodes and the few
nodes this pack adds (vision_nodes.py). Relative imports only: ComfyUI loads custom node packs as packages.
"""

from . import flow_server
from .vision_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

flow_server.install()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
