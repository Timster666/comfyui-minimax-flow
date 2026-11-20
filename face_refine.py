"""Refine faces: Carasibana's ComfyUI-H3-FaceRefine (https://github.com/Carasibana/ComfyUI-H3-FaceRefine) on a
finished clip, the way its example workflow does it.

Each frame's face is found and cropped so it fills a 768 canvas, H3 re-renders those crops at a low denoise with the
clip's own prompt, pictures, LoRAs and RefMods (and a close-up of the face, if one is chosen), with the clip's real
sound locked in for the lips (MiniMaxH3NativeAudioLock), and the faces are stitched back into the original frames.
The result is a new clip next to the original, with the original sound.

Everything here is optional: without the packs, Vision offers no Refine faces.
"""

import os

from . import h3_graph

# The nodes the refine needs: the FaceRefine pack's, and the audio lock that drives the lips.
NODES = ("H3FaceTrackCrop", "H3InjectVideoLatent", "H3PerFrameDenoise", "H3FaceStitch", "MiniMaxH3NativeAudioLock")
DETECTOR = "face_yolov8m.pt"   # Bingsu/adetailer, in models/ultralytics/bbox
# The base denoise. The per-frame node scales it down for big faces, so small faces get nearly all of it.
STRENGTHS = {"light": 0.3, "normal": 0.4, "strong": 0.5}
CANVAS = 768

# Graph nodes added to h3_graph's (whose loaders, LoRAs, RefMods, guider, sampler, decode and save it keeps).
VIDEO, PARTS, TRACK, INJECT, LOCK, PER_FRAME, STITCH, FACE = "60", "61", "62", "63", "64", "65", "66", "59"


def _classes():
    try:
        import nodes
        return nodes.NODE_CLASS_MAPPINGS
    except Exception:
        return {}


def detector():
    """The face detector as the tracker lists it (bbox/face_yolov8m.pt with Impact Pack), if its file is there."""
    track = _classes().get("H3FaceTrackCrop")
    if not track:
        return None
    try:
        choices = track.INPUT_TYPES()["required"]["detector"][0]
    except Exception:
        return None
    import folder_paths
    for name in choices:
        if os.path.basename(name.replace("\\", "/")) != DETECTOR:
            continue
        for key in ("ultralytics_bbox", "ultralytics"):
            try:
                if folder_paths.get_full_path(key, name):
                    return name
            except Exception:
                pass
        # The pack also looks in models/ultralytics/bbox when nothing registers that folder.
        if os.path.isfile(os.path.join(folder_paths.models_dir, "ultralytics", "bbox", os.path.basename(name))):
            return name
    return None


def why_not():
    """Why Refine faces can't run here, in words, or None."""
    have = _classes()
    if not all(n in have for n in NODES):
        return ("Refine faces needs the ComfyUI-H3-FaceRefine node pack and its audio lock. On RunPod: set "
                "ENABLE_FACEREFINE=true.")
    if not detector():
        return "Refine faces needs the face detector %s in models/ultralytics/bbox (gear menu › Models)." % DETECTOR
    return None


def models(video_models):
    """The video models a refine can use: not HyperFlow, whose fixed schedule can't re-render part of the way."""
    return [m for m in video_models if m.get("scheduler") != h3_graph.HYPERFLOW_SCHEDULE]


def has_audio(path):
    try:
        import av
        with av.open(path) as container:
            return bool(container.streams.audio)
    except Exception:
        return False


