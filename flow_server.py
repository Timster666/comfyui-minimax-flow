"""The server side of MiniMax H3 Vision: its page, its API, and the renderer.

Everything lives in ComfyUI's output folder under minimax_flow/:
    project.json   images, clips and the last-used settings
    images/        uploaded reference images
    clips/         finished videos
    stills/        finished images (the Image tab)
Files there are served by ComfyUI's own /view endpoint.

Videos are real when the H3 editor pack and its models are installed (see h3_bridge.py), and
images are real when MOSS-Image's files are installed (see moss_bridge.py). Each is fake otherwise: a fake render reports its steps like a real one and then hands out a
sample clip, so the whole app can be built and tested without models or a GPU.
"mode" in config.json can force either: "auto" (the default), "real" or "fake" ("image_mode" for images).
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

from . import comfy_link, h3_bridge, moss_bridge

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

# The Image tab (MOSS-Image): any aspect ratio, sides in steps of 16, about 0.25 to 4 MP, best at 1 to 2 MP.
FAKE_IMAGE_MODELS = [{"id": "moss_image_sft_int8_convrot.safetensors", "name": "MOSS-Image SFT (INT8)", "steps": 50,
                      "minSteps": 1, "maxSteps": 80, "stepSize": 1, "cfg": 3.0},
                     {"id": "moss_image_sft_8stepdmd_int8_convrot.safetensors", "name": "MOSS-Image SFT Turbo 8-step (INT8)",
                      "steps": 8, "minSteps": 1, "maxSteps": 80, "stepSize": 1, "cfg": 1.0}]
IMAGE_ASPECTS = {"1:1": [1, 1], "4:5": [4, 5], "3:4": [3, 4], "2:3": [2, 3], "9:16": [9, 16],
                 "16:9": [16, 9], "3:2": [3, 2], "4:3": [4, 3], "5:4": [5, 4], "21:9": [21, 9]}
IMAGE_MEGAPIXELS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
IMAGE_RECOMMENDED_MP = [1.0, 1.5, 2.0]
CFG = {"min": 1, "max": 10, "step": 0.5}
MEGAPIXELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5, 2.0]
LENGTH = {"min": 2, "max": 20, "fps": 24}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")

DEFAULT_UI = {
    "settings": {"model": "MiniMax H3", "steps": 20, "res": 0.6, "aspect": "16:9", "len": 6,
                 "seedMode": "random", "seed": 48213, "loras": []},
    "imageSettings": {"model": "MOSS-Image SFT (INT8)", "steps": 50, "cfg": 3.0, "res": 1.0, "aspect": "1:1",
                      "seedMode": "random", "seed": 48213, "loras": []},
    "attached": [],
    "perRow": 3,
    "prompt": "",
    "imagePrompt": "",
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


def image_mode():
    """Like render_mode, for the Image tab: real when MOSS-Image's files are installed."""
    config = load_config()
    mode = config.get("image_mode", config.get("mode"))
    if mode not in ("auto", "real", "fake"):
        mode = "fake" if config.get("fake_renders") else "auto"
    if mode == "fake":
        return "fake", FAKE_IMAGE_MODELS, "Fake renders are switched on in config.json."
    try:
        return "real", moss_bridge.models(), None
    except moss_bridge.Missing as error:
        return ("fake", FAKE_IMAGE_MODELS, str(error)) if mode == "auto" else ("real", [], str(error))
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't check for MOSS-Image")
        why = "MiniMax H3 Vision couldn't check for MOSS-Image: %s: %s" % (type(error).__name__, error)
        return ("fake", FAKE_IMAGE_MODELS, why) if mode == "auto" else ("real", [], why)


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


