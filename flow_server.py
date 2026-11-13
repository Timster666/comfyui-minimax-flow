"""The server side of MiniMax H3 Vision: its page, its API, and the renderer.

Everything lives in ComfyUI's output folder under minimax_flow/:
    project.json   images, clips and the last-used settings
    images/        uploaded reference images
    clips/         finished videos
Files there are served by ComfyUI's own /view endpoint.

Renders are real when the H3 editor pack and its models are installed (see h3_bridge.py),
and fake otherwise: a fake render reports its steps like a real one and then hands out a
sample clip, so the whole app can be built and tested without models or a GPU.
"mode" in config.json can force either: "auto" (the default), "real" or "fake".
"""

import asyncio
import json
import logging
import math
import os
import random
import re
import shutil
import time
import uuid

from aiohttp import web

from . import h3_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "app")
SAMPLES_DIR = os.path.join(HERE, "samples")
CONFIG_FILE = os.path.join(HERE, "config.json")
ROOT = "minimax_flow"
EVENT = "mmflow.clip"
LOG = logging.getLogger("minimax_flow")

# The models fake renders offer. Real renders offer the H3 editor's checkpoint families that are installed.
FAKE_MODELS = [
    {"id": "hyperflow", "name": "HyperFlow H3 Flash", "steps": 6, "minSteps": 4, "maxSteps": 8, "stepSize": 2},
    {"id": "h3_turbo", "name": "MiniMax H3 Turbo", "steps": 6, "minSteps": 1, "maxSteps": 16, "stepSize": 1},
    {"id": "h3", "name": "MiniMax H3", "steps": 20, "minSteps": 1, "maxSteps": 40, "stepSize": 1},
    {"id": "singularity", "name": "Singularity v2", "steps": 20, "minSteps": 1, "maxSteps": 40, "stepSize": 1},
]
ASPECTS = {"16:9": [16, 9], "9:16": [9, 16], "1:1": [1, 1], "4:3": [4, 3], "3:4": [3, 4], "21:9": [21, 9]}
MEGAPIXELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5, 2.0]
LENGTH = {"min": 2, "max": 20, "fps": 24}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")

DEFAULT_UI = {
    "settings": {"model": "MiniMax H3", "steps": 20, "res": 0.6, "aspect": "16:9", "len": 6,
                 "seedMode": "random", "seed": 48213, "loras": []},
    "attached": [],
    "perRow": 3,
    "prompt": "",
}

_lock = asyncio.Lock()
_queue = None          # asyncio.Queue of clip ids, made on first use (it needs the running loop)
_cancelled = set()


# ---------------------------------------------------------------- storage

def output_dir():
    import folder_paths
    return folder_paths.get_output_directory()


def root_dir(*parts):
    path = os.path.join(output_dir(), ROOT, *parts)
    os.makedirs(path if not os.path.splitext(path)[1] else os.path.dirname(path), exist_ok=True)
    return path


def project_file():
    return os.path.join(root_dir(), "project.json")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def render_mode():
    """("real", models) or ("fake", models), and why real isn't used when it isn't."""
    config = load_config()
    mode = config.get("mode")
    if mode not in ("auto", "real", "fake"):
        mode = "fake" if config.get("fake_renders") else "auto"
    if mode == "fake":
        return "fake", FAKE_MODELS, "Fake renders are switched on in config.json."
    try:
        models = h3_bridge.models()
    except h3_bridge.Missing as error:
        return ("fake", FAKE_MODELS, str(error)) if mode == "auto" else ("real", [], str(error))
    except Exception as error:
        # Never let this break the app: say what went wrong and offer fake renders.
        LOG.exception("MiniMax H3 Vision couldn't check for the H3 editor")
        why = "MiniMax H3 Vision couldn't use the H3 editor: %s: %s" % (type(error).__name__, error)
        return ("fake", FAKE_MODELS, why) if mode == "auto" else ("real", [], why)
    if not models:
        why = "None of the H3 editor's checkpoints are installed."
        return ("fake", FAKE_MODELS, why) if mode == "auto" else ("real", [], why)
    return "real", models, None


