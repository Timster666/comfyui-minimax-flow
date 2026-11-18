"""Pruna AI's P-H3-Image, with ComfyUI's native nodes (ComfyUI 0.44 or newer, no extra node pack).

The graph follows Comfy-Org's P-H3-Image templates (image_p_h3_t2i and image_p_h3_image_edit): MiniMax H3's
FL2VA model with Pruna's P-H3-Image LoRA at 1.0, ModelSamplingMiniMaxH3 at 1.0/1.0, P-H3's own text encoder
and VAE, "p-h3-image Conditioning" (the prompt and up to nine pictures), then a fixed 8-step schedule
(ManualSigmas, er_sde, guidance 1 with BasicGuider). If Pruna's full P-H3-Image checkpoint is installed too,
it can be picked instead of FL2VA + LoRA.

Enhance prompt runs Pruna's prompt upsampler first (TextGenerateV2 on the upsampler model, as in the
templates): it gets "Aspect ratio: 4:3 / Request: ..." and the pictures, and writes the long prompt the
model then reads. Off, the text goes to the model as it is.

MiniMax's own image model, MiniMax-H3-Image (ComfyUI 0.45 or newer), is offered in the same tab, so the two can be
compared on the same prompt and pictures: Comfy-Org's image_minimax_h3_image_t2i/edit templates (the H3-Image
model, MiniMax H3's Qwen3-VL text encoder, H3-Image's VAE, "MiniMax H3 Image Conditioning", 6 steps euler/simple
at CFG 1 with the prompt zeroed out as the negative). It works at 4 to 16 MP (16 is MiniMax's sweet spot). It gets
the prompt as written: no prompt expander (the templates' Qwen3.5 one isn't used).

The slots are picture 1 to picture 9: picture 1 is the one edited, the others are references, and with no
pictures it is text-to-image. The model calls them <Picture 1>, <Picture 2> ... in the order they come, so
"picture 3" with slot 2 empty is sent as <Picture 2>. The prompt saved with the image is the one you wrote.
"""

import math
import os
import re

ROOT = "minimax_flow"
SAMPLER_NODE = "15"     # SamplerCustomAdvanced: its progress is the render's steps (the app listens for this id)
SAVE_NODE = "9"         # SaveImage
UPSAMPLED_NODE = "30"   # PreviewAny on the upsampler's text: read back from the history when the image is done
SLOTS = 9
STEPS = 8
SIGMAS = "1.0, 0.984753, 0.967905, 0.951688, 0.925372, 0.881923, 0.810083, 0.528718, 0.0"
GRID = 32               # the model's canvas is in steps of 32 px
MEGAPIXELS = [1.0, 2.0, 4.0, 8.0]
VISION_MEGAPIXELS = 0.95  # the pictures the upsampler looks at, as in the edit template

LORA = "p-h3-image_lora_bf16"
FL2VA = ["minimax_h3_fl2va_pruned_int8_convrot", "minimax_h3_fl2va_pruned_bf16", "minimax_h3_fl2va_pruned_fp8"]
TEXT_ENCODERS = ["p-h3-image_text_encoder_bf16", "p-h3-image_text_encoder_int8_convrot", "p-h3-image_text_encoder_w4a8"]
VAES = ["p-h3-image_vae_bf16", "p-h3-image_vae_int8_convrot", "p-h3-image_vae"]
UPSAMPLERS = ["p-h3-image_prompt_upsampler_bf16", "p-h3-image_prompt_upsampler_int8_convrot", "p-h3-image_prompt_upsampler_w4a8"]
FULL = {"p-h3-image_bf16": "P-H3-Image (BF16)", "p-h3-image_int8_convrot": "P-H3-Image (INT8)", "p-h3-image_w4a8": "P-H3-Image (W4A8)"}
LORA_ROUTE = "MiniMax H3 + P-H3-Image LoRA"

H3IMAGE = {"minimax_h3_image_pruned_int8_convrot": "MiniMax H3-Image (INT8)",
           "minimax_h3_image_pruned_bf16": "MiniMax H3-Image (BF16)",
           "minimax_h3_image_pruned_w4a8": "MiniMax H3-Image (W4A8)"}
H3IMAGE_ENCODERS = ["qwen3vl_32b_minimax_h3_int8_convrot", "qwen3vl_32b_minimax_h3_nvfp4_awq", "qwen3vl_32b_minimax_h3_bf16"]
H3IMAGE_VAES = ["minimax_h3_image_vae_fp16", "minimax_h3_image_vae_bf16", "minimax_h3_image_vae"]
H3IMAGE_STEPS = 6
H3IMAGE_MEGAPIXELS = [4.0, 8.0, 12.0, 16.0]