def dims(aspect, mp, grid=32, table=None):
    """Width and height for an aspect ratio at a size in megapixels (1 MP = 1024x1024).
    H3 wants sides in steps of 32, MOSS-Image in steps of 16."""
    a, b = (table or ASPECTS)[aspect]
    pixels = mp * 1024 * 1024
    w = max(grid, round(math.sqrt(pixels * a / b) / grid) * grid)
    h = max(grid, round(math.sqrt(pixels * b / a) / grid) * grid)
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
    imode, imodels, iwhy = image_mode()
    if any(c.get("status") in ("queued", "rendering") and c.get("prompt_id") for c in data["clips"]):
        ensure_watcher()
    return web.json_response({
        **data,
        "options": {"models": models, "aspects": ASPECTS, "megapixels": MEGAPIXELS, "length": LENGTH,
                    "loras": loras, "fake": mode == "fake", "why_fake": why, "root": ROOT,
                    "image": {"models": imodels, "aspects": IMAGE_ASPECTS, "megapixels": IMAGE_MEGAPIXELS,
                              "recommendedMP": IMAGE_RECOMMENDED_MP, "cfg": CFG,
                              "fake": imode == "fake", "why_fake": iwhy}},
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
    if body.get("kind") == "image":
        return await generate_image(prompt, s)
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
                "starred": False, "created": time.time(), "mode": mode, "kind": "video"}
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


async def generate_image(prompt, s):
    mode, models, why = image_mode()
    model = next((m for m in models if m["name"] == s.get("model")), None)
    if not model:
        raise web.HTTPBadRequest(text=why or "That model isn't installed.")
    aspect = s.get("aspect") if s.get("aspect") in IMAGE_ASPECTS else "1:1"
    res = float(s.get("res", 1.0))
    if res not in IMAGE_MEGAPIXELS:
        raise web.HTTPBadRequest(text="Unknown size.")
    steps = int(s.get("steps", model["steps"]))
    if not model["minSteps"] <= steps <= model["maxSteps"]:
        raise web.HTTPBadRequest(text="%s takes %d to %d steps." % (model["name"], model["minSteps"], model["maxSteps"]))
    cfg = float(s.get("cfg", model["cfg"]))
    if not CFG["min"] <= cfg <= CFG["max"]:
        raise web.HTTPBadRequest(text="Guidance must be %s to %s." % (CFG["min"], CFG["max"]))
    seed = int(s.get("seed", 0)) if s.get("seedMode") == "fixed" else random.randrange(2 ** 31)
    loras = [{"name": str(l["name"]), "strength": float(l["strength"])} for l in s.get("loras", []) if l.get("on", True)]
    w, h = dims(aspect, res, 16, IMAGE_ASPECTS)
    clip = {"id": "s" + uuid.uuid4().hex[:10], "kind": "image", "prompt": prompt, "model": model["name"], "profile": model["id"],
            "steps": steps, "cfg": cfg, "res": res, "aspect": aspect, "width": w, "height": h, "seed": seed, "loras": loras,
            "status": "queued", "step": 0, "file": None, "subfolder": ROOT + "/stills", "starred": False,
            "created": time.time(), "mode": mode}
    if mode == "real":
        try:
            clip["prompt_id"] = await comfy_link.queue_prompt(moss_bridge.build_graph(clip), moss_bridge.EXTRA_DATA)
        except Exception as error:
            LOG.exception("MiniMax H3 Vision couldn't queue an image")
            raise web.HTTPBadRequest(text="ComfyUI refused the image: %s" % error)
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


def name_from(prompt, taken):
    """An @name for a picture, from its quoted words (or its first words), cut at a whole word."""
    quoted = re.search(r'["\u201c]([^"\u201d]{1,60})["\u201d]', prompt)
    words = re.findall(r"[a-z0-9]+", (quoted.group(1) if quoted else prompt).lower())
    base = ""
    for word in words:
        if base and len(base) + 1 + len(word) > 20:
            break
        base = (base + "_" + word) if base else word[:20]
    base = base or "image"
    name, n = base, 2
    while name in taken:
        name, n = "%s%d" % (base, n), n + 1
    return name


async def image_from_clip(request):
    """A finished image from the Image tab, as an @image for videos. Made once per image; asked again, it is returned."""
    clip_id = str((await request.json()).get("clip_id", ""))
    async with _lock:
        data = read_project()
        had = next((i for i in data["images"] if i.get("from_clip") == clip_id), None)
        if had:
            return web.json_response({"image": had})
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip or clip.get("kind") != "image" or clip.get("status") != "done" or not clip.get("file"):
            raise web.HTTPBadRequest(text="That image isn't finished.")
        source = os.path.join(output_dir(), clip.get("subfolder") or ROOT + "/stills", clip["file"])
        image_id = "i" + uuid.uuid4().hex[:10]
        file = image_id + (os.path.splitext(clip["file"])[1] or ".png")
        shutil.copyfile(source, root_dir("images", file))
        image = {"id": image_id, "name": name_from(clip["prompt"], {i["name"] for i in data["images"]}), "file": file,
                 "added": time.time(), "from_clip": clip_id}
        data["images"].append(image)
        write_project(data)
    return web.json_response({"image": image})


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
            await comfy_link.cancel(clip["prompt_id"])
        except Exception:
            LOG.exception("MiniMax H3 Vision couldn't stop a render")
    if clip.get("file"):
        try:
            os.remove(os.path.join(output_dir(), clip.get("subfolder") or ROOT + ("/stills" if clip.get("kind") == "image" else "/clips"), clip["file"]))
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
            running, queued = comfy_link.queue_state()
            for clip in busy:
                pid = clip["prompt_id"]
                if pid in running:
                    if clip["status"] != "rendering":
                        await set_clip(clip["id"], status="rendering")
                elif pid in queued:
                    continue
                else:
                    if clip.get("kind") == "image":
                        entry = comfy_link.history(pid)
                        file, why = moss_bridge.saved_file(entry), comfy_link.failure(entry)
                    else:
                        file, why = h3_bridge.result(pid)
                    if file:
                        await set_clip(clip["id"], status="done", step=clip["steps"], file=file["filename"],
                                       subfolder=file.get("subfolder") or ROOT + ("/stills" if clip.get("kind") == "image" else "/clips"))
                    else:
                        await set_clip(clip["id"], status="failed", error=why)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("MiniMax H3 Vision couldn't check its renders")


def sample_for(aspect, kind="video"):
    a, b = (IMAGE_ASPECTS if kind == "image" else ASPECTS)[aspect]
    shape = "wide" if a > b else "tall" if a < b else "square"
    return os.path.join(SAMPLES_DIR, ("sample_image_%s.png" if kind == "image" else "sample_%s.mp4") % shape)


async def fake_render(clip_id):
    """Stands in for a real render: model loading, then one step at a time, then a finished file."""
    config = load_config()
    async with _lock:
        clip = next((c for c in read_project()["clips"] if c["id"] == clip_id), None)
    if not clip:
        return
    await set_clip(clip_id, status="rendering", step=0)
    await asyncio.sleep(float(config.get("fake_load_seconds", 1.5)))
    if clip.get("kind") == "image":
        per_step = float(config.get("fake_seconds_per_image_step", 0.08)) * math.sqrt(clip["res"])
    else:
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
    if clip.get("kind") == "image":
        file = clip_id + ".png"
        shutil.copyfile(sample_for(clip["aspect"], "image"), root_dir("stills", file))
        await set_clip(clip_id, status="done", step=clip["steps"], file=file, subfolder=ROOT + "/stills")
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
    ("POST", "/mmflow/api/images/from-clip", image_from_clip),
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
