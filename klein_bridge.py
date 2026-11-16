"""Image edits and renders with FLUX.2 [klein] 9B, ComfyUI's native support for it (no extra node pack).

The graph follows Comfy-Org's Klein 9B image edit templates (image_flux2_klein_image_edit_9b_distilled and
_base): the model, the Qwen3 8B text encoder loaded as "flux2", the Flux 2 VAE, and per reference picture
ImageScaleToTotalPixels (1 MP, lanczos) -> VAEEncode -> ReferenceLatent, chained on the prompt and on the
negative. Sampling is SamplerCustomAdvanced with euler, Flux2Scheduler and CFGGuider. With no pictures it
is plain text-to-image, as in the 9B text-to-image template.

Distilled checkpoints render in 4 steps at guidance 1, with the prompt zeroed out as the negative.
Base checkpoints take 20 steps at guidance 5, with an empty negative prompt.

The four slots keep their names: "image 3" in the prompt always means slot 3. Klein numbers its references
in the order they come, so when a slot before it is empty the prompt the model gets is renumbered to match
(slots 1 and 3 filled: "image 3" is sent as "image 2"). The prompt saved with the image is the one you wrote.
"""

import os
import re

ROOT = "minimax_flow"
SAMPLER_NODE = "15"     # SamplerCustomAdvanced: its progress is the render's steps (the app listens for this id)
SAVE_NODE = "9"         # SaveImage
SLOTS = 4
REF_MEGAPIXELS = 1.0    # reference pictures are scaled to this before encoding, as in the templates

DISTILLED = {"steps": 4, "minSteps": 1, "maxSteps": 12, "cfg": 1.0, "base": False}
BASE = {"steps": 20, "minSteps": 4, "maxSteps": 60, "cfg": 5.0, "base": True}

# Names for the checkpoints Black Forest Labs publishes; anything else is named after its file.
KNOWN = {
    "flux-2-klein-9b": "Klein 9B Distilled (BF16)",
    "flux-2-klein-9b-fp8": "Klein 9B Distilled (fp8)",
    "flux-2-klein-base-9b": "Klein 9B Base (BF16)",
    "flux-2-klein-base-9b-fp8": "Klein 9B Base (fp8)",
}
# Text encoders and VAEs, best first. MOSS-Image's flux2-vae_small is left out on purpose: Klein's reference
# pictures need the full encoder.
TEXT_ENCODERS = ["qwen_3_8b_fp8mixed", "qwen_3_8b", "qwen_3_8b_fp8"]
VAES = ["flux2-vae", "full_encoder_small_decoder"]
IMAGE_RE = re.compile(r"\b(image)(\s?)([1-4])\b", re.I)


class Missing(Exception):
    """Klein's files aren't all installed."""


def _files(kind):
    import folder_paths

    try:
        return list(folder_paths.get_filename_list(kind))
    except Exception:
        return []


def _stem(path):
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]


def is_base(file):
    return "base" in _stem(file or "").lower()


def preset(file):
    """The settings a checkpoint renders best with."""
    return BASE if is_base(file) else DISTILLED


def is_klein_9b(file):
    stem = _stem(file).lower()
    return "klein" in stem and "9b" in stem and file.lower().endswith((".safetensors", ".sft"))


def parts():
    """The text encoder and VAE Klein 9B needs, or Missing saying which isn't there."""
    encoders = {_stem(f).lower(): f for f in _files("text_encoders")}
    encoder = next((encoders[n] for n in TEXT_ENCODERS if n in encoders), None) or \
        next((f for s, f in sorted(encoders.items()) if s.startswith("qwen_3_8b")), None)
    vaes = {_stem(f).lower(): f for f in _files("vae")}
    vae = next((vaes[n] for n in VAES if n in vaes), None)
    if not encoder:
        raise Missing("Klein's text encoder (qwen_3_8b_fp8mixed.safetensors) isn't in models/text_encoders.")
    if not vae:
        raise Missing("Klein's VAE (flux2-vae.safetensors) isn't in models/vae.")
    return encoder, vae


