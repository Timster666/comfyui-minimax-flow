"""Image renders with MOSS-Image, ComfyUI's native support for it (no extra node pack).

The graph follows Comfy-Org's MOSS-Image text-to-image template: the model, the Qwen3.5 text
encoder loaded as "moss_image", the Flux2 small VAE, the recommended negative prompt and a
KSampler with er_sde / simple. The template's prompt rewriter and skip-layer guidance are off,
as they are by default there.

MOSS-Image's files are found by name in ComfyUI's model folders, so any MOSS checkpoint you
add (BF16, Base, a finetune) shows up as a model of its own.

Distilled "turbo" checkpoints (a file name with "dmd", "turbo" or "Nstep") render in a few
steps: they get their own settings (8 steps, guidance 1, Euler, no negative prompt).
"""

import os
import re

ROOT = "minimax_flow"
SAMPLER_NODE = "15"     # its progress is the render's steps (the app listens for this id)
SAVE_NODE = "9"
NEGATIVE = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"
SAMPLER, SCHEDULER = "er_sde", "simple"
DEFAULT_STEPS, DEFAULT_CFG = 50, 3.0

# Settings per kind of checkpoint: the full model, and step-distilled (turbo) ones.
FULL = {"steps": DEFAULT_STEPS, "cfg": DEFAULT_CFG, "sampler": SAMPLER, "scheduler": SCHEDULER, "negative": NEGATIVE}
TURBO = {"steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "negative": ""}

# Names for the checkpoints Comfy-Org publishes; anything else is named after its file.
KNOWN = {
    "moss_image_sft_int8_convrot": "MOSS-Image SFT (INT8)",
    "moss_image_sft_bf16": "MOSS-Image SFT (BF16)",
    "moss_image_base_int8_convrot": "MOSS-Image Base (INT8)",
    "moss_image_base_bf16": "MOSS-Image Base (BF16)",
    "moss_image_sft_8stepdmd_int8_convrot": "MOSS-Image SFT Turbo 8-step (INT8)",
}
# Text encoders and VAEs, best first.
TEXT_ENCODERS = ["qwen3.5_9b_w4a8_gptq", "qwen3.5_9b_int8_convrot", "qwen3.5_9b_bf16"]
VAES = ["flux2-vae_small", "flux2-vae"]


class Missing(Exception):
    """MOSS-Image's files aren't all installed."""


def _files(kind):
    import folder_paths

    try:
        return list(folder_paths.get_filename_list(kind))
    except Exception:
        return []


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def _pick(files, preferred, fallback):
    by_stem = {_stem(f).lower(): f for f in files}
    for name in preferred:
        if name in by_stem:
            return by_stem[name]
    return next((f for f in files if fallback(_stem(f).lower())), None)


def is_turbo(file):
    stem = _stem(file or "").lower()
    return "dmd" in stem or "turbo" in stem or re.search(r"\d+step", stem) is not None


def preset(file):
    """The settings a checkpoint renders best with."""
    return TURBO if is_turbo(file) else FULL


def parts():
    """The text encoder and VAE MOSS-Image needs, or Missing saying which isn't there."""
    encoder = _pick(_files("text_encoders"), TEXT_ENCODERS, lambda s: s.startswith("qwen3.5_9b") or "qwen35" in s)
    vae = _pick(_files("vae"), VAES, lambda s: "flux2" in s and "vae" in s)
    if not encoder:
        raise Missing("MOSS-Image's text encoder (qwen3.5_9b_w4a8_gptq.safetensors) isn't in models/text_encoders.")
    if not vae:
        raise Missing("MOSS-Image's VAE (flux2-vae_small.safetensors) isn't in models/vae.")
    return encoder, vae


def models():
    """Every MOSS-Image checkpoint in models/diffusion_models, if its text encoder and VAE are there too."""
    checkpoints = [f for f in _files("diffusion_models") if "moss" in _stem(f).lower()]
    if not checkpoints:
        raise Missing("No MOSS-Image checkpoint (moss_image_sft_int8_convrot.safetensors) in models/diffusion_models.")
    parts()
    rows = []
    # INT8 first, and the full model before its turbo version.
    for file in sorted(checkpoints, key=lambda f: (0 if "int8" in f.lower() else 1, is_turbo(f), f.lower())):
        stem = _stem(file)
        name = KNOWN.get(stem.lower()) or re.sub(r"[_-]+", " ", stem)
        if is_turbo(file) and "turbo" not in name.lower() and stem.lower() not in KNOWN:
            name += " (turbo)"
        p = preset(file)
        rows.append({"id": file, "name": name, "file": file, "steps": p["steps"], "minSteps": 1, "maxSteps": 80,
                     "stepSize": 1, "cfg": p["cfg"]})
    return rows


def build_graph(clip, name=None):
    """The ComfyUI graph for one MiniMax H3 Vision image.

    clip: the image as the app keeps it (prompt, profile = checkpoint file, steps, cfg, width, height, seed, loras).
    """
    encoder, vae = parts()
    p = preset(clip["profile"])
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": clip["profile"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "moss_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": clip["prompt"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": p["negative"]}},
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": int(clip["width"]), "height": int(clip["height"]), "batch_size": 1}},
    }
    model = ["1", 0]
    for i, lora in enumerate(clip.get("loras") or []):
        key = str(20 + i)
        graph[key] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": model, "lora_name": lora["name"], "strength_model": float(lora["strength"])}}
        model = [key, 0]
    graph[SAMPLER_NODE] = {"class_type": "KSampler", "inputs": {
        "model": model, "seed": int(clip["seed"]), "steps": int(clip["steps"]), "cfg": float(clip["cfg"]),
        "sampler_name": p["sampler"], "scheduler": p["scheduler"], "positive": ["4", 0], "negative": ["5", 0],
        "latent_image": ["6", 0], "denoise": 1.0}}
    graph["8"] = {"class_type": "VAEDecode", "inputs": {"samples": [SAMPLER_NODE, 0], "vae": ["3", 0]}}
    graph[SAVE_NODE] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "%s/stills/%s" % (ROOT, name or clip["id"])}}
    return graph


# Live previews while it samples: ComfyUI picks TAESD or a quick colour preview by itself.
EXTRA_DATA = {"preview_method": "auto"}


def saved_file(entry):
    """The image a finished job saved, as {filename, subfolder}, or None."""
    for found in ((entry or {}).get("outputs") or {}).get(SAVE_NODE, {}).get("images") or []:
        if isinstance(found, dict) and found.get("filename"):
            return found
    return None
