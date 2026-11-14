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


NODE_CLASS_MAPPINGS = {"MMVisionLoadStill": LoadStill, "MMVisionLora": Lora, "MMVisionHyperFlowLoader": HyperFlowLoader,
                       "MMVisionHyperFlowSigmas": HyperFlowSigmas, "MMVisionLivePreview": LivePreview}
NODE_DISPLAY_NAME_MAPPINGS = {"MMVisionLoadStill": "Vision Load Still", "MMVisionLora": "Vision H3 LoRA",
                              "MMVisionHyperFlowLoader": "Vision HyperFlow Loader",
                              "MMVisionHyperFlowSigmas": "Vision HyperFlow Sigmas", "MMVisionLivePreview": "Vision Live Preview"}
