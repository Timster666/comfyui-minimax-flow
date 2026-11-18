"""The prompt builder's library: categories, subcategories and prompts, kept in minimax_flow/prompt_library.json and,
when Google Drive is set up (GDRIVE_FOLDER, GDRIVE_TOKEN), in vision-prompts.json in the Drive folder too.

The library is a flat list of nodes, each with an id, a kind ("cat", "sub" or "prompt"), a parent, a place among its
siblings ("pos") and the time it last changed ("at"), plus a list of deleted ids with the time they were deleted.
Two copies (two pods, or the pod and Drive) are merged node by node: the newer change of a node wins, and a delete
wins over changes made before it. So an edit on one pod doesn't wipe a new prompt made on another.

On the first read after ComfyUI starts, the Drive copy is fetched and merged in. After a change, the library is
written here at once and to Drive a few seconds later (fetch, merge, upload), so a burst of edits is one upload.
"""

import asyncio
import json
import logging
import os
import time
import uuid

from . import gdrive

LOG = logging.getLogger("minimax_flow")
DRIVE_NAME = "vision-prompts.json"
KINDS = {"cat": None, "sub": "cat", "prompt": ("cat", "sub")}
LIMITS = {"name": 120, "text": 8000, "nodes": 20000}
DELAY = 3.0


def empty():
    return {"version": 1, "nodes": [], "deleted": {}}


def clean(lib):
    """The library with only what belongs in it, or ValueError."""
    if not isinstance(lib, dict) or not isinstance(lib.get("nodes"), list):
        raise ValueError("That isn't a prompt library.")
    nodes, seen = [], set()
    for n in lib["nodes"][:LIMITS["nodes"]]:
        if not isinstance(n, dict) or n.get("kind") not in KINDS or not isinstance(n.get("id"), str) or n["id"] in seen:
            continue
        seen.add(n["id"])
        node = {"id": n["id"][:40], "kind": n["kind"], "parent": n.get("parent") if n["kind"] != "cat" else None,
                "pos": float(n.get("pos") or 0), "at": float(n.get("at") or 0)}
        if n["kind"] == "prompt":
            node["text"] = str(n.get("text") or "")[:LIMITS["text"]]
            node["name"] = str(n.get("name") or "")[:LIMITS["name"]]
        else:
            node["name"] = str(n.get("name") or "Untitled")[:LIMITS["name"]]
        nodes.append(node)
    deleted = {str(k)[:40]: float(v) for k, v in (lib.get("deleted") or {}).items() if isinstance(v, (int, float))}
    return {"version": 1, "nodes": nodes, "deleted": deleted}


def merge(a, b):
    """Two copies as one: per node the newer change, deletes kept, and what lost its parent moved to the top."""
    nodes = {}
    for n in a["nodes"] + b["nodes"]:
        if n["id"] not in nodes or n["at"] > nodes[n["id"]]["at"]:
            nodes[n["id"]] = n
    deleted = dict(a["deleted"])
    for k, v in b["deleted"].items():
        deleted[k] = max(v, deleted.get(k, 0))
    nodes = {k: n for k, n in nodes.items() if not (k in deleted and deleted[k] >= n["at"])}
    # A node whose category was deleted on the other copy goes with it, except prompts changed since: those are kept,
    # in an "Unsorted" category, so nothing written is lost.
    gone = [k for k, n in nodes.items() if n["parent"] and n["parent"] not in nodes]
    rescue = None
    for k in gone:
        n = nodes[k]
        if n["kind"] == "prompt":
            if not rescue:
                rescue = next((x for x in nodes.values() if x["kind"] == "cat" and x["name"] == "Unsorted"), None)
                if not rescue:
                    rescue = {"id": "c" + uuid.uuid4().hex[:10], "kind": "cat", "parent": None, "name": "Unsorted",
                              "pos": max([x["pos"] for x in nodes.values() if x["kind"] == "cat"] or [0]) + 1, "at": time.time()}
                    nodes[rescue["id"]] = rescue
            nodes[k] = {**n, "parent": rescue["id"]}
        else:
            del nodes[k]
    # Prompts under a deleted subcategory were moved above; anything still dangling goes.
    nodes = {k: n for k, n in nodes.items() if not n["parent"] or n["parent"] in nodes}
    cutoff = time.time() - 90 * 86400   # deletes are remembered for 90 days
    return {"version": 1, "nodes": sorted(nodes.values(), key=lambda n: (n["pos"], n["id"])),
            "deleted": {k: v for k, v in deleted.items() if v > cutoff}}


class Library:
    def __init__(self):
        self.path = None
        self.lib = None
        self.pulled = False
        self.timer = None
        self.lock = asyncio.Lock()
        self.drive = {"state": "off", "error": None, "at": None}
        self.on_change = None    # told when a sync brought in changes from Drive

    def load(self, path):
        if self.lib is not None:
            return
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.lib = clean(json.load(f))
        except (OSError, ValueError):
            self.lib = empty()

    def write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.lib, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def drive_on(self):
        return gdrive.problem() is None

    def view(self):
        if not self.drive_on():
            self.drive = {**self.drive, "state": "off"}
        return {"lib": self.lib, "drive": self.drive}

    async def get(self, path):
        self.load(path)
        if not self.pulled and self.drive_on():
            self.pulled = True
            try:
                await asyncio.wait_for(self.sync(push=False), 15)
            except asyncio.TimeoutError:
                self.drive = {"state": "error", "error": "Google Drive didn't answer in time.", "at": None}
        return self.view()

    async def put(self, path, lib):
        self.load(path)
        lib = clean(lib)
        async with self.lock:
            self.lib = merge(self.lib, lib)
            self.write()
        if self.drive_on():
            self.drive = {**self.drive, "state": "pending", "error": None}
            if self.timer:
                self.timer.cancel()
            self.timer = asyncio.get_running_loop().call_later(DELAY, lambda: asyncio.ensure_future(self.sync()))
        return self.view()

    async def sync(self, push=True):
        """Fetch the Drive copy, merge it in, and (when push, or when the Drive copy lacked something) upload."""
        d = gdrive.DRIVE
        stage = self.path + ".drive"
        try:
            self.drive = {**self.drive, "state": "syncing", "error": None}
            listed = json.loads(await d.run("lsjson", gdrive.remote(), "--files-only", "--max-depth", "1",
                                            "--include", DRIVE_NAME) or "[]")
            theirs = empty()
            if listed:
                await d.run("copyto", gdrive.remote(DRIVE_NAME), stage)
                with open(stage, encoding="utf-8") as f:
                    theirs = clean(json.load(f))
            async with self.lock:
                merged = merge(self.lib, theirs)
                changed_here = merged != self.lib
                self.lib = merged
                if changed_here:
                    self.write()
            if push or merged != theirs:
                with open(stage, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False)
                await d.run("copyto", stage, gdrive.remote(DRIVE_NAME))
            self.drive = {"state": "synced", "error": None, "at": time.time()}
            if changed_here and self.on_change:
                self.on_change()
            return changed_here
        except Exception as error:
            LOG.warning("MiniMax H3 Vision couldn't sync the prompt library with Google Drive: %s", error)
            self.drive = {"state": "error", "error": str(error) or type(error).__name__, "at": None}
            return False
        finally:
            try:
                os.remove(stage)
            except OSError:
                pass


LIBRARY = Library()