def models():
    """Every Klein 9B checkpoint in models/diffusion_models, if its text encoder and VAE are there too."""
    checkpoints = [f for f in _files("diffusion_models") if is_klein_9b(f)]
    if not checkpoints:
        raise Missing("No Klein 9B checkpoint (flux-2-klein-9b.safetensors) in models/diffusion_models. "
                      "On RunPod: set ENABLE_KLEIN_9B_BF16=true and HF_TOKEN.")
    parts()
    rows = []
    # Distilled before Base, BF16 before fp8.
    for file in sorted(checkpoints, key=lambda f: (is_base(f), "fp8" in f.lower(), f.lower())):
        stem = _stem(file)
        name = KNOWN.get(stem.lower()) or re.sub(r"[_-]+", " ", stem)
        p = preset(file)
        rows.append({"id": file, "name": name, "file": file, "steps": p["steps"], "minSteps": p["minSteps"],
                     "maxSteps": p["maxSteps"], "cfg": p["cfg"], "base": p["base"], "stepSize": 1})
    return rows


def lora_file(name):
    """The LoRA as ComfyUI lists it: found by its file name if it was moved into a folder (e.g. loras/Flux Klein)."""
    try:
        import folder_paths
        from .h3_graph import pick_file
        return pick_file([name], folder_paths.get_filename_list("loras")) or name
    except Exception:
        return name


def model_prompt(prompt, filled):
    """The prompt as the model should read it: "image N" renumbered to the order the filled slots are sent in.

    filled: the slot numbers (1-4) that have a picture, in order. A slot that is empty keeps its words.
    """
    order = {slot: n + 1 for n, slot in enumerate(filled)}

    def swap(m):
        slot = int(m.group(3))
        return "%s%s%d" % (m.group(1), m.group(2), order[slot]) if slot in order else m.group(0)
    return IMAGE_RE.sub(swap, prompt)


# Face swap: Alissonerdx's BFS (Best Face Swap) head-swap LoRA for Klein 9B, as in Comfy's "face swap" hub
# workflow: picture 1 is the body and scene, picture 2 the head to put on it (in that order, both at 1 MP), the
# LoRA at 1.0, lcm. The files can carry the hub's "Alissonerdx__BFS-Best-Face-Swap__" prefix.
BFS_LORAS = {"bfs_head_v1_flux-klein_9b_step3500_rank128": "BFS head swap · rank 128",
             "bfs_head_v1_flux-klein_9b_step3750_rank64": "BFS head swap · rank 64"}
# The swap prompt from the "Flux2 Klein 9b faceswap detailer" workflow (image 1 the body, image 2 the head).
SWAP_PROMPT = (
    "head_swap: Use image 1 as the base image, preserving its environment, background, camera perspective, framing, "
    "exposure, contrast, and lighting. Remove the head from image 1 and seamlessly replace it with the head from image 2.\n"
    "Match the original head size, face-to-body ratio, neck thickness, shoulder alignment, and camera distance so "
    "proportions remain natural and unchanged.\n\n"
    "Adapt the inserted head to the lighting of image 1 by matching light direction, intensity, softness, color "
    "temperature, shadows, and highlights, with no independent relighting.\n"
    "Preserve the identity of image 2, including hair texture, eye color, nose structure, facial proportions, and skin details.\n"
    "Match the pose and expression from image 1, including head tilt, rotation, eye direction, gaze, micro-expressions, "
    "and lip position.\n"
    "Ensure seamless neck and jaw blending, consistent skin tone, realistic shadow contact, natural skin texture, and "
    "uniform sharpness.\n"
    "Photorealistic, high quality, sharp details, 4K.")
# Extra pictures of the same face (slots 3-5), when there are any.
SWAP_MORE_FACES = "{images} the same person as image 2 from other angles: use them for the identity too."
# Refine face: a second Klein pass on the head, cut out at 1024 px and pasted back (the workflow's detailer).
REFINE_PROMPT = ("Refine this face: sharp, natural skin texture and detail, clear eyes and teeth. Keep the identity, "
                 "expression, pose, lighting and colours exactly as they are.")
SWAP_SLOTS = 5
SWAP_NEGATIVE = ("bad quality, noise, blurry, worst quality, low resolution, blur, distortion, unnatural blending, "
                 "cartoon, illustration, painting")


def bfs_loras():
    """The BFS head-swap LoRAs for Klein 9B that are installed, rank 128 first: [{"file", "name"}]."""
    try:
        import folder_paths
        files = folder_paths.get_filename_list("loras")
    except Exception:
        return []
    stems = {os.path.splitext(os.path.basename(f.replace("\\", "/")))[0].lower(): f for f in files}
    found = []
    for want, label in BFS_LORAS.items():
        f = next((f for stem, f in stems.items() if stem == want or stem.endswith("__" + want)), None)
        if f:
            found.append({"file": f, "name": label})
    return found


def bfs_lora(file=None):
    """The BFS LoRA to use: that file if it is installed, else the first one; None if there is none."""
    found = bfs_loras()
    return next((x["file"] for x in found if x["file"] == file), found[0]["file"] if found else None)


