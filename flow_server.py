"""The server side of MiniMax H3 Vision: its page, its API, and the renderer.

Everything lives in ComfyUI's output folder under minimax_flow/:
    project.json   images, clips and the last-used settings
    images/        uploaded reference images
    clips/         finished videos
    stills/        finished images (the Image tab)
Files there are served by ComfyUI's own /view endpoint.

Videos are MiniMax H3 graphs built here (h3_graph.py, with this pack's nodes in vision_nodes.py), and
images are MOSS-Image graphs (moss_bridge.py). Both run in ComfyUI's own queue. A tab whose models
aren't installed says which files it needs.
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

from . import clip_info, comfy_link, edit_media, edit_project, h3_graph, moss_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "app")
ROOT = "minimax_flow"
EVENT = "mmflow.clip"
LOG = logging.getLogger("minimax_flow")

ASPECTS = {"16:9": [16, 9], "9:16": [9, 16], "1:1": [1, 1], "4:3": [4, 3], "3:4": [3, 4], "21:9": [21, 9]}

# The Image tab (MOSS-Image): any aspect ratio, sides in steps of 16, about 0.25 to 4 MP, best at 1 to 2 MP.
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

# The Edit tab: one track of pieces, each a part of a finished video: {id, clip, in, out} in seconds.
DEFAULT_EDIT = {"name": "Untitled edit", "pieces": [], "ratio": "16:9", "muted": False}
EXPORT_EVENT = "mmvision_export"
_exports = {}   # export id -> {"cancel": bool}

DEFAULT_UI = {
    "settings": {"model": "HyperFlow H3 Flash", "steps": 6, "res": 0.6, "aspect": "16:9", "len": 5,
                 "seedMode": "random", "seed": 48213, "loras": []},
    "imageSettings": {"model": "MOSS-Image SFT (INT8)", "steps": 50, "cfg": 3.0, "res": 1.0, "aspect": "1:1",
                      "seedMode": "random", "seed": 48213, "loras": []},
    "attached": [],
    "perRow": 3,
    "prompt": "",
    "imagePrompt": "",
    "imageNegative": moss_bridge.NEGATIVE,   # the Image tab's negative prompt box starts with this text
    "negativeOpen": False,
    "negativeShown": True,
}

_lock = asyncio.Lock()


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


def sampling_choices():
    """ComfyUI's samplers and schedulers, as its KSampler lists them."""
    try:
        import comfy.samplers
        return list(comfy.samplers.KSampler.SAMPLERS), list(comfy.samplers.KSampler.SCHEDULERS)
    except Exception:
        return (["euler", "euler_ancestral", "heun", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "res_multistep", "uni_pc"],
                ["simple", "normal", "karras", "exponential", "sgm_uniform", "beta"])


def pick_sampling(s, model, video):
    """The sampler and scheduler a render asked for: None means the model's own."""
    samplers, schedulers = sampling_choices()
    if video:
        schedulers = schedulers + [h3_graph.BETA57]
    sampler = s.get("sampler") or None
    scheduler = s.get("scheduler") or None
    if sampler and sampler not in samplers:
        raise web.HTTPBadRequest(text="ComfyUI has no sampler called %s." % sampler)
    if scheduler and (scheduler not in schedulers or model.get("scheduler") == h3_graph.HYPERFLOW_SCHEDULE):
        raise web.HTTPBadRequest(text="%s can't use the %s scheduler." % (model["name"], scheduler))
    return sampler, scheduler


def video_models():
    """The installed MiniMax H3 models, and why there are none when there are none."""
    try:
        models = h3_graph.models()
        return models, (None if models else h3_graph.why_none())
    except Exception as error:
        # Never let this break the app: say what went wrong.
        LOG.exception("MiniMax H3 Vision couldn't list its video models")
        return [], "MiniMax H3 Vision couldn't list its video models: %s: %s" % (type(error).__name__, error)


def image_models():
    """The installed MOSS-Image models, and why there are none when there are none."""
    try:
        return moss_bridge.models(), None
    except moss_bridge.Missing as error:
        return [], str(error)
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't check for MOSS-Image")
        return [], "MiniMax H3 Vision couldn't check for MOSS-Image: %s: %s" % (type(error).__name__, error)


