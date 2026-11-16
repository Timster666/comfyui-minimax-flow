"""The graph nodes MiniMax H3 Vision's video renders use, besides ComfyUI's own.

They come from the H3 editor's nodes (see NOTICE), under names of their own (MMVision...), so this pack
and the H3 editor can be installed side by side. Each video graph uses them like this:

- MMVisionLoadStill: an @image, read from the output folder, where the app keeps uploads.
- MMVisionHyperFlowLoader and MMVisionHyperFlowSigmas: HyperFlow H3 Flash, its model and its 4, 6 or 8-step schedule.
- MMVisionLora: an H3 LoRA, renumbered for HyperFlow and added at run time on INT8 weights.
- MMVisionLivePreview: the moving preview of the clip at every step.
"""

import os

import folder_paths
import numpy as np
import torch
from comfy_api.latest import io
from PIL import Image, ImageOps

from . import h3_lora, hyperflow, live_preview

CATEGORY = "MiniMax H3 Vision"


def output_file(file):
    base = os.path.realpath(folder_paths.get_output_directory())
    path = os.path.realpath(os.path.join(base, file))
    if not path.startswith(base + os.sep) or not os.path.exists(path):
        raise ValueError("no file at %s in the output folder" % file)
    return path


class LoadStill(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionLoadStill",
            display_name="Vision Load Still",
            category=CATEGORY,
            description="Read a picture from the output folder, where MiniMax H3 Vision keeps uploaded images.",
            inputs=[io.String.Input("file", tooltip="Path inside the output folder.")],
            outputs=[io.Image.Output(display_name="image")],
        )

    @classmethod
    def execute(cls, file) -> io.NodeOutput:
        with Image.open(output_file(file)) as image:
            pixels = np.array(ImageOps.exif_transpose(image).convert("RGB")).astype(np.float32) / 255.0
        return io.NodeOutput(torch.from_numpy(pixels)[None])


class Lora(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionLora",
            display_name="Vision H3 LoRA",
            category=CATEGORY,
            description=(
                "Apply a LoRA trained on MiniMax H3. On INT8 or FP8 weights and HyperFlow's factorized layers it is "
                "added at run time, so small updates are not rounded away. On HyperFlow, whose blocks are pruned, its "
                "blocks move down to the layers they were trained for. Several in a row stack."
            ),
            inputs=[
                io.Model.Input("model"),
                io.Combo.Input("lora_name", options=folder_paths.get_filename_list("loras")),
                io.Float.Input("strength", default=1.0, min=-4.0, max=4.0, step=0.05),
                io.Float.Input("start_sigma", default=1.0, min=0.0, max=1.0, step=0.005, optional=True,
                               tooltip="Highest noise level (sigma) where this LoRA runs. 1.0 runs from the first step."),
                io.Float.Input("end_sigma", default=0.0, min=0.0, max=1.0, step=0.005, optional=True,
                               tooltip="Lowest noise level (sigma) where this LoRA runs. 0.0 runs to the final step."),
            ],
            outputs=[io.Model.Output(display_name="model")],
        )

    @classmethod
    def execute(cls, model, lora_name, strength, start_sigma=1.0, end_sigma=0.0) -> io.NodeOutput:
        path = folder_paths.get_full_path_or_raise("loras", lora_name)
        return io.NodeOutput(h3_lora.apply(model, path, lora_name, strength, "auto", start_sigma, end_sigma))


class HyperFlowLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionHyperFlowLoader",
            display_name="Vision HyperFlow Loader",
            category=CATEGORY,
            description=(
                "Load a HyperFlow H3 Flash INT8 safetensors checkpoint: MiniMax H3 distilled to 4, 6 or 8 steps, "
                "with some blocks pruned. Sample it with Vision HyperFlow Sigmas, euler and no CFG."
            ),
            inputs=[io.Combo.Input("unet_name", options=hyperflow.files())],
            outputs=[io.Model.Output(display_name="model")],
        )

    @classmethod
    def validate_inputs(cls, unet_name) -> bool | str:
        # Checked here instead of against the list, so the file may sit in another folder.
        if not isinstance(unet_name, str):
            return True
        try:
            hyperflow.model_path(unet_name)
        except FileNotFoundError:
            base = unet_name.replace("\\", "/").rsplit("/", 1)[-1]
            return "%s is not installed. Put the file in models/diffusion_models/hyperflow, then restart ComfyUI." % base
        return True

    @classmethod
    def execute(cls, unet_name) -> io.NodeOutput:
        return io.NodeOutput(hyperflow.load(hyperflow.model_path(unet_name)))


