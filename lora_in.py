"""LoRAs into ComfyUI's loras folder from the LoRA window: uploaded from the browser in pieces, or fetched by the
machine ComfyUI runs on from a Hugging Face, Civitai or other https link.

Uploads come in pieces of a few MB (RunPod's proxy cuts off long requests), each written at the offset the server
expects, so a piece that failed is simply sent again. The file is checked to be a safetensors file before it is
moved into place. Links run in the background, one at a time, with progress and Cancel.
"""

import asyncio
import json
import os
import re
import shutil
import struct
import sys
import time
import urllib.parse
import urllib.request
import uuid

import folder_paths

from . import model_store

FOLDERS = ["MiniMax H3", "MOSS Image", "Flux Klein", "P-H3 Image", ""]
CHUNK_MAX = 64 * 1024 * 1024
UA = "MiniMax-H3-Vision"

# A Hugging Face file in the child process (hf_xet when installed, like the Models page): prints its size, then its path.
HF_FETCH = r"""
import sys
from huggingface_hub import HfApi, hf_hub_download
repo, rev, path, stage = sys.argv[1:5]
try:
    info = HfApi().get_paths_info(repo, [path], revision=rev)
    print("SIZE %d" % (info[0].size or 0), flush=True)
except Exception:
    pass
print("DONE " + hf_hub_download(repo, path, revision=rev, local_dir=stage), flush=True)
"""


def loras_dir():
    return folder_paths.get_folder_paths("loras")[0]


def clean_name(name):
    """A plain file name ending in .safetensors, or ValueError."""
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    if not name or name.startswith("."):
        raise ValueError("That file has no name.")
    if not name.lower().endswith(".safetensors"):
        raise ValueError("Only .safetensors LoRAs can be added.")
    return name


def clean_folder(folder):
    folder = str(folder or "")
    if folder not in FOLDERS:
        raise ValueError("Unknown LoRA folder.")
    return folder