def read_project():
    try:
        with open(project_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("images", [])
    data.setdefault("clips", [])
    ui = data.setdefault("ui", {})
    for key, value in DEFAULT_UI.items():
        ui.setdefault(key, json.loads(json.dumps(value)))
    return data


def write_project(data):
    path = project_file()
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(temp, path)


def broadcast(clip):
    from server import PromptServer
    PromptServer.instance.send_sync(EVENT, {"clip": clip}, None)


def dims(aspect, mp):
    """Width and height for an aspect ratio at a size in megapixels (1 MP = 1024x1024), sides in steps of 32."""
    a, b = ASPECTS[aspect]
    pixels = mp * 1024 * 1024
    w = max(32, round(math.sqrt(pixels * a / b) / 32) * 32)
    h = max(32, round(math.sqrt(pixels * b / a) / 32) * 32)
    return w, h


def recover(data):
    """Renders that were running when ComfyUI stopped will never finish."""
    for clip in data["clips"]:
        if clip.get("status") in ("queued", "rendering"):
            clip["status"] = "failed"
            clip["error"] = "ComfyUI was restarted during this render."


# ---------------------------------------------------------------- page and API

async def page(request):
    raise web.HTTPFound("/mmflow/app/index.html?v=%d" % int(time.time()))


async def get_state(request):
    async with _lock:
        data = read_project()
        if not getattr(get_state, "recovered", False):
            recover(data)
            write_project(data)
            get_state.recovered = True
    try:
        import folder_paths
        loras = sorted(folder_paths.get_filename_list("loras"), key=str.lower)
    except Exception:
        loras = []
    mode, models, why = render_mode()
    if any(c.get("status") in ("queued", "rendering") and c.get("prompt_id") for c in data["clips"]):
        ensure_watcher()
    return web.json_response({
        **data,
        "options": {"models": models, "aspects": ASPECTS, "megapixels": MEGAPIXELS, "length": LENGTH,
                    "loras": loras, "fake": mode == "fake", "why_fake": why, "root": ROOT},
    })


async def put_ui(request):
    body = await request.json()
    async with _lock:
        data = read_project()
        for key in DEFAULT_UI:
            if key in body:
                data["ui"][key] = body[key]
        write_project(data)
    return web.json_response({"ok": True})


async def upload_images(request):
    reader = await request.multipart()
    added = []
    async with _lock:
        data = read_project()
        taken = {i["name"] for i in data["images"]}
        while True:
            part = await reader.next()
            if part is None:
                break
            if not part.filename:
                continue
            ext = os.path.splitext(part.filename)[1].lower()
            if ext not in IMAGE_TYPES:
                continue
            image_id = "i" + uuid.uuid4().hex[:10]
            file = image_id + ext
            with open(root_dir("images", file), "wb") as f:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
            base = re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(part.filename)[0].lower()).strip("_")[:16] or "image"
            name, n = base, 2
            while name in taken:
                name, n = "%s%d" % (base, n), n + 1
            taken.add(name)
            image = {"id": image_id, "name": name, "file": file, "added": time.time()}
            data["images"].append(image)
            added.append(image)
        write_project(data)
    return web.json_response({"images": added})


async def rename_image(request):
    image_id = request.match_info["id"]
    name = str((await request.json()).get("name", ""))
    if not NAME_RE.match(name):
        raise web.HTTPBadRequest(text="Names need letters, digits or _.")
    async with _lock:
        data = read_project()
        image = next((i for i in data["images"] if i["id"] == image_id), None)
        if not image:
            raise web.HTTPNotFound(text="No such image.")
        if any(i["name"] == name and i is not image for i in data["images"]):
            raise web.HTTPBadRequest(text="Another image already has that name.")
        image["name"] = name
        write_project(data)
    return web.json_response({"image": image})


