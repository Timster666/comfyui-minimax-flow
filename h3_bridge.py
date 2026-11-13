"""Real renders, through the H3 editor pack that is installed next to this one.

MiniMax H3 Vision doesn't build H3 graphs itself. It hands each clip to the H3 editor's own
render_graph(), the code the editor renders with, and then sets the size, steps and file
locations to MiniMax H3 Vision's settings. Queueing, cancelling and reading results use the
editor's helpers too, so a render here behaves like one in the editor.

Images in the prompt become cast members: "@detective" is a picture named detective, and the
editor writes the prompt H3 expects for references (<Subject 1> in <Picture 1>, and so on).
"""

import sys

ROOT = "minimax_flow"
SAMPLER_NODE = "15"     # the editor's SamplerCustomAdvanced: its progress is the render's steps
SAVE_NODE = "19"        # the editor's SaveVideo


class Missing(Exception):
    """The H3 editor pack isn't installed, or is a version this doesn't know."""


_found = {}


def _has(module, *names):
    """Whether a module has these names. Some modules (lazy loaders in transformers and others) raise
    when a name is looked up, so any error counts as no."""
    try:
        return all(hasattr(module, name) for name in names)
    except Exception:
        return False


def editor():
    """The H3 editor's project module (graphs) and editor_api module (queue, results), found among loaded modules."""
    if not _found:
        modules = list(sys.modules.items())
        # ComfyUI loads a pack under its folder's name, so its modules are "<folder>.project" and
        # "<folder>.editor_api". Look at packs with h3 in the name first, then everything else.
        modules.sort(key=lambda item: 0 if "h3" in item[0].lower() and "." in item[0] else 1)
        for name, module in modules:
            if module is None or not isinstance(name, str):
                continue
            if "project" not in _found and name.endswith("project") and _has(module, "render_graph", "PROFILES", "snap_frames"):
                _found["project"] = module
            if "api" not in _found and name.endswith("editor_api") and _has(module, "queue_prompt", "own_nodes", "saved_file"):
                _found["api"] = module
            if len(_found) == 2:
                break
    if "project" not in _found or "api" not in _found:
        _found.clear()
        raise Missing("The H3 editor pack (comfyui-h3-editor) isn't installed in this ComfyUI.")
    return _found["project"], _found["api"]


def available():
    """Model files ComfyUI can see, in the form the editor's file picking expects."""
    _, api = editor()
    return api.available_models()


def models(files=None):
    """Every H3 checkpoint family whose files are installed: id, name, recommended steps and the most steps offered."""
    project, _ = editor()
    files = available() if files is None else files
    rows = []
    for row in project.profile_status(files):
        if not row["available"]:
            continue
        steps = int(row["steps"]["final"])
        if project.is_hyperflow(row["unet"]):
            # HyperFlow has a schedule for 4, 6 and 8 steps only (its sigmas node refuses anything else).
            rows.append({"id": row["id"], "name": row["title"], "steps": steps, "minSteps": 4, "maxSteps": 8, "stepSize": 2})
        else:
            rows.append({"id": row["id"], "name": row["title"], "steps": steps, "minSteps": 1,
                         "maxSteps": 40 if steps >= 12 else 16, "stepSize": 1})
    return rows


def frames(seconds):
    """How many frames a clip of this length really gets: H3 takes lengths in steps of 17 frames."""
    project, _ = editor()
    return project.snap_frames(seconds)


def build_graph(clip, images, files=None):
    """The ComfyUI graph for one MiniMax H3 Vision clip.

    clip: the clip as MiniMax H3 Vision keeps it (prompt, profile, steps, width, height, len, seed, loras).
    images: every uploaded image, so @names in the prompt can be found.
    """
    project_mod, api = editor()
    files = available() if files is None else files
    by_name = {image["name"].lower(): image for image in images}
    mentioned = []
    for name in _mentions(clip["prompt"]):
        image = by_name.get(name.lower())
        if image and image["id"] not in mentioned:
            mentioned.append(image["id"])
    cast = [{"id": image["id"], "name": image["name"], "file": image["file"], "subfolder": ROOT + "/images"}
            for image in images if image["id"] in mentioned]
    project = {
        "profile": clip["profile"],
        "cast": cast,
        "loras": [{"file": l["name"], "strength": float(l["strength"])} for l in clip.get("loras") or []],
    }
    # "draft" is the editor's plain one-pass render. MiniMax H3 Vision sets size and steps itself, and
    # adds the Realism LoRA only when it is in the clip's LoRA list.
    editor_clip = {"id": clip["id"], "action": clip["prompt"], "seconds": clip["len"], "cast": mentioned}
    graph = project_mod.render_graph(project, "minimax_flow", editor_clip, "take", "draft", clip["seed"], files)

    graph["10"]["inputs"].update({"width": int(clip["width"]), "height": int(clip["height"])})
    steps_node = graph["12"]["inputs"]
    if "steps" not in steps_node:
        raise Missing("This version of the H3 editor builds its steps differently; MiniMax H3 Vision needs an update.")
    steps_node["steps"] = int(clip["steps"])
    graph["9"]["inputs"]["folder"] = ROOT + "/preview"
    graph[SAVE_NODE]["inputs"]["filename_prefix"] = "%s/clips/%s" % (ROOT, clip["id"])
    # The editor keeps a take's first and last frames for extending it later; MiniMax H3 Vision doesn't extend yet.
    graph.pop("20", None)
    return api.own_nodes(graph)


def _mentions(text):
    import re
    return re.findall(r"@([A-Za-z0-9_]+)", text)


async def queue(graph, client_id=None):
    """Send a graph to ComfyUI's queue. Returns its prompt id; raises with ComfyUI's reason when refused."""
    _, api = editor()
    return await api.queue_prompt(graph, client_id)


def queue_state():
    """The prompt ids ComfyUI is running now, and those waiting."""
    _, api = editor()
    return api.comfy_queue()


def result(prompt_id):
    """A finished render: (file, None) with the saved video, or (None, why it failed)."""
    from server import PromptServer
    _, api = editor()
    entry = PromptServer.instance.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)
    file = api.saved_file(entry)
    return (file, None) if file else (None, api.failure(entry))


async def cancel(prompt_id):
    """Stop a render: interrupt it if it is running, or take it out of the queue."""
    _, api = editor()
    running, _ = api.comfy_queue()
    if prompt_id in running:
        await api.comfy("POST", "/interrupt", {"prompt_id": prompt_id})
    else:
        await api.comfy("POST", "/queue", {"delete": [prompt_id]})