def lanpaint():
    """Is LanPaint's KSampler (scraed/LanPaint) installed?"""
    try:
        import nodes
        return "LanPaint_KSampler" in nodes.NODE_CLASS_MAPPINGS
    except Exception:
        return False


def swap_prompt(notes="", faces=2):
    """The face swap's prompt, with a line about the extra face pictures and your notes after it.
    faces: how many pictures go in, body and face included."""
    text = SWAP_PROMPT
    if faces > 2:
        more = {3: "Image 3 shows", 4: "Images 3 and 4 show"}.get(faces, "Images 3 to %d show" % faces)
        text += "\n" + SWAP_MORE_FACES.format(images=more)
    notes = (notes or "").strip()
    return text + ("\n" + notes if notes else "")


def build_graph(clip, refs, name=None):
    """The ComfyUI graph for one Klein image.

    clip: the image as the app keeps it (prompt, profile = checkpoint file, steps, cfg, width, height, seed,
    loras, negative for Base). refs: (slot number, file inside the output folder) for each filled slot, in order.
    """
    encoder, vae = parts()
    p = preset(clip["profile"])
    w, h = int(clip["width"]), int(clip["height"])
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": clip["profile"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0],
                                                         "text": model_prompt(clip["sent"] if clip.get("swap") else clip["prompt"],
                                                                                  [s for s, _ in refs])}},
    }
    if p["base"]:
        graph["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": clip.get("negative") or ""}}
    else:
        graph["5"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
    model = ["1", 0]
    if clip.get("swap"):
        bfs = bfs_lora(clip.get("bfs"))
        if not bfs:
            raise Missing("Face swap needs the BFS head-swap LoRA for Klein 9B (bfs_head_v1_flux-klein_9b_step3750_rank64."
                          "safetensors from Alissonerdx/BFS-Best-Face-Swap) in models/loras. On RunPod: ENABLE_BFS_KLEIN=true.")
        graph["19"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": bfs, "strength_model": 1.0}}
        model = ["19", 0]
        if p["base"]:
            graph["5"]["inputs"]["text"] = clip.get("negative") or SWAP_NEGATIVE
    for i, lora in enumerate(clip.get("loras") or []):
        key = str(20 + i)
        graph[key] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": model, "lora_name": lora_file(lora["name"]), "strength_model": float(lora["strength"])}}
        model = [key, 0]
    positive, negative = ["4", 0], ["5", 0]
    for n, (slot, file) in enumerate(refs[:SWAP_SLOTS if clip.get("swap") else SLOTS]):
        load, scale, enc, pos, neg = (str(40 + 10 * n + k) for k in range(5))
        graph[load] = {"class_type": "MMVisionLoadStill", "inputs": {"file": file}}
        graph[scale] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load, 0], "upscale_method": "lanczos",
            # A face swap's pictures go in at its own size (the workflow: 2 MP), an edit's at 1 MP.
            "megapixels": float(clip["res"]) if clip.get("swap") else REF_MEGAPIXELS, "resolution_steps": 1}}
        graph[enc] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale, 0], "vae": ["3", 0]}}
        graph[pos] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": positive, "latent": [enc, 0]}}
        graph[neg] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": negative, "latent": [enc, 0]}}
        positive, negative = [pos, 0], [neg, 0]
    graph.update({
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "lcm" if clip.get("swap") else "euler"}},
        "12": {"class_type": "Flux2Scheduler", "inputs": {"steps": int(clip["steps"]), "width": w, "height": h}},
        "13": {"class_type": "CFGGuider", "inputs": {"model": model, "positive": positive, "negative": negative,
                                                     "cfg": float(clip["cfg"])}},
        "14": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(clip["seed"])}},
        SAMPLER_NODE: {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["14", 0], "guider": ["13", 0], "sampler": ["11", 0], "sigmas": ["12", 0], "latent_image": ["6", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": [SAMPLER_NODE, 0], "vae": ["3", 0]}},
        SAVE_NODE: {"class_type": "SaveImage", "inputs": {"images": ["8", 0],
                                                          "filename_prefix": "%s/stills/%s" % (ROOT, name or clip["id"])}},
    })
    if clip.get("swap"):
        _swap_extras(graph, clip, refs, model, positive, negative, w, h)
    return graph