class HyperFlowSigmas(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionHyperFlowSigmas",
            display_name="Vision HyperFlow Sigmas",
            category=CATEGORY,
            description="The step schedule of a HyperFlow checkpoint: 4 steps for speed, 6 to balance, 8 for quality.",
            inputs=[io.Model.Input("model"), io.Int.Input("steps", default=6, min=4, max=8, step=2, optional=True)],
            outputs=[io.Sigmas.Output(display_name="sigmas")],
        )

    @classmethod
    def execute(cls, model, steps=6) -> io.NodeOutput:
        return io.NodeOutput(hyperflow.sigmas(model, 6 if steps is None else steps))


class LivePreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionLivePreview",
            display_name="Vision Live Preview",
            category=CATEGORY,
            description="Show the whole clip moving, with sound, at every sampling step. Does nothing without taeh3 in models/vae_approx.",
            inputs=[
                io.Model.Input("model"),
                io.Vae.Input("audio_vae", optional=True, tooltip="H3 audio VAE. With it the preview has sound."),
                io.String.Input("folder", default="minimax_flow/preview", tooltip="Where preview clips go, inside the output folder."),
            ],
            outputs=[io.Model.Output()],
        )

    @classmethod
    def execute(cls, model, folder, audio_vae=None) -> io.NodeOutput:
        return io.NodeOutput(live_preview.attach(model, folder, audio_vae))


def _blur(mask, radius):
    """A soft edge: a separable box blur, twice (close to a Gaussian), on a [H, W] mask."""
    radius = int(radius)
    if radius < 1:
        return mask
    k = 2 * radius + 1
    m = mask[None, None]
    kernel_h = torch.ones(1, 1, 1, k, dtype=m.dtype, device=m.device) / k
    kernel_v = torch.ones(1, 1, k, 1, dtype=m.dtype, device=m.device) / k
    for _ in range(2):
        m = torch.nn.functional.conv2d(torch.nn.functional.pad(m, (radius, radius, 0, 0), mode="replicate"), kernel_h)
        m = torch.nn.functional.conv2d(torch.nn.functional.pad(m, (0, 0, radius, radius), mode="replicate"), kernel_v)
    return m[0, 0]


def _grow(mask, pixels):
    """The mask made bigger by about this many pixels (a max filter), on a [H, W] mask."""
    pixels = int(pixels)
    if pixels < 1:
        return mask
    k = 2 * pixels + 1
    return torch.nn.functional.max_pool2d(mask[None, None], k, stride=1, padding=pixels)[0, 0]


class LoadMask(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionLoadMask",
            display_name="Vision Load Mask",
            category=CATEGORY,
            description=("Read a mask the app painted (white = change) from the output folder, sized to width x height, "
                         "grown and with a soft edge."),
            inputs=[
                io.String.Input("file", tooltip="Path inside the output folder."),
                io.Int.Input("width", default=1024, min=16, max=16384),
                io.Int.Input("height", default=1024, min=16, max=16384),
                io.Int.Input("grow", default=8, min=0, max=256, tooltip="Pixels to make the mask bigger by."),
                io.Int.Input("feather", default=12, min=0, max=256, tooltip="Pixels of soft edge."),
            ],
            outputs=[io.Mask.Output(display_name="mask")],
        )

    @classmethod
    def execute(cls, file, width, height, grow, feather) -> io.NodeOutput:
        with Image.open(output_file(file)) as image:
            image = image.convert("RGBA")
            # Painted strokes: white, or anything not see-through on a clear background.
            alpha = np.array(image.getchannel("A")).astype(np.float32) / 255.0
            light = np.array(image.convert("L")).astype(np.float32) / 255.0
            mask = np.maximum(light * alpha, 0) if alpha.min() < 1 else light
            mask = Image.fromarray((mask * 255).astype(np.uint8)).resize((int(width), int(height)), Image.BILINEAR)
        m = (torch.from_numpy(np.array(mask).astype(np.float32) / 255.0) > 0.5).float()
        m = _blur(_grow(m, grow), feather).clamp(0, 1)
        return io.NodeOutput(m[None])