def build(clip, images, source, audio, face=None, strength="normal", name=None, files=None):
    """The graph that refines the faces of a finished clip.

    clip: the new clip (the source's prompt, pictures, LoRAs and RefMods, with the model and steps to refine with).
    source: the source video, as a path inside the output folder. audio: whether it has sound.
    face: an Add image picture of the face (a close-up works best): it joins the references and picks the face to
    follow when there are several. Without one, the largest face is refined.
    """
    graph = h3_graph.build_graph(clip, images, files=files, name=name)
    if h3_graph.is_hyperflow(graph["1"]["inputs"].get("unet_name", "")):
        raise ValueError("HyperFlow can't refine faces: pick another model.")
    conditioning = graph["10"]["inputs"]
    refs = {k: v for k, v in conditioning.items() if k.startswith("ref_images.")}
    graph["10"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": conditioning["prompt"],
        "width": [TRACK, 4], "height": [TRACK, 5], "length": [TRACK, 6], "ref_image_size": "match", **refs}}
    for gone in ("9", "17"):   # the live preview and the sound decode: the original sound is kept
        graph.pop(gone, None)

    graph[VIDEO] = {"class_type": "MMVisionLoadVideo", "inputs": {"file": source}}
    graph[PARTS] = {"class_type": "GetVideoComponents", "inputs": {"video": [VIDEO, 0]}}
    graph[TRACK] = {"class_type": "H3FaceTrackCrop", "inputs": {
        "images": [PARTS, 0], "detector": detector() or DETECTOR, "confidence": 0.35, "crop_factor": 3.0,
        "canvas_width": CANVAS, "canvas_height": CANVAS, "canvas_mode": "auto_capped_768",
        "smooth_window": 21, "size_smooth_window": 51, "smooth_method": "gaussian", "size_mode": "per_frame",
        "select": "largest_face", "identity_track": bool(face), "identity_threshold": 0.28,
        "identity_model": "insightface", "cut_detection": "none", "fallback_detector": "none"}}
    if face:
        graph[FACE] = {"class_type": "MMVisionLoadStill", "inputs": {"file": face}}
        graph[TRACK]["inputs"]["identity_reference"] = [FACE, 0]
        if len(refs) < h3_graph.H3_PICTURES:
            graph["10"]["inputs"]["ref_images.ref_image_%d" % len(refs)] = [FACE, 0]
    graph[INJECT] = {"class_type": "H3InjectVideoLatent", "inputs": {"av_latent": ["10", 1], "images": [TRACK, 0], "vae": ["3", 0]}}
    model, latent = ["5", 0], [INJECT, 0]
    if audio:
        graph[LOCK] = {"class_type": "MiniMaxH3NativeAudioLock", "inputs": {
            "model": model, "av_latent": latent, "audio_vae": ["4", 0], "audio": [PARTS, 1]}}
        model, latent = [LOCK, 0], [LOCK, 1]
    graph[PER_FRAME] = {"class_type": "H3PerFrameDenoise", "inputs": {
        "model": model, "av_latent": latent, "transform": [TRACK, 1], "denoise_multiplier_small_face": 1.0,
        "denoise_multiplier_large_face": 0.35, "scale_mode": "absolute_px", "face_px_small": 30.0, "face_px_large": 120.0,
        "gamma": 1.0, "smooth_frames": 9}}
    graph["13"]["inputs"]["model"] = [PER_FRAME, 2]
    graph["12"] = {"class_type": "BasicScheduler", "inputs": {
        "model": [PER_FRAME, 2], "scheduler": "simple", "steps": int(clip["steps"]), "denoise": STRENGTHS[strength]}}
    graph["15"]["inputs"]["latent_image"] = [PER_FRAME, 0]
    graph[STITCH] = {"class_type": "H3FaceStitch", "inputs": {
        "base_images": [PARTS, 0], "refined_crops": ["16", 0], "transform": [TRACK, 1], "paste_region": "face_only",
        "mask_dilation": 24, "feather": 24, "colour_match": 1.0, "blend": 1.0, "undetected_frames": "fade_out",
        "feather_scales_with_crop": False}}
    graph["18"] = {"class_type": "CreateVideo", "inputs": {"images": [STITCH, 0], "fps": [PARTS, 2]}}
    if audio:
        graph["18"]["inputs"]["audio"] = [PARTS, 1]
    return graph