def read_project():
    try:
        with open(project_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("images", [])
    data.setdefault("clips", [])
    data.setdefault("edit", dict(DEFAULT_EDIT))
    ui = data.setdefault("ui", {})
    # Before the negative prompt box showed its text, an empty box meant "the recommended one".
    if "negativeShown" not in ui and not ui.get("imageNegative"):
        ui["imageNegative"] = moss_bridge.NEGATIVE
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
    models, why = video_models()
    imodels, iwhy = image_models()
    if any(c.get("status") in ("queued", "rendering") and c.get("prompt_id") for c in data["clips"]):
        ensure_watcher()
    return web.json_response({
        **data,
        "options": {"editSizes": EDIT_SIZES, "samplers": sampling_choices()[0], "schedulers": sampling_choices()[1],
                    "beta57": h3_graph.BETA57, "models": models, "aspects": ASPECTS, "megapixels": MEGAPIXELS, "length": LENGTH,
                    "loras": loras, "missing": why, "root": ROOT,
                    "image": {"models": imodels, "aspects": IMAGE_ASPECTS, "megapixels": IMAGE_MEGAPIXELS,
                              "recommendedMP": IMAGE_RECOMMENDED_MP, "cfg": CFG,
                              "missing": iwhy}},
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
        return await generate_image(prompt, s, body.get("negative"))
    models, why = video_models()
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
    sampler, scheduler = pick_sampling(s, model, True)
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
                "starred": False, "created": time.time(), "kind": "video"}
        if sampler:
            clip["sampler"] = sampler
        if scheduler:
            clip["scheduler"] = scheduler
        images = list(data["images"])
        # The pictures it uses, so a project export finds them even after a rename.
        clip["refs"] = [{"id": i["id"], "name": i["name"]} for i in edit_project.refs_of(clip, images)]

    try:
        graph = h3_graph.build_graph(clip, images, name=clip_info.file_stem(clip))
        clip["prompt_id"] = await comfy_link.queue_prompt(graph, clip_info.extra_data(clip), body.get("client_id"))
    except h3_graph.Missing as error:
        raise web.HTTPBadRequest(text=str(error))
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't queue a render")
        raise web.HTTPBadRequest(text="ComfyUI refused the render: %s" % error)

    async with _lock:
        data = read_project()
        data["clips"].insert(0, clip)
        write_project(data)
    ensure_watcher()
    broadcast(clip)
    return web.json_response({"clip": clip})


async def generate_image(prompt, s, negative=None):
    models, why = image_models()
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
    sampler, scheduler = pick_sampling(s, model, False)
    # The negative prompt is exactly what the box holds; empty means none.
    negative = str(negative).strip()[:2000] if negative is not None else None
    w, h = dims(aspect, res, 16, IMAGE_ASPECTS)
    clip = {"id": "s" + uuid.uuid4().hex[:10], "kind": "image", "prompt": prompt, "model": model["name"], "profile": model["id"],
            "steps": steps, "cfg": cfg, "res": res, "aspect": aspect, "width": w, "height": h, "seed": seed, "loras": loras,
            "status": "queued", "step": 0, "file": None, "subfolder": ROOT + "/stills", "starred": False,
            "created": time.time()}
    for key, value in (("sampler", sampler), ("scheduler", scheduler)):
        if value:
            clip[key] = value
    if negative is not None:
        clip["negative"] = negative
    try:
        clip["prompt_id"] = await comfy_link.queue_prompt(moss_bridge.build_graph(clip, name=clip_info.file_stem(clip)),
                                                          clip_info.extra_data(clip, moss_bridge.EXTRA_DATA))
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't queue an image")
        raise web.HTTPBadRequest(text="ComfyUI refused the image: %s" % error)
    async with _lock:
        data = read_project()
        data["clips"].insert(0, clip)
        write_project(data)
    ensure_watcher()
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


def nearest(value, options):
    return min(options, key=lambda o: abs(o - value))


def nearest_aspect(w, h, table):
    return min(table, key=lambda k: abs(math.log((w / h) / (table[k][0] / table[k][1]))))


async def import_clip(request):
    """A video or image made earlier (here, in the H3 editor or by MiniMax H3 Vision elsewhere): it joins the
    grid with the prompt and settings found inside it."""
    reader = await request.multipart()
    part = await reader.next()
    while part is not None and not part.filename:
        part = await reader.next()
    if part is None:
        raise web.HTTPBadRequest(text="No file came with the import.")
    ext = os.path.splitext(part.filename)[1].lower()
    kind = "video" if ext in clip_info.VIDEO_TYPES else "image" if ext in clip_info.IMAGE_TYPES else None
    if not kind:
        raise web.HTTPBadRequest(text="Import takes a video (.mp4, .mov, .webm) or an image (.png, .jpg, .webp).")
    folder = "clips" if kind == "video" else "stills"
    temp = root_dir(folder, ".import_%s%s" % (uuid.uuid4().hex[:8], ext))
    with open(temp, "wb") as f:
        while True:
            chunk = await part.read_chunk()
            if not chunk:
                break
            f.write(chunk)
    try:
        found = clip_info.settings(temp)
    except Exception:
        LOG.exception("MiniMax H3 Vision couldn't read an imported file")
        found = None
    if not found or not found.get("prompt"):
        os.remove(temp)
        raise web.HTTPBadRequest(text="%s has no prompt or settings inside it. Only files saved by ComfyUI with its "
                                      "metadata (from MiniMax H3 Vision, the H3 editor or a MOSS-Image workflow) have them."
                                      % part.filename)
    if (found.get("kind") or kind) != kind:
        found["kind"] = kind
    clip = clip_from_settings(found, kind)
    file = numbered(root_dir(folder), clip_info.file_stem(clip), ext)
    os.replace(temp, root_dir(folder, file))
    clip.update({"id": ("c" if kind == "video" else "s") + uuid.uuid4().hex[:10], "status": "done", "step": clip["steps"],
                 "file": file, "subfolder": ROOT + "/" + folder, "starred": False, "created": time.time(),
                 "mode": "imported", "imported": part.filename, "found": found["from"]})
    async with _lock:
        data = read_project()
        data["clips"].insert(0, clip)
        write_project(data)
    broadcast(clip)
    return web.json_response({"clip": clip})


def clip_from_settings(found, kind):
    """A clip record from what a file says, filled in to match this app's settings."""
    if kind == "image":
        models, _ = image_models()
        table, sizes, default_aspect = IMAGE_ASPECTS, IMAGE_MEGAPIXELS, "1:1"
    else:
        models, _ = video_models()
        table, sizes, default_aspect = ASPECTS, MEGAPIXELS, "16:9"
    model = next((m for m in models if m["id"] == found.get("profile") or m["name"] == found.get("model")), None)
    if not model and found.get("unet"):
        # An H3 editor graph names a checkpoint file: find the model family that uses it.
        try:
            model = next((m for m in models if m["id"] == h3_graph.profile_for(found["unet"], found.get("unet_lora"))), None)
        except Exception:
            model = None
    w, h = found.get("width") or 0, found.get("height") or 0
    aspect = found.get("aspect") if found.get("aspect") in table else nearest_aspect(w, h, table) if w and h else default_aspect
    res = found.get("res") if found.get("res") in sizes else nearest((w * h) / (1024 * 1024), sizes) if w and h else sizes[0]
    clip = {"kind": kind, "prompt": found["prompt"],
            "model": model["name"] if model else found.get("model") or os.path.splitext(os.path.basename(found.get("profile") or found.get("unet") or "Unknown model"))[0],
            "profile": model["id"] if model else found.get("profile"),
            "steps": int(found.get("steps") or (model or {}).get("steps") or 0), "res": res, "aspect": aspect,
            "width": w or None, "height": h or None, "seed": int(found.get("seed") or 0),
            "loras": [{"name": str(l["name"]), "strength": float(l.get("strength", 1.0))} for l in found.get("loras") or []]}
    if kind == "image":
        clip["cfg"] = float(found.get("cfg") or (model or {}).get("cfg") or 3.0)
    else:
        clip["len"] = int(min(LENGTH["max"], max(LENGTH["min"], found.get("len") or 6)))
    return clip


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
                        entry = comfy_link.history(pid)
                        file, why = video_file(entry), comfy_link.failure(entry)
                    if file:
                        await set_clip(clip["id"], status="done", step=clip["steps"], file=file["filename"],
                                       subfolder=file.get("subfolder") or ROOT + ("/stills" if clip.get("kind") == "image" else "/clips"))
                    else:
                        await set_clip(clip["id"], status="failed", error=why)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("MiniMax H3 Vision couldn't check its renders")


def video_file(entry):
    """The video a finished job saved, as {filename, subfolder}, or None."""
    for value in ((entry or {}).get("outputs") or {}).get(h3_graph.SAVE_NODE, {}).values():
        for found in value if isinstance(value, list) else []:
            if isinstance(found, dict) and found.get("filename"):
                return found
    return None


def numbered(folder, stem, ext):
    """A free file name the way ComfyUI numbers its outputs: stem_00001_.ext, stem_00002_.ext, ..."""
    n = 1
    while os.path.exists(os.path.join(folder, "%s_%05d_%s" % (stem, n, ext))):
        n += 1
    return "%s_%05d_%s" % (stem, n, ext)


# ---------------------------------------------------------------- wiring

# ---------------------------------------------------------------- the Edit tab

async def put_edit(request):
    """Save the edit (the app sends it a moment after each change)."""
    body = await request.json()
    pieces = []
    for p in body.get("pieces") or []:
        try:
            pieces.append({"id": str(p["id"])[:40], "clip": str(p["clip"])[:40], "in": round(float(p["in"]), 4), "out": round(float(p["out"]), 4)})
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="A piece of the edit is missing its clip or times.")
    edit = {"name": str(body.get("name") or "Untitled edit")[:80], "pieces": pieces[:500],
            "ratio": body.get("ratio") if body.get("ratio") in EDIT_SIZES else "16:9", "muted": bool(body.get("muted"))}
    async with _lock:
        data = read_project()
        data["edit"] = edit
        write_project(data)
    return web.json_response({"edit": edit})


def image_path(image):
    return root_dir("images", image["file"])


def clip_path(clip):
    folder = clip.get("subfolder") or ROOT + "/clips"
    return os.path.join(output_dir(), folder, clip["file"])


async def clip_strip(request):
    """A video's filmstrip and waveform for the timeline (made once, then kept in minimax_flow/strips)."""
    clip_id = request.match_info["id"]
    async with _lock:
        clip = next((c for c in read_project()["clips"] if c["id"] == clip_id), None)
    if not clip or not clip.get("file") or clip.get("kind") == "image":
        raise web.HTTPNotFound(text="No such video.")
    try:
        info = await asyncio.to_thread(edit_media.strip, clip_path(clip), root_dir("strips"), clip_id)
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't read %s", clip.get("file"))
        raise web.HTTPBadRequest(text="Couldn't read %s: %s" % (clip.get("file"), error))
    info = dict(info, sprite_url="/view?type=output&subfolder=%s&filename=%s&v=%s" % (ROOT + "/strips", info["sprite"], int(info["source_mtime"])))
    return web.json_response(info)


# Export sizes per frame shape: "as made" is H3's own 0.6 MP size.
EDIT_SIZES = {"16:9": [[1056, 608], [1280, 720], [1920, 1080]], "9:16": [[608, 1056], [720, 1280], [1080, 1920]],
              "1:1": [[800, 800], [1080, 1080]]}


async def start_export(request):
    """Export the edit as one MP4 in minimax_flow/exports. Progress arrives as mmvision_export events."""
    from server import PromptServer

    body = await request.json()
    width, height = int(body.get("width", 1056)), int(body.get("height", 608))
    if not (64 <= width <= 4096 and 64 <= height <= 4096):
        raise web.HTTPBadRequest(text="Unknown size.")
    async with _lock:
        data = read_project()
    clips = {c["id"]: c for c in data["clips"]}
    pieces = []
    for p in data["edit"]["pieces"]:
        clip = clips.get(p["clip"])
        if clip and clip.get("file") and os.path.exists(clip_path(clip)):
            pieces.append((clip_path(clip), float(p["in"]), float(p["out"])))
    if not pieces:
        raise web.HTTPBadRequest(text="The edit is empty.")
    stem = re.sub(r"[^a-z0-9]+", "_", str(body.get("name") or data["edit"]["name"]).lower()).strip("_")[:60] or "edit"
    folder = root_dir("exports")
    file = stem + ".mp4"
    n = 2
    while os.path.exists(os.path.join(folder, file)):
        file = "%s_%d.mp4" % (stem, n)
        n += 1
    job = "x" + uuid.uuid4().hex[:10]
    _exports[job] = {"cancel": False}
    server, loop = PromptServer.instance, asyncio.get_running_loop()

    def tell(**fields):
        # From the export thread: ComfyUI's send_sync is safe to call from another thread.
        server.send_sync(EXPORT_EVENT, dict(fields, job=job), None)

    async def run():
        try:
            await asyncio.to_thread(edit_media.export, pieces, os.path.join(folder, file), width, height, bool(body.get("sound", True)),
                                    lambda fraction, words: tell(progress=round(fraction, 3), step=words),
                                    lambda: _exports.get(job, {}).get("cancel"))
            tell(done=True, file=file, subfolder=ROOT + "/exports", progress=1.0)
        except edit_media.Cancelled:
            tell(cancelled=True)
        except Exception as error:
            LOG.exception("MiniMax H3 Vision couldn't export the edit")
            tell(error=str(error))
        finally:
            _exports.pop(job, None)

    loop.create_task(run())
    return web.json_response({"job": job, "file": file, "subfolder": ROOT + "/exports"})


def free_name(folder, stem, ext):
    file, n = stem + ext, 2
    while os.path.exists(os.path.join(folder, file)):
        file = "%s_%d%s" % (stem, n, ext)
        n += 1
    return file


async def export_project(request):
    """The edit and the videos it uses as one zip in minimax_flow/exports, to keep or to open elsewhere."""
    async with _lock:
        data = read_project()
    if not data["edit"]["pieces"]:
        raise web.HTTPBadRequest(text="The edit is empty.")
    stem = re.sub(r"[^a-z0-9]+", "_", data["edit"]["name"].lower()).strip("_")[:60] or "edit"
    folder = root_dir("exports")
    file = free_name(folder, stem + "_project", ".zip")
    clips = {c["id"]: c for c in data["clips"] if c.get("kind", "video") == "video" and c.get("file")}
    try:
        counts = await asyncio.to_thread(edit_project.pack, data["edit"], clips, clip_path, os.path.join(folder, file),
                                         data["images"], image_path)
    except Exception as error:
        LOG.exception("MiniMax H3 Vision couldn't save the edit project")
        raise web.HTTPBadRequest(text="Couldn't save the project: %s" % error)
    return web.json_response(dict(counts, file=file, subfolder=ROOT + "/exports"))


async def import_project(request):
    """Open a project zip: its videos join the app (Video gallery and Edit tab), and its timeline replaces the edit."""
    reader = await request.multipart()
    part = await reader.next()
    while part is not None and not part.filename:
        part = await reader.next()
    if part is None:
        raise web.HTTPBadRequest(text="No file came with the import.")
    temp = root_dir("exports", ".project_%s.zip" % uuid.uuid4().hex[:8])
    try:
        with open(temp, "wb") as f:
            while True:
                chunk = await part.read_chunk(4 << 20)
                if not chunk:
                    break
                f.write(chunk)
        async with _lock:
            data = read_project()
            existing = {c["id"]: c for c in data["clips"]}
            try:
                edit, added, same, new_images, same_images = await asyncio.to_thread(
                    edit_project.unpack, temp, existing, root_dir("clips"), clip_path, data["images"], root_dir("images"), image_path)
            except edit_project.Bad as error:
                raise web.HTTPBadRequest(text=str(error))
            now = time.time()
            for i, clip in enumerate(added):
                clip["subfolder"] = ROOT + "/clips"
                clip["imported"] = part.filename
                clip["created"] = now - i * 0.001   # newest first, so they are easy to find
            data["clips"] = added + data["clips"]
            for image in new_images:
                image["added"] = now
            data["images"].extend(new_images)
            data["edit"] = edit
            write_project(data)
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
    for clip in added:
        broadcast(clip)
    return web.json_response({"edit": edit, "clips": added, "same": same, "images": new_images, "sameImages": same_images})


async def cancel_export(request):
    job = _exports.get(request.match_info["job"])
    if job:
        job["cancel"] = True
    return web.json_response({"ok": True})


ROUTES = (
    ("GET", "/mmflow/app", page),
    ("GET", "/mmflow/api/state", get_state),
    ("PUT", "/mmflow/api/ui", put_ui),
    ("POST", "/mmflow/api/images", upload_images),
    ("POST", "/mmflow/api/images/from-clip", image_from_clip),
    ("PATCH", "/mmflow/api/images/{id}", rename_image),
    ("POST", "/mmflow/api/generate", generate),
    ("POST", "/mmflow/api/import", import_clip),
    ("PATCH", "/mmflow/api/clips/{id}", update_clip),
    ("DELETE", "/mmflow/api/clips/{id}", delete_clip),
    ("PUT", "/mmflow/api/edit", put_edit),
    ("GET", "/mmflow/api/strips/{id}", clip_strip),
    ("POST", "/mmflow/api/export", start_export),
    ("DELETE", "/mmflow/api/export/{job}", cancel_export),
    ("POST", "/mmflow/api/edit/export-project", export_project),
    ("POST", "/mmflow/api/edit/import-project", import_project),
)


def install():
    from server import PromptServer

    routes = PromptServer.instance.routes
    for method, path, handler in ROUTES:
        routes.route(method, path)(handler)
    routes.static("/mmflow/app/", APP_DIR)