# Shapes the upsampler is told about: the nearest of these to the real size.
RATIOS = {"1:1": 1.0, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9, "21:9": 21 / 9, "3:1": 3.0, "4:1": 4.0,
          "3:4": 3 / 4, "2:3": 2 / 3, "9:16": 9 / 16}
PICTURE_RE = re.compile(r"\bpicture\s?([1-9])\b", re.I)
EDIT_PREFIX = "Edit <Picture 1>. "
VERBATIM_RE = re.compile(r"\s*(Edit <Picture \d+>|subject_definitions:)")


class Missing(Exception):
    """P-H3-Image's files aren't all installed."""


def _files(kind):
    import folder_paths

    try:
        return list(folder_paths.get_filename_list(kind))
    except Exception:
        return []


def _stem(path):
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0].lower()


def _find(kind, names):
    """The first of names (file stems, best first) that is installed in that models folder, any subfolder."""
    have = {}
    for f in _files(kind):
        have.setdefault(_stem(f), f)
    return next((have[n] for n in names if n in have), None)


def parts():
    """The text encoder and VAE P-H3-Image needs, or Missing saying which isn't there."""
    encoder = _find("text_encoders", TEXT_ENCODERS)
    vae = _find("vae", VAES)
    if not encoder:
        raise Missing("P-H3-Image's text encoder (p-h3-image_text_encoder_int8_convrot.safetensors) isn't in models/text_encoders. "
                      "On RunPod: set ENABLE_PH3_IMAGE=true.")
    if not vae:
        raise Missing("P-H3-Image's VAE (p-h3-image_vae_int8_convrot.safetensors) isn't in models/vae.")
    return encoder, vae


def upsampler():
    """The prompt upsampler for Enhance prompt, or None."""
    return _find("text_encoders", UPSAMPLERS)


def fl2va():
    files = _files("diffusion_models")
    found = _find("diffusion_models", FL2VA)
    return found or next((f for f in sorted(files) if "fl2va" in _stem(f) and "hyperflow" not in _stem(f)), None)


def lora():
    return _find("loras", [LORA])


def h3image_parts():
    """H3-Image's text encoder (MiniMax H3's) and VAE, or Missing."""
    encoder = _find("text_encoders", H3IMAGE_ENCODERS)
    vae = _find("vae", H3IMAGE_VAES)
    if not encoder:
        raise Missing("MiniMax H3's text encoder (qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) isn't in models/text_encoders: "
                      "H3-Image uses it.")
    if not vae:
        raise Missing("H3-Image's VAE (minimax_h3_image_vae_fp16.safetensors) isn't in models/vae. "
                      "On RunPod: set ENABLE_H3_IMAGE=true.")
    return encoder, vae


def h3image_models():
    """MiniMax-H3-Image checkpoints that are installed (with its parts), as rows; [] if none."""
    files = [f for f in _files("diffusion_models") if _stem(f) in H3IMAGE]
    if not files:
        return []
    try:
        import nodes
        if "MiniMaxH3ImageConditioning" not in nodes.NODE_CLASS_MAPPINGS:
            raise Missing("This ComfyUI is too old for MiniMax H3-Image: it needs ComfyUI 0.45 or newer (the \"MiniMax "
                          "H3 Image Conditioning\" node). On RunPod: set UPDATE_COMFYUI=true and restart the pod.")
    except ImportError:
        pass
    h3image_parts()
    order = list(H3IMAGE)
    files.sort(key=lambda f: order.index(_stem(f)))
    return [{"id": "h3image:" + f, "name": H3IMAGE[_stem(f)], "file": f, "lora": None, "h3image": True,
             "steps": H3IMAGE_STEPS, "minSteps": H3IMAGE_STEPS, "maxSteps": H3IMAGE_STEPS, "stepSize": 1, "cfg": 1.0,
             "megapixels": H3IMAGE_MEGAPIXELS, "recommendedMP": 16.0, "loras": False} for f in files]


def models():
    """Everything the Pruna tab can run: P-H3-Image (MiniMax H3 FL2VA + the LoRA, Pruna's full checkpoints) and
    MiniMax-H3-Image. Missing when there is none, with P-H3-Image's reason first."""
    rows, why = [], None
    try:
        rows = ph3_models()
    except Missing as error:
        why = error
    try:
        rows += h3image_models()
    except Missing as error:
        why = why or error
        if not rows:
            raise
    if not rows:
        raise why
    return rows