async def generate(request):
    body = await request.json()
    prompt = str(body.get("prompt", "")).strip()
    s = body.get("settings") or {}
    if not prompt:
        raise web.HTTPBadRequest(text="Write a prompt first.")
    mode, models, why = render_mode()
    model = next((m for m in models if m["name"] == s.get("model")), None)
    if not model:
        raise web.HTTPBadRequest(text=why or "That model isn't installed.")
    aspect = s.get("aspect") if s.get("aspect") in ASPECTS else "16:9"
    res = float(s.get("res", 0.2))
    if res not in MEGAPIXELS:
        raise web.HTTPBadRequest(text="Unknown size.")
    steps = int(s.get("steps", model["steps"]))
    low, high, every = model.get("minSteps", 1), model["maxSteps"], model.get("stepSize", 1)
    if not (low <= steps <= high and (steps - low) % every == 0):
        raise web.HTTPBadRequest(text="%s takes %s steps." % (model["name"], ", ".join(map(str, range(low, high + 1, every)))
                                                                if every > 1 else "%d to %d" % (low, high)))
    length = int(s.get("len", 6))
    if not LENGTH["min"] <= length <= LENGTH["max"]:
        raise web.HTTPBadRequest(text="Length must be %d to %d seconds." % (LENGTH["min"], LENGTH["max"]))
    seed = int(s.get("seed", 0)) if s.get("seedMode") == "fixed" else random.randrange(2 ** 31)
    loras = [{"name": str(l["name"]), "strength": float(l["strength"])} for l in s.get("loras", []) if l.get("on", True)]

    async with _lock:
        data = read_project()
        names = {i["name"] for i in data["images"]}
        unknown = [n for n in MENTION_RE.findall(prompt) if n not in names]
        if unknown:
            raise web.HTTPBadRequest(text="There is no image called @%s." % unknown[0])
        w, h = dims(aspect, res)
        clip = {"id": "c" + uuid.uuid4().hex[:10], "prompt": prompt, "model": model["name"], "profile": model["id"],
                "steps": steps, "res": res, "aspect": aspect, "len": length, "width": w, "height": h, "seed": seed,
                "loras": loras, "status": "queued", "step": 0, "file": None, "subfolder": ROOT + "/clips",
                "starred": False, "created": time.time(), "mode": mode}
        images = list(data["images"])

    if mode == "real":
        try:
            graph = h3_bridge.build_graph(clip, images)
            clip["prompt_id"] = await h3_bridge.queue(graph, body.get("client_id"))
        except Exception as error:
            LOG.exception("MiniMax H3 Vision couldn't queue a render")
            raise web.HTTPBadRequest(text="ComfyUI refused the render: %s" % error)

    async with _lock:
        data = read_project()
        data["clips"].insert(0, clip)
        write_project(data)
    if mode == "real":
        ensure_watcher()
    else:
        ensure_worker()
        await _queue.put(clip["id"])
    broadcast(clip)
    return web.json_response({"clip": clip})


async def update_clip(request):
    clip_id = request.match_info["id"]
    body = await request.json()
    async with _lock:
        data = read_project()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            raise web.HTTPNotFound(text="No such clip.")
        if "starred" in body:
            clip["starred"] = bool(body["starred"])
        write_project(data)
    return web.json_response({"clip": clip})


async def delete_clip(request):
    """Delete a clip. A clip that is still queued or rendering is cancelled first."""
    clip_id = request.match_info["id"]
    async with _lock:
        data = read_project()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            raise web.HTTPNotFound(text="No such clip.")
        busy = clip.get("status") in ("queued", "rendering")
        if busy:
            _cancelled.add(clip_id)
        data["clips"].remove(clip)
        write_project(data)
    if busy and clip.get("prompt_id"):
        try:
            await h3_bridge.cancel(clip["prompt_id"])
        except Exception:
            LOG.exception("MiniMax H3 Vision couldn't stop a render")
    if clip.get("file"):
        try:
            os.remove(os.path.join(output_dir(), clip.get("subfolder") or ROOT + "/clips", clip["file"]))
        except OSError:
            pass
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- rendering

