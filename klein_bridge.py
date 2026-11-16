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
BFS_LORAS = ["bfs_head_v1_flux-klein_9b_step3750_rank64", "bfs_head_v1_flux-klein_9b_step3500_rank128"]
SWAP_PROMPT = ("head_swap: start with Picture 1 as the base image, keeping its lighting, environment and background. "
               "Replace the head in Picture 1 with the head from Picture 2, keeping the hair, eye colour and nose shape "
               "from Picture 2. Copy the eye direction, head rotation and micro-expressions from Picture 1. Match the "
               "skin tone and light so the head and body blend naturally. high quality, sharp details, 4k")
SWAP_NEGATIVE = ("bad quality, noise, blurry, worst quality, low resolution, blur, distortion, unnatural blending, "
                 "cartoon, illustration, painting")


def bfs_lora():
    """The BFS head-swap LoRA for Klein 9B as ComfyUI lists it, or None."""
    try:
        import folder_paths
        files = folder_paths.get_filename_list("loras")
    except Exception:
        return None
    stems = {os.path.splitext(os.path.basename(f.replace("\\", "/")))[0].lower(): f for f in files}
    for want in BFS_LORAS:
        for stem, f in stems.items():
            if stem == want or stem.endswith("__" + want):
                return f
    return None


def swap_prompt(notes=""):
    """The face swap's prompt, with your notes after it."""
    notes = (notes or "").strip()
    return SWAP_PROMPT + (". " + notes if notes else "")


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
        bfs = bfs_lora()
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
    for n, (slot, file) in enumerate(refs[:SLOTS]):
        load, scale, enc, pos, neg = (str(40 + 10 * n + k) for k in range(5))
        graph[load] = {"class_type": "MMVisionLoadStill", "inputs": {"file": file}}
        graph[scale] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load, 0], "upscale_method": "lanczos", "megapixels": REF_MEGAPIXELS, "resolution_steps": 1}}
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
    return graph


# Live previews while it samples: ComfyUI picks TAESD or a quick colour preview by itself.
EXTRA_DATA = {"preview_method": "auto"}