def _swap_extras(graph, clip, refs, model, positive, negative, w, h):
    """Its nodes are 100+ and 120+, clear of the pictures' 40-89.
    A face swap's head mask (only the head changes, the rest stays as it was, LanPaint if chosen) and
    Refine face (the head again at 1024 px, pasted back)."""
    mask = (clip.get("mask") or {}).get("file")
    image = ["8", 0]
    if mask:
        body = refs[0][1]
        graph.update({
            "100": {"class_type": "MMVisionLoadStill", "inputs": {"file": body}},
            "101": {"class_type": "ImageScale", "inputs": {"image": ["100", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}},
            "102": {"class_type": "VAEEncode", "inputs": {"pixels": ["101", 0], "vae": ["3", 0]}},
            "103": {"class_type": "MMVisionLoadMask", "inputs": {"file": mask, "width": w, "height": h,
                                                                 "grow": int(clip["mask"].get("grow", 8)), "feather": 0}},
            "104": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["102", 0], "mask": ["103", 0]}},
            # The soft-edged mask for putting the new head into the untouched body picture.
            "105": {"class_type": "MMVisionLoadMask", "inputs": {"file": mask, "width": w, "height": h,
                                                                 "grow": int(clip["mask"].get("grow", 8)),
                                                                 "feather": int(clip["mask"].get("feather", 12))}},
        })
        if clip.get("sampler") == "lanpaint":
            # LanPaint's KSampler, as in the workflow: euler, simple, denoise 1, 2 thinking steps, Image First.
            graph[SAMPLER_NODE] = {"class_type": "LanPaint_KSampler", "inputs": {
                "model": model, "seed": int(clip["seed"]), "steps": int(clip.get("lanSteps") or 20), "cfg": float(clip["cfg"]),
                "sampler_name": "euler", "scheduler": "simple", "positive": positive, "negative": negative,
                "latent_image": ["104", 0], "denoise": 1.0, "LanPaint_NumSteps": 2, "LanPaint_PromptMode": "Image First",
                "LanPaint_Info": "LanPaint KSampler.", "Inpainting_mode": "\U0001f5bc\ufe0f Image Inpainting"}}
            for k in ("11", "12", "13", "14"):
                graph.pop(k, None)
        else:
            graph[SAMPLER_NODE]["inputs"]["latent_image"] = ["104", 0]
        graph.pop("6", None)
        graph["106"] = {"class_type": "ImageCompositeMasked", "inputs": {
            "destination": ["101", 0], "source": ["8", 0], "x": 0, "y": 0, "resize_source": False, "mask": ["105", 0]}}
        image = ["106", 0]
    if clip.get("refine") and mask:
        # Refine face: the head cut out at 1024 px, rendered again by plain Klein with itself as the reference,
        # only inside the mask and part of the way (refine strength), then pasted back.
        strength = float(clip.get("refineStrength", 0.5))
        graph.update({
            "120": {"class_type": "MMVisionLoadMask", "inputs": {"file": mask, "width": w, "height": h,
                                                                 "grow": int(clip["mask"].get("grow", 8)) + 8,
                                                                 "feather": int(clip["mask"].get("feather", 12))}},
            "121": {"class_type": "MMVisionCropToMask", "inputs": {"image": image, "mask": ["120", 0], "padding": 0.35, "size": 1024}},
            "122": {"class_type": "VAEEncode", "inputs": {"pixels": ["121", 0], "vae": ["3", 0]}},
            "123": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": REFINE_PROMPT}},
            "124": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["123", 0], "latent": ["122", 0]}},
            "125": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["123", 0]}},
            "126": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["122", 0], "mask": ["121", 1]}},
            "127": {"class_type": "Flux2Scheduler", "inputs": {"steps": 8, "width": 1024, "height": 1024}},
            "128": {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["127", 0], "denoise": strength}},
            "129": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["124", 0], "negative": ["125", 0], "cfg": 1.0}},
            "130": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "131": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(clip["seed"]) + 1}},
            "132": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["131", 0], "guider": ["129", 0], "sampler": ["130", 0], "sigmas": ["128", 1], "latent_image": ["126", 0]}},
            "133": {"class_type": "VAEDecode", "inputs": {"samples": ["132", 0], "vae": ["3", 0]}},
            "134": {"class_type": "MMVisionPasteCrop", "inputs": {"image": image, "crop": ["133", 0], "crop_mask": ["121", 1], "box": ["121", 2]}},
        })
        image = ["134", 0]
    graph[SAVE_NODE]["inputs"]["images"] = image


# Live previews while it samples: ComfyUI picks TAESD or a quick colour preview by itself.
EXTRA_DATA = {"preview_method": "auto"}
