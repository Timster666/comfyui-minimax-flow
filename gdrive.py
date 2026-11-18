"""Edit projects (.zip) saved to and opened from a Google Drive folder, with rclone.

The pod is given the folder's link (GDRIVE_FOLDER) and a Google sign-in made once with `rclone authorize "drive"`
(GDRIVE_TOKEN). rclone is set up from these on each run (no config file), and does the transfers straight between
the pod and Drive. One transfer at a time, with progress and Cancel.
"""

import asyncio
import glob
import json
import os
import re
import shutil
import time
import uuid

LOG_TAIL = 20


def folder_id(link):
    """The folder's id from its link (…/folders/<id>, ?id=<id>), or the id itself."""
    link = (link or "").strip()
    m = re.search(r"/folders/([\w-]{10,})", link) or re.search(r"[?&]id=([\w-]{10,})", link)
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[\w-]{10,}", link) else None


def token():
    """The token rclone printed: its JSON, even when the lines around it were pasted too."""
    raw = os.environ.get("GDRIVE_TOKEN", "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return json.dumps(data) if data.get("refresh_token") or data.get("access_token") else None


def rclone():
    return shutil.which("rclone")


def problem():
    """Why Drive can't be used, or None. None of the variables set: Drive is simply off ("off")."""
    if not os.environ.get("GDRIVE_FOLDER") and not os.environ.get("GDRIVE_TOKEN"):
        return "off"
    if not rclone():
        return "rclone isn't installed on the pod (it comes with the template's image)."
    if not folder_id(os.environ.get("GDRIVE_FOLDER")):
        return "GDRIVE_FOLDER isn't a Google Drive folder link."
    if not token():
        return "GDRIVE_TOKEN isn't the token rclone authorize \"drive\" prints (the part in { })."
    return None


def remote_env():
    return {**os.environ, "RCLONE_CONFIG_GD_TYPE": "drive", "RCLONE_CONFIG_GD_SCOPE": "drive",
            "RCLONE_CONFIG_GD_TOKEN": token() or "", "RCLONE_CONFIG_GD_ROOT_FOLDER_ID": folder_id(os.environ.get("GDRIVE_FOLDER")) or "",
            "RCLONE_CONFIG": os.devnull}


def remote(name=""):
    return "gd:" + name


def why(lines):
    """A short reason from rclone's error lines."""
    text = " ".join(lines)
    if "invalid_grant" in text or "token expired" in text.lower() or "unauthorized" in text.lower():
        return "Google turned the sign-in down: make a new GDRIVE_TOKEN with rclone authorize \"drive\"."
    if "notFound" in text or "File not found" in text or "directory not found" in text:
        return "The folder (or file) isn't there, or this Google account can't see it. Check GDRIVE_FOLDER."
    if "storageQuotaExceeded" in text or "quota" in text.lower():
        return "Google Drive is full."
    return (lines[-1] if lines else "rclone failed")[:300]


class Drive:
    def __init__(self):
        self.jobs = {}
        self.order = []
        self.task = None
        self.cache = None          # the folder's zips, read again after a save and when asked

    async def run(self, *args, job=None):
        """rclone with the Drive set up; progress into job. Returns stdout, or raises RuntimeError."""
        proc = await asyncio.create_subprocess_exec(
            rclone(), *args, "--use-json-log", "-v", "--stats", "1s", "--retries", "5", "--low-level-retries", "20",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=remote_env())
        if job is not None:
            job["proc"] = proc
        errors = []

        async def read_log():
            async for line in proc.stderr:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                stats = entry.get("stats")
                if stats and job is not None:
                    job["done"] = stats.get("bytes", job["done"])
                    job["total"] = stats.get("totalBytes") or job["total"]
                    now = [t for t in stats.get("transferring") or [] if t.get("speed")]
                    job["speed"] = now[0]["speed"] if now else stats.get("speed") or job["speed"]
                if entry.get("level") in ("error", "critical"):
                    errors.append(entry.get("msg", "").strip())
                    del errors[:-LOG_TAIL]

        out, _ = await asyncio.gather(proc.stdout.read(), read_log())
        await proc.wait()
        if job is not None:
            job.pop("proc", None)
            if job.get("cancel"):
                raise asyncio.CancelledError()
        if proc.returncode != 0:
            raise RuntimeError(why(errors))
        return out.decode("utf-8", "replace")

    async def files(self, fresh=False):
        if self.cache is None or fresh:
            out = await self.run("lsjson", remote(), "--files-only", "--max-depth", "1")
            items = [f for f in json.loads(out or "[]") if f["Name"].lower().endswith(".zip")]
            items.sort(key=lambda f: f.get("ModTime", ""), reverse=True)
            self.cache = [{"name": f["Name"], "size": f.get("Size", 0), "modified": f.get("ModTime")} for f in items]
        return self.cache

    # ---- Jobs ------------------------------------------------------------------
    def add(self, kind, name, **extra):
        jid = uuid.uuid4().hex[:10]
        self.jobs[jid] = {"id": jid, "kind": kind, "name": name, "state": "queued", "done": 0, "total": None,
                          "speed": 0, "error": None, "at": time.time(), **extra}
        self.order.append(jid)
        if not self.task or self.task.done():
            self.task = asyncio.ensure_future(self._loop())
        return self.view(jid)

    def view(self, jid):
        j = self.jobs[jid]
        return {k: j[k] for k in ("id", "kind", "name", "state", "done", "total", "speed", "error", "at", "result") if k in j}

    def status(self):
        now = time.time()
        for jid in [j for j in self.order if self.jobs[j]["state"] in ("done", "failed", "cancelled")
                    and now - self.jobs[j].get("ended", now) > 900]:
            self.order.remove(jid)
            self.jobs.pop(jid, None)
        return [self.view(j) for j in self.order]

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job or job["state"] in ("done", "failed", "cancelled"):
            return
        job["cancel"] = True
        if job["state"] == "queued":
            job.update(state="cancelled", error="Cancelled.", ended=time.time())
        elif job.get("proc") and job["proc"].returncode is None:
            job["proc"].kill()

    async def _loop(self):
        while True:
            jid = next((j for j in self.order if self.jobs[j]["state"] == "queued"), None)
            if not jid:
                return
            job = self.jobs[jid]
            try:
                await (self._save(job) if job["kind"] == "save" else self._open(job))
                job["state"] = "done"
            except asyncio.CancelledError:
                job.update(state="cancelled", error="Cancelled.")
            except Exception as error:
                job.update(state="failed", error=str(error) or type(error).__name__)
            finally:
                job["ended"] = time.time()
                for path in job.pop("cleanup", []):
                    # with rclone's partial file when it was stopped half-way
                    for f in glob.glob(glob.escape(path) + "*"):
                        try:
                            os.remove(f)
                        except OSError:
                            pass

    async def _save(self, job):
        """Make the zip (job["make"]: an async function giving its path), then copy it into the folder under a
        name that isn't taken there yet."""
        job["state"] = "packing"
        path = await job.pop("make")()
        if job.get("cancel"):
            raise asyncio.CancelledError()
        taken = {f["name"].lower() for f in await self.files(fresh=True)}
        stem, ext = os.path.splitext(os.path.basename(path))
        name, n = stem + ext, 2
        while name.lower() in taken:
            name, n = "%s_%d%s" % (stem, n, ext), n + 1
        job.update(name=name, state="sending", total=os.path.getsize(path))
        await self.run("copyto", path, remote(name), job=job)
        job["done"] = job["total"]
        self.cache = None

    async def _open(self, job):
        """Copy the zip down, then open it (job["opener"]: an async function given its path and name)."""
        job["state"] = "fetching"
        temp = job.pop("temp")
        job.setdefault("cleanup", []).append(temp)
        await self.run("copyto", remote(job["name"]), temp, job=job)
        if job.get("cancel"):
            raise asyncio.CancelledError()
        job["state"] = "opening"
        job["result"] = await job.pop("opener")(temp, job["name"])


DRIVE = Drive()