def route_for(profile):
    rows = {r["id"]: r for r in models()}
    return rows.get(profile) or next(iter(rows.values()))


def ph3_models():
    """The ways to run P-H3-Image that are installed: MiniMax H3 FL2VA + the LoRA, and Pruna's full checkpoints."""
    try:
        import nodes
        old = "PH3ImageConditioning" not in nodes.NODE_CLASS_MAPPINGS
    except Exception:
        old = False
    if old:
        raise Missing("This ComfyUI is too old for P-H3-Image: it needs ComfyUI 0.44 or newer (the \"p-h3-image "
                      "Conditioning\" node). On RunPod: set UPDATE_COMFYUI=true and restart the pod.")
    rows = []
    base, adapter = fl2va(), lora()
    if base and adapter:
        rows.append({"id": "lora", "name": LORA_ROUTE, "file": base, "lora": adapter})
    for f in _files("diffusion_models"):
        if _stem(f) in FULL:
            rows.append({"id": f, "name": FULL[_stem(f)], "file": f, "lora": None})
    if not rows:
        if not adapter:
            raise Missing("The P-H3-Image LoRA (p-h3-image_lora_bf16.safetensors) isn't in models/loras. "
                          "On RunPod: set ENABLE_PH3_IMAGE=true.")
        raise Missing("MiniMax H3's FL2VA model (minimax_h3_fl2va_pruned_int8_convrot.safetensors) isn't in "
                      "models/diffusion_models: P-H3-Image's LoRA runs on it.")
    parts()
    for row in rows:
        row.update({"steps": STEPS, "minSteps": STEPS, "maxSteps": STEPS, "stepSize": 1, "cfg": 1.0,
                    "megapixels": MEGAPIXELS, "recommendedMP": 4.0, "loras": True})
    return rows


def lora_file(name):
    """A LoRA as ComfyUI lists it: found by its file name if it was moved into a folder."""
    try:
        import folder_paths
        from .h3_graph import pick_file
        return pick_file([name], folder_paths.get_filename_list("loras")) or name
    except Exception:
        return name


def size_for(w0, h0, mp):
    """Width and height with the shape w0 x h0 at about mp megapixels, in steps of 32. A megapixel is 1024 x 1024
    here, as in ComfyUI's Resolution Selector: 8 MP at 4:3 is the templates' 3360 x 2496."""
    k = math.sqrt(mp * 1024 * 1024 / (w0 * h0))
    return max(GRID, round(w0 * k / GRID) * GRID), max(GRID, round(h0 * k / GRID) * GRID)


def ratio_name(w, h):
    r = w / h
    return min(RATIOS, key=lambda name: abs(math.log(RATIOS[name] / r)))


def model_prompt(prompt, filled):
    """The prompt with "picture N" as the model's <Picture k>, numbered in the order the filled slots are sent.

    filled: the slot numbers (1-9) that have a picture, in order. A slot that is empty keeps its words.
    """
    order = {slot: n + 1 for n, slot in enumerate(filled)}

    def swap(m):
        slot = int(m.group(1))
        return "<Picture %d>" % order[slot] if slot in order else m.group(0)
    return PICTURE_RE.sub(swap, prompt)


def sent_text(text, has_pictures, enhance, width, height, h3image=False):
    """What the model (or, with Enhance, the upsampler) is given. H3-Image reads an edit as written."""
    if enhance:
        return "Aspect ratio: %s\nRequest: %s" % (ratio_name(width, height), text)
    if has_pictures and not h3image and not VERBATIM_RE.match(text):
        return EDIT_PREFIX + text
    return text


