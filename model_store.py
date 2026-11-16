"""The Models page: which of model_catalog.json's groups are installed, and downloading them in the background.

Downloads run one file at a time in a child Python process (huggingface_hub's hf_hub_download, with hf_xet when it
is installed, like the RunPod template's start.sh), into models/.downloads, then move into place. Progress is the
size of what has arrived so far. HF_TOKEN in the environment is used for gated repositories (Klein).
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid

import folder_paths

LOG = logging.getLogger("minimax_flow")
CATALOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_catalog.json")

# One file in the child process: find it by name anywhere in the repository, download it, print where it went.
FETCH = r"""
import sys
from huggingface_hub import HfApi, hf_hub_download
repo, name, stage = sys.argv[1:4]
paths = [p for p in HfApi().list_repo_files(repo) if p == name or p.endswith("/" + name)]
if not paths:
    sys.exit("NOTFOUND %s isn't in %s" % (name, repo))
print(hf_hub_download(repo, sorted(paths, key=len)[0], local_dir=stage), flush=True)
"""
# The sizes of a repository's files, in the child process too (it needs the network).
SIZES = r"""
import json, sys
from huggingface_hub import HfApi
repo, names = sys.argv[1], sys.argv[2:]
api = HfApi()
paths = {}
for p in api.list_repo_files(repo):
    for n in names:
        if p == n or p.endswith("/" + n):
            paths.setdefault(n, p)
