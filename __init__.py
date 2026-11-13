"""MiniMax Flow: a simple prompt-and-gallery video studio for MiniMax H3, inside ComfyUI.

Relative imports only: ComfyUI loads custom node packs as packages.
"""

from . import flow_server

# This pack adds no graph nodes of its own; it adds a page and its server routes.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

flow_server.install()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