def build_graph(clip, refs, name=None):
    """The ComfyUI graph for one P-H3-Image picture.

    clip: the image as the app keeps it (sent = the text for the model or the upsampler, profile = "lora" or a
    checkpoint file, width, height, seed, loras, enhance). refs: files inside the output folder, in slot order.
    """
    route = route_for(clip["profile"])
    if route.get("h3image"):
        return h3image_graph(clip, refs, route, name)
    encoder, vae = parts()
    w, h = int(clip["width"]), int(clip["height"])
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": route["file"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    model = ["1", 0]
    if route.get("lora"):
        graph["19"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": model, "lora_name": route["lora"], "strength_model": 1.0}}
        model = ["19", 0]
    # Your LoRAs (P-H3-Image's own, or MiniMax H3's: it is the same model underneath) go through Vision's H3
    # LoRA node, as in the Video tab: added at run time on the INT8 weights, with their adaLN part on pruned FL2VA.
    for i, extra in enumerate(clip.get("loras") or []):
        key = str(20 + i)
        graph[key] = {"class_type": "MMVisionLora",
                      "inputs": {"model": model, "lora_name": lora_file(extra["name"]), "strength": float(extra["strength"])}}
        model = [key, 0]
    graph["18"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": model, "shift_video": 1.0, "shift_audio": 1.0}}

    pictures = {}
    vision = {}
    for n, file in enumerate(refs[:SLOTS]):
        load = str(40 + 2 * n)
        graph[load] = {"class_type": "MMVisionLoadStill", "inputs": {"file": file}}
        pictures["image_%d" % (n + 1)] = [load, 0]
        if clip.get("enhance"):
            small = str(41 + 2 * n)
            graph[small] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": [load, 0], "upscale_method": "lanczos", "megapixels": VISION_MEGAPIXELS, "resolution_steps": GRID}}
            vision["image%d" % (n + 1)] = [small, 0]

    text = clip["sent"]
    if clip.get("enhance"):
        up = upsampler()
        if not up:
            raise Missing("Enhance prompt needs Pruna's prompt upsampler (p-h3-image_prompt_upsampler_bf16.safetensors) "
                          "in models/text_encoders.")
        graph["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": up, "type": "minimax", "device": "default"}}
        inputs = {"clip": ["4", 0], "prompt": text, "max_length": 1536, "sampling_mode": "off",
                  "thinking": True, "use_default_template": True, "mtp": "auto"}
        for key, link in vision.items():
            inputs["images." + key] = link
        graph["29"] = {"class_type": "TextGenerateV2", "inputs": inputs}
        graph[UPSAMPLED_NODE] = {"class_type": "PreviewAny", "inputs": {"source": ["29", 0]}}
        text = ["29", 0]
    cond = {"clip": ["2", 0], "vae": ["3", 0], "prompt": text, "width": w, "height": h}
    for key, link in pictures.items():
        cond["images." + key] = link
    graph["10"] = {"class_type": "PH3ImageConditioning", "inputs": cond}
    graph.update({
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "er_sde"}},
        "12": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS}},
        "13": {"class_type": "BasicGuider", "inputs": {"model": ["18", 0], "conditioning": ["10", 0]}},
        "14": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(clip["seed"])}},
        SAMPLER_NODE: {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["14", 0], "guider": ["13", 0], "sampler": ["11", 0], "sigmas": ["12", 0], "latent_image": ["10", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": [SAMPLER_NODE, 0], "vae": ["3", 0]}},
        SAVE_NODE: {"class_type": "SaveImage", "inputs": {"images": ["8", 0],
                                                          "filename_prefix": "%s/stills/%s" % (ROOT, name or clip["id"])}},
    })
    return graph


def h3image_graph(clip, refs, route, name=None):
    """MiniMax-H3-Image, as in Comfy-Org's T2I and edit templates."""
    encoder, vae = h3image_parts()
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": route["file"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    loads = []
    for n, file in enumerate(refs[:SLOTS]):
        load = str(40 + 2 * n)
        graph[load] = {"class_type": "MMVisionLoadStill", "inputs": {"file": file}}
        loads.append([load, 0])
    cond = {"clip": ["2", 0], "vae": ["3", 0], "prompt": clip["sent"],
            "width": int(clip["width"]), "height": int(clip["height"])}
    for n, link in enumerate(loads):
        cond["images.image_%d" % (n + 1)] = link
    graph["10"] = {"class_type": "MiniMaxH3ImageConditioning", "inputs": cond}
    graph.update({
        "11": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["10", 0]}},
        SAMPLER_NODE: {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": int(clip["seed"]), "steps": H3IMAGE_STEPS, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["10", 0], "negative": ["11", 0], "latent_image": ["10", 1], "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": [SAMPLER_NODE, 0], "vae": ["3", 0]}},
        SAVE_NODE: {"class_type": "SaveImage", "inputs": {"images": ["8", 0],
                                                          "filename_prefix": "%s/stills/%s" % (ROOT, name or clip["id"])}},
    })
    return graph


def upsampled_text(entry):
    """What the upsampler wrote, from a finished job's history, or None."""
    out = ((entry or {}).get("outputs") or {}).get(UPSAMPLED_NODE) or {}
    text = out.get("text")
    if isinstance(text, (list, tuple)) and text:
        text = text[0]
    return text if isinstance(text, str) and text.strip() else None


# Live previews while it samples: ComfyUI picks a preview method by itself (TAEH3 if it is installed).
EXTRA_DATA = {"preview_method": "auto"}