info = {i.path: i.size for i in api.get_paths_info(repo, list(paths.values()))}
print(json.dumps({n: info.get(p) for n, p in paths.items()}))
"""


def catalog():
    with open(CATALOG_FILE, encoding="utf-8") as f:
        return json.load(f)["groups"]


def models_dir():
    return folder_paths.models_dir


def find(name, folder):
    """Where ComfyUI has this file (any folder for that kind of model, any subfolder), or None."""
    kind = folder.split("/")[0]
    direct = os.path.join(models_dir(), folder, name)
    if os.path.isfile(direct) and os.path.getsize(direct) > 0:
        return direct
    try:
        for f in folder_paths.get_filename_list(kind):
            if os.path.basename(f.replace("\\", "/")) == name:
                path = folder_paths.get_full_path(kind, f)
                if path and os.path.getsize(path) > 0:
                    return path
    except Exception:
        pass
    return None


def refresh(folder):
    """ComfyUI caches its model lists and only notices a change in the top folder: forget that one."""
    getattr(folder_paths, "filename_list_cache", {}).pop(folder.split("/")[0], None)


class Store:
    def __init__(self):
        self.queue = []          # group ids waiting
        self.job = None          # the download running: {group, file, index, count, done, total, speed, started}
        self.errors = {}         # group id: why its last download stopped
        self.proc = None
        self.task = None
        self.sizes = {}          # "repo/name": bytes
        self.sizes_file = None
        self._sizes_started = False
        self.on_done = None      # called (group id) when a group is installed

    # ---- Sizes (looked up once, kept in the output folder) -----------------
    def load_sizes(self, out_dir):
        self.sizes_file = os.path.join(out_dir, "model_sizes.json")
        try:
            with open(self.sizes_file, encoding="utf-8") as f:
                self.sizes.update(json.load(f))
        except (OSError, ValueError):
            pass

    def start_sizes(self):
        if self._sizes_started:
            return
        self._sizes_started = True
        threading.Thread(target=self._fill_sizes, daemon=True).start()

    def _fill_sizes(self):
        by_repo = {}
        for g in catalog():
            for repo, name, _ in g["files"]:
                if repo + "/" + name not in self.sizes:
                    by_repo.setdefault(repo, set()).add(name)
        import subprocess
        for repo, names in by_repo.items():
            try:
                out = subprocess.run([sys.executable, "-c", SIZES, repo, *sorted(names)], capture_output=True, text=True, timeout=60)
                for name, size in json.loads(out.stdout.strip().splitlines()[-1]).items():
                    if size:
                        self.sizes[repo + "/" + name] = size
            except Exception as error:
                LOG.info("MiniMax H3 Vision couldn't look up the sizes in %s: %s", repo, error)
        if self.sizes_file:
            try:
                with open(self.sizes_file, "w", encoding="utf-8") as f:
                    json.dump(self.sizes, f)
            except OSError:
                pass

    # ---- What the page shows ----------------------------------------------
    def status(self):
        self.start_sizes()
        groups = []
        for g in catalog():
            files = []
            for repo, name, folder in g["files"]:
                path = find(name, folder)
                size = os.path.getsize(path) if path else self.sizes.get(repo + "/" + name)
                files.append({"repo": repo, "name": name, "folder": folder, "installed": bool(path), "size": size})
            have = sum(f["installed"] for f in files)
            state = "installed" if have == len(files) else "partly" if have else "missing"
            if self.job and self.job["group"] == g["id"]:
                state = "downloading"
            elif g["id"] in self.queue:
                state = "queued"
            groups.append({**{k: g.get(k) for k in ("id", "tab", "name", "about", "env", "gated", "needs")},
                           "files": files, "state": state, "error": self.errors.get(g["id"]),
                           "missingBytes": sum(f["size"] or 0 for f in files if not f["installed"]),
                           "unknownSize": any(not f["installed"] and not f["size"] for f in files),
                           "bytes": sum(f["size"] or 0 for f in files)})
        usage = shutil.disk_usage(models_dir())
        return {"groups": groups, "job": self.job, "queue": list(self.queue),
                "disk": {"free": usage.free, "total": usage.total}, "token": bool(os.environ.get("HF_TOKEN"))}

    # ---- Downloads -----------------------------------------------------------
    def download(self, group_id):
        if not any(g["id"] == group_id for g in catalog()):
            raise KeyError(group_id)
        self.errors.pop(group_id, None)
        if group_id not in self.queue and not (self.job and self.job["group"] == group_id):
            self.queue.append(group_id)
        if not self.task or self.task.done():
            self.task = asyncio.ensure_future(self._run())

    def cancel(self, group_id):
        if group_id in self.queue:
            self.queue.remove(group_id)
        if self.job and self.job["group"] == group_id:
            self.job["cancelled"] = True
            if self.proc and self.proc.returncode is None:
                self.proc.kill()

    def delete(self, group_id):
        """Delete the group's files that no other installed group uses. Returns the names kept because they are shared."""
        groups = catalog()
        group = next(g for g in groups if g["id"] == group_id)
        if self.job and self.job["group"] == group_id:
            raise RuntimeError("It's downloading: cancel it first.")
        others = set()
        for g in groups:
            if g["id"] != group_id and all(find(n, f) for _, n, f in g["files"]):
                others.update(n for _, n, _ in g["files"])
        kept, removed = [], []
        for _, name, folder in group["files"]:
            if name in others:
                kept.append(name)
                continue
            path = find(name, folder)
            if path:
                os.remove(path)
                removed.append(name)
                refresh(folder)
        return {"removed": removed, "kept": kept}

    async def _run(self):
        while self.queue:
            group_id = self.queue.pop(0)
            group = next((g for g in catalog() if g["id"] == group_id), None)
            if not group:
                continue
            todo = [(r, n, f) for r, n, f in group["files"] if not find(n, f)]
            self.job = {"group": group_id, "count": len(todo), "index": 0, "file": None, "done": 0, "total": None,
                        "speed": 0, "started": time.time()}
            try:
                for i, (repo, name, folder) in enumerate(todo):
                    self.job.update({"index": i + 1, "file": name, "done": 0, "total": self.sizes.get(repo + "/" + name), "speed": 0})
                    await self._fetch(repo, name, folder)
                    if self.job.get("cancelled"):
                        break
                if self.job.get("cancelled"):
                    self.errors[group_id] = "Cancelled."
                elif self.on_done:
                    self.on_done(group_id)
            except Exception as error:
                self.errors[group_id] = str(error)
                LOG.warning("MiniMax H3 Vision couldn't download %s: %s", group["name"], error)
            finally:
                self.job = None
                self.proc = None

    async def _fetch(self, repo, name, folder):
        stage = os.path.join(models_dir(), ".downloads", uuid.uuid4().hex[:8])
        os.makedirs(stage, exist_ok=True)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", FETCH, repo, name, stage,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"})
            watch = asyncio.ensure_future(self._watch(stage))
            out, err = await self.proc.communicate()
            watch.cancel()
            if self.job.get("cancelled"):
                return
            if self.proc.returncode != 0:
                raise RuntimeError(self._why(repo, name, err.decode("utf-8", "replace")))
            got = out.decode().strip().splitlines()[-1]
            dest = os.path.join(models_dir(), folder, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(got, dest)
            refresh(folder)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    async def _watch(self, stage):
        last, at = 0, time.time()
        while True:
            await asyncio.sleep(0.5)
            done = 0
            for root, _, files in os.walk(stage):
                for f in files:
                    try:
                        done += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            now = time.time()
            if self.job:
                speed = (done - last) / max(now - at, 1e-3)
                self.job["speed"] = 0.7 * self.job["speed"] + 0.3 * speed if self.job["speed"] else speed
                self.job["done"] = done
            last, at = done, now

    @staticmethod
    def _why(repo, name, err):
        tail = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        if "NOTFOUND" in err:
            return "%s isn't in %s any more." % (name, repo)
        if any(k in err for k in ("GatedRepo", "401", "403", "Access to model", "gated")):
            if not os.environ.get("HF_TOKEN"):
                return ("%s is gated: set HF_TOKEN on the pod (a Hugging Face read token) and accept the license at "
                        "https://huggingface.co/%s." % (repo, repo))
            return "Hugging Face refused %s: accept the license at https://huggingface.co/%s with the account of your HF_TOKEN." % (repo, repo)
        if "No space left" in err:
            return "The disk is full."
        return "Download failed: %s" % tail[:300]


STORE = Store()