def ensure_worker():
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
        asyncio.get_running_loop().create_task(worker())


async def set_clip(clip_id, **fields):
    async with _lock:
        data = read_project()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            return None
        clip.update(fields)
        write_project(data)
    broadcast(clip)
    return clip


async def worker():
    while True:
        clip_id = await _queue.get()
        try:
            if clip_id in _cancelled:
                _cancelled.discard(clip_id)
                continue
            await fake_render(clip_id)
        except Exception as error:
            LOG.exception("MiniMax H3 Vision render failed")
            await set_clip(clip_id, status="failed", error=str(error))


_watcher = None


def ensure_watcher():
    """One watcher for real renders: it follows each in ComfyUI's queue until its video is saved."""
    global _watcher
    if _watcher is None or _watcher.done():
        _watcher = asyncio.get_running_loop().create_task(watch_real())


async def watch_real():
    while True:
        await asyncio.sleep(1.0)
        try:
            async with _lock:
                busy = [c for c in read_project()["clips"] if c.get("status") in ("queued", "rendering") and c.get("prompt_id")]
            if not busy:
                return
            running, queued = h3_bridge.queue_state()
            for clip in busy:
                pid = clip["prompt_id"]
                if pid in running:
                    if clip["status"] != "rendering":
                        await set_clip(clip["id"], status="rendering")
                elif pid in queued:
                    continue
                else:
                    file, why = h3_bridge.result(pid)
                    if file:
                        await set_clip(clip["id"], status="done", step=clip["steps"], file=file["filename"],
                                       subfolder=file.get("subfolder") or ROOT + "/clips")
                    else:
                        await set_clip(clip["id"], status="failed", error=why)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("MiniMax H3 Vision couldn't check its renders")


def sample_for(aspect):
    a, b = ASPECTS[aspect]
    shape = "wide" if a > b else "tall" if a < b else "square"
    return os.path.join(SAMPLES_DIR, "sample_%s.mp4" % shape)


async def fake_render(clip_id):
    """Stands in for a real render: model loading, then one step at a time, then a finished file."""
    config = load_config()
    async with _lock:
        clip = next((c for c in read_project()["clips"] if c["id"] == clip_id), None)
    if not clip:
        return
    await set_clip(clip_id, status="rendering", step=0)
    await asyncio.sleep(float(config.get("fake_load_seconds", 1.5)))
    per_step = float(config.get("fake_seconds_per_step", 0.35)) * math.sqrt(clip["res"] / 0.2) * (clip["len"] / 6)
    for step in range(1, clip["steps"] + 1):
        if clip_id in _cancelled:
            _cancelled.discard(clip_id)
            return
        await asyncio.sleep(per_step)
        if clip_id in _cancelled:
            _cancelled.discard(clip_id)
            return
        if not await set_clip(clip_id, step=step):
            return
    file = clip_id + ".mp4"
    shutil.copyfile(sample_for(clip["aspect"]), root_dir("clips", file))
    await set_clip(clip_id, status="done", step=clip["steps"], file=file)


# ---------------------------------------------------------------- wiring

ROUTES = (
    ("GET", "/mmflow/app", page),
    ("GET", "/mmflow/api/state", get_state),
    ("PUT", "/mmflow/api/ui", put_ui),
    ("POST", "/mmflow/api/images", upload_images),
    ("PATCH", "/mmflow/api/images/{id}", rename_image),
    ("POST", "/mmflow/api/generate", generate),
    ("PATCH", "/mmflow/api/clips/{id}", update_clip),
    ("DELETE", "/mmflow/api/clips/{id}", delete_clip),
)


def install():
    from server import PromptServer

    routes = PromptServer.instance.routes
    for method, path, handler in ROUTES:
        routes.route(method, path)(handler)
    routes.static("/mmflow/app/", APP_DIR)