def check_safetensors(path):
    """ValueError unless the file starts like a safetensors file (an 8-byte header size, then a JSON header)."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise ValueError("That file is too small to be a LoRA.")
        n = struct.unpack("<Q", head)[0]
        if n <= 2 or n > 100 * 1024 * 1024 or n + 8 > os.path.getsize(path):
            raise ValueError("That isn't a safetensors file.")
        start = f.read(min(n, 4096)).lstrip()
        if not start.startswith(b"{"):
            raise ValueError("That isn't a safetensors file.")


def stage_dir(*parts):
    """Partial files live outside the loras folder, so ComfyUI never lists one."""
    base = os.path.join(getattr(folder_paths, "models_dir", os.path.dirname(loras_dir())), ".downloads", "loras", *parts)
    os.makedirs(base, exist_ok=True)
    return base


def destination(folder, name):
    return os.path.join(loras_dir(), folder, name) if folder else os.path.join(loras_dir(), name)


def refresh():
    getattr(folder_paths, "filename_list_cache", {}).pop("loras", None)


class Incoming:
    def __init__(self):
        self.uploads = {}        # id: {name, folder, size, path, offset}
        self.jobs = {}           # id: a link being fetched (see link())
        self.order = []
        self.task = None
        self.on_done = None      # called when a LoRA is in

    # ---- Uploads ---------------------------------------------------------------
    def start_upload(self, name, folder, size):
        name, folder = clean_name(name), clean_folder(folder)
        size = int(size)
        if size <= 8:
            raise ValueError("That file is empty.")
        if shutil.disk_usage(loras_dir()).free < size + 512 * 1024 * 1024:
            raise ValueError("There isn't enough free disk space for it.")
        self.sweep()
        uid = uuid.uuid4().hex[:12]
        path = os.path.join(stage_dir(), uid + ".part")
        open(path, "wb").close()
        self.uploads[uid] = {"name": name, "folder": folder, "size": size, "path": path, "offset": 0, "at": time.time()}
        return {"id": uid, "offset": 0, "exists": os.path.exists(destination(folder, name))}

    def sweep(self):
        """Uploads left behind (the page was closed, or ComfyUI restarted) are removed after an hour."""
        now = time.time()
        for uid in [u for u, up in self.uploads.items() if now - up["at"] > 3600]:
            self.cancel_upload(uid)
        base = stage_dir()
        for f in os.listdir(base):
            path = os.path.join(base, f)
            if f.endswith(".part") and f[:-5] not in self.uploads:
                try:
                    if now - os.path.getmtime(path) > 3600:
                        os.remove(path)
                except OSError:
                    pass

    def write(self, uid, offset, data):
        up = self.uploads.get(uid)
        if not up:
            raise KeyError(uid)
        offset = int(offset)
        if offset != up["offset"]:
            return up["offset"]            # the client sends from where the server is
        if len(data) > CHUNK_MAX or up["offset"] + len(data) > up["size"]:
            raise ValueError("That piece doesn't fit the file.")
        with open(up["path"], "r+b") as f:
            f.seek(offset)
            f.write(data)
        up["offset"] += len(data)
        up["at"] = time.time()
        return up["offset"]

    def finish(self, uid, overwrite):
        up = self.uploads.get(uid)
        if not up:
            raise KeyError(uid)
        if up["offset"] != up["size"]:
            raise ValueError("The upload isn't complete.")
        dest = destination(up["folder"], up["name"])
        if os.path.exists(dest) and not overwrite:
            raise FileExistsError(up["name"])
        try:
            check_safetensors(up["path"])
        except ValueError:
            self.cancel_upload(uid)
            raise
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(up["path"], dest)
        del self.uploads[uid]
        refresh()
        return os.path.relpath(dest, loras_dir()).replace(os.sep, "/")

    def cancel_upload(self, uid):
        up = self.uploads.pop(uid, None)
        if up:
            try:
                os.remove(up["path"])
            except OSError:
                pass

    # ---- Links -------------------------------------------------------------------
    def link(self, url, folder):
        folder = clean_folder(folder)
        url = str(url or "").strip()
        src = parse_link(url)
        jid = uuid.uuid4().hex[:10]
        self.jobs[jid] = {"id": jid, "url": url, "folder": folder, "name": src.get("name") or "", "state": "queued",
                          "done": 0, "total": None, "speed": 0, "error": None, "src": src, "at": time.time()}
        self.order.append(jid)
        if not self.task or self.task.done():
            self.task = asyncio.ensure_future(self._run())
        return self.view(jid)

    def cancel_job(self, jid):
        job = self.jobs.get(jid)
        if not job:
            return
        if job["state"] == "queued":
            job["state"], job["error"] = "cancelled", "Cancelled."
        elif job["state"] == "downloading":
            job["cancel"] = True
            proc = job.get("proc")
            if proc and proc.returncode is None:
                proc.kill()

    def view(self, jid):
        j = self.jobs[jid]
        return {k: j[k] for k in ("id", "url", "folder", "name", "state", "done", "total", "speed", "error", "file", "note", "at") if k in j}

    def status(self):
        # Finished links are shown for a while, then dropped.
        now = time.time()
        for jid in [j for j in self.order if self.jobs[j]["state"] in ("done", "failed", "cancelled")
                    and now - self.jobs[j].get("ended", now) > 600]:
            self.order.remove(jid)
            self.jobs.pop(jid, None)
        return [self.view(j) for j in self.order]

    async def _run(self):
        while True:
            jid = next((j for j in self.order if self.jobs[j]["state"] == "queued"), None)
            if not jid:
                return
            job = self.jobs[jid]
            job["state"] = "downloading"
            stage = stage_dir("link-" + jid)
            try:
                got = await (self._hf(job, stage) if job["src"]["kind"] == "hf" else self._http(job, stage))
                if job.get("cancel"):
                    raise asyncio.CancelledError()
                check_safetensors(got)
                name = clean_name(job["name"] or os.path.basename(got))
                dest = destination(job["folder"], name)
                if os.path.exists(dest):
                    if os.path.getsize(dest) == os.path.getsize(got):
                        job["note"] = "already there"
                    else:
                        stem, ext = os.path.splitext(name)
                        n = 2
                        while os.path.exists(destination(job["folder"], "%s_%d%s" % (stem, n, ext))):
                            n += 1
                        name = "%s_%d%s" % (stem, n, ext)
                        dest = destination(job["folder"], name)
                if not job.get("note"):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(got, dest)
                job.update({"state": "done", "name": name, "file": os.path.relpath(dest, loras_dir()).replace(os.sep, "/")})
                refresh()
                if self.on_done:
                    self.on_done(job["file"])
            except asyncio.CancelledError:
                job.update({"state": "cancelled", "error": "Cancelled."})
            except Exception as error:
                job.update({"state": "failed", "error": str(error) or type(error).__name__})
            finally:
                job["ended"] = time.time()
                job.pop("proc", None)
                shutil.rmtree(stage, ignore_errors=True)

    async def _hf(self, job, stage):
        src = job["src"]
        job["name"] = job["name"] or os.path.basename(src["path"])
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", HF_FETCH, src["repo"], src["rev"], src["path"], stage,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=model_store.child_env())   # with the Hugging Face token from the Models page, for gated LoRAs
        job["proc"] = proc
        watch = asyncio.ensure_future(self._watch(job, stage))
        path = None
        async for line in proc.stdout:
            line = line.decode().strip()
            if line.startswith("SIZE "):
                job["total"] = int(line[5:]) or None
            elif line.startswith("DONE "):
                path = line[5:]
        err = (await proc.stderr.read()).decode("utf-8", "replace")
        await proc.wait()
        watch.cancel()
        if job.get("cancel"):
            raise asyncio.CancelledError()
        if proc.returncode != 0 or not path:
            if "EntryNotFound" in err or "404 Client Error" in err:
                raise RuntimeError("That file isn't on Hugging Face (check the link).")
            if any(k in err for k in ("GatedRepo", "RepositoryNotFound", "401 Client Error", "403 Client Error")):
                raise RuntimeError("Hugging Face refused it: the repository is gated, private or doesn't exist. For a gated "
                                   "one, paste a Hugging Face token on the Models page (gear menu) and accept its terms on huggingface.co.")
            raise RuntimeError("Download failed: " + (err.strip().splitlines() or ["unknown error"])[-1][:300])
        return path

    async def _http(self, job, stage):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._http_blocking, job, stage)

    def _http_blocking(self, job, stage):
        src = job["src"]
        url = src["url"]
        headers = {"User-Agent": UA}
        if src["kind"] == "civitai":
            if src.get("model") and not src.get("version"):
                info = json.load(urllib.request.urlopen(urllib.request.Request(
                    "https://civitai.com/api/v1/models/%s" % src["model"], headers=headers), timeout=30))
                versions = info.get("modelVersions") or []
                if not versions:
                    raise RuntimeError("That Civitai model has no files.")
                url = "https://civitai.com/api/download/models/%s" % versions[0]["id"]
            token = os.environ.get("CIVITAI_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
        try:
            res = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("The site refused it (%d)%s." % (error.code, ": set CIVITAI_TOKEN on the pod" if src["kind"] == "civitai" else ""))
            raise RuntimeError("The site answered %d." % error.code)
        name = job["name"]
        disp = res.headers.get("Content-Disposition") or ""
        m = re.search(r'filename\*=UTF-8\'\'([^;]+)', disp) or re.search(r'filename="?([^";]+)"?', disp)
        if m:
            name = urllib.parse.unquote(m.group(1))
        name = name or os.path.basename(urllib.parse.urlparse(res.url).path)
        job["name"] = clean_name(name)
        job["total"] = int(res.headers.get("Content-Length") or 0) or None
        path = os.path.join(stage, job["name"])
        last, at = 0, time.time()
        with open(path, "wb") as f:
            while True:
                if job.get("cancel"):
                    raise asyncio.CancelledError()
                block = res.read(1024 * 1024)
                if not block:
                    break
                f.write(block)
                job["done"] += len(block)
                now = time.time()
                if now - at >= 0.5:
                    speed = (job["done"] - last) / (now - at)
                    job["speed"] = 0.7 * job["speed"] + 0.3 * speed if job["speed"] else speed
                    last, at = job["done"], now
        return path

    async def _watch(self, job, stage):
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
            speed = (done - last) / max(now - at, 1e-3)
            job["speed"] = 0.7 * job["speed"] + 0.3 * speed if job["speed"] else speed
            job["done"] = done
            last, at = done, now


def parse_link(url):
    """What to fetch for a link: Hugging Face (a file's page or resolve link), Civitai (a model page, with or without
    modelVersionId, or an api/download link), or any https link to a .safetensors file. ValueError otherwise."""
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or not p.netloc:
        raise ValueError("Paste an https link to a LoRA (Hugging Face or Civitai).")
    host = p.netloc.lower()
    parts = [urllib.parse.unquote(x) for x in p.path.split("/") if x]
    if host in ("huggingface.co", "www.huggingface.co", "hf.co"):
        if len(parts) >= 5 and parts[2] in ("blob", "resolve"):
            return {"kind": "hf", "repo": parts[0] + "/" + parts[1], "rev": parts[3], "path": "/".join(parts[4:]),
                    "name": parts[-1] if parts[-1].lower().endswith(".safetensors") else ""}
        raise ValueError("Link to the LoRA file on Hugging Face (open the file and copy its address), not the repository.")
    if host.endswith("civitai.com"):
        q = urllib.parse.parse_qs(p.query)
        if len(parts) >= 4 and parts[:3] == ["api", "download", "models"]:
            return {"kind": "civitai", "url": "https://civitai.com/api/download/models/%s" % parts[3], "version": parts[3]}
        if len(parts) >= 2 and parts[0] == "models" and parts[1].isdigit():
            version = (q.get("modelVersionId") or [None])[0]
            if version:
                return {"kind": "civitai", "url": "https://civitai.com/api/download/models/%s" % version, "version": version}
            return {"kind": "civitai", "url": None, "model": parts[1]}
        raise ValueError("Link to a Civitai model page (civitai.com/models/…).")
    if p.path.lower().endswith(".safetensors"):
        return {"kind": "http", "url": url, "name": os.path.basename(p.path)}
    raise ValueError("Paste a Hugging Face or Civitai link, or an https link to a .safetensors file.")


INCOMING = Incoming()