class CropToMask(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionCropToMask",
            display_name="Vision Crop To Mask",
            category=CATEGORY,
            description=("Cut out the part of the picture around the mask, with some room around it, scaled so its long "
                         "side is size (in steps of 16): for re-rendering a face at full detail, then Vision Paste Crop."),
            inputs=[
                io.Image.Input("image"),
                io.Mask.Input("mask"),
                io.Float.Input("padding", default=0.35, min=0.0, max=2.0, step=0.05, tooltip="Room around the mask, as a share of its size."),
                io.Int.Input("size", default=1024, min=256, max=4096, step=16),
            ],
            outputs=[io.Image.Output(display_name="crop"), io.Mask.Output(display_name="crop_mask"),
                     io.String.Output(display_name="box")],
        )

    @classmethod
    def execute(cls, image, mask, padding, size) -> io.NodeOutput:
        _, H, W, _ = image.shape
        m = mask[0] if mask.dim() == 3 else mask
        if m.shape[-2:] != (H, W):
            m = torch.nn.functional.interpolate(m[None, None], size=(H, W), mode="bilinear")[0, 0]
        ys, xs = torch.nonzero(m > 0.05, as_tuple=True)
        if len(ys) == 0:
            x0, y0, x1, y1 = 0, 0, W, H
        else:
            x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            side = max(x1 - x0, y1 - y0) * (1 + padding)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            side = min(side, W, H)
            x0 = int(round(min(max(cx - side / 2, 0), W - side))); y0 = int(round(min(max(cy - side / 2, 0), H - side)))
            x1, y1 = x0 + int(round(side)), y0 + int(round(side))
        w, h = x1 - x0, y1 - y0
        k = size / max(w, h)
        tw, th = max(16, int(round(w * k / 16)) * 16), max(16, int(round(h * k / 16)) * 16)
        crop = image[:, y0:y1, x0:x1, :].movedim(-1, 1)
        crop = torch.nn.functional.interpolate(crop, size=(th, tw), mode="bicubic", antialias=True).clamp(0, 1).movedim(1, -1)
        cm = torch.nn.functional.interpolate(m[y0:y1, x0:x1][None, None], size=(th, tw), mode="bilinear")[0]
        return io.NodeOutput(crop, cm, "%d,%d,%d,%d" % (x0, y0, w, h))


class PasteCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MMVisionPasteCrop",
            display_name="Vision Paste Crop",
            category=CATEGORY,
            description="Put a re-rendered crop from Vision Crop To Mask back into the picture, blended by its mask.",
            inputs=[io.Image.Input("image"), io.Image.Input("crop"), io.Mask.Input("crop_mask"), io.String.Input("box")],
            outputs=[io.Image.Output(display_name="image")],
        )

    @classmethod
    def execute(cls, image, crop, crop_mask, box) -> io.NodeOutput:
        x0, y0, w, h = (int(v) for v in box.split(","))
        out = image.clone()
        piece = torch.nn.functional.interpolate(crop.movedim(-1, 1), size=(h, w), mode="bicubic", antialias=True).clamp(0, 1).movedim(1, -1)
        m = crop_mask if crop_mask.dim() == 3 else crop_mask[None]
        m = torch.nn.functional.interpolate(m[:, None], size=(h, w), mode="bilinear")[:, 0, :, :, None].to(out.dtype)
        region = out[:, y0:y0 + h, x0:x0 + w, :]
        out[:, y0:y0 + h, x0:x0 + w, :] = region * (1 - m) + piece.to(out.dtype) * m
        return io.NodeOutput(out)


NODE_CLASS_MAPPINGS = {"MMVisionLoadStill": LoadStill, "MMVisionLora": Lora, "MMVisionHyperFlowLoader": HyperFlowLoader,
                       "MMVisionHyperFlowSigmas": HyperFlowSigmas, "MMVisionLivePreview": LivePreview,
                       "MMVisionLoadMask": LoadMask, "MMVisionCropToMask": CropToMask, "MMVisionPasteCrop": PasteCrop}
NODE_DISPLAY_NAME_MAPPINGS = {"MMVisionLoadStill": "Vision Load Still", "MMVisionLora": "Vision H3 LoRA",
                              "MMVisionHyperFlowLoader": "Vision HyperFlow Loader",
                              "MMVisionHyperFlowSigmas": "Vision HyperFlow Sigmas", "MMVisionLivePreview": "Vision Live Preview",
                              "MMVisionLoadMask": "Vision Load Mask", "MMVisionCropToMask": "Vision Crop To Mask",
                              "MMVisionPasteCrop": "Vision Paste Crop"}
