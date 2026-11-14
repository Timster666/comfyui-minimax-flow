"""The Edit tab's work on video files, with PyAV (which ComfyUI itself requires).

- strip(): a clip's filmstrip (one small picture every half second, in one JPEG) and its waveform,
  for the timeline.
- export(): the edit as one MP4: each piece trimmed, fitted into the frame with black bars where
  its shape differs, and joined, picture (H.264, 24 fps) and sound (AAC, 48 kHz stereo).

No ComfyUI imports, so both can run and be tested anywhere PyAV is installed.
"""

import json
import os

import numpy as np

FPS = 24
RATE = 48000
STRIP_STEP = 0.5        # seconds between filmstrip pictures
STRIP_HEIGHT = 72
PEAKS_PER_SECOND = 25


class Cancelled(Exception):
    pass


def _video_stream(container):
    if not container.streams.video:
        raise ValueError("%s has no picture" % os.path.basename(container.name))
    return container.streams.video[0]


def duration_of(path):
    """Seconds of picture in a file."""
    import av

    with av.open(path) as container:
        stream = _video_stream(container)
        if stream.frames and stream.average_rate:
            return float(stream.frames / stream.average_rate)
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
        return float(container.duration or 0) / 1e6


def frames_at(path, times):
    """The decoded frame showing at each of these times (sorted, in seconds), as av.VideoFrame."""
    import av

    with av.open(path) as container:
        stream = _video_stream(container)
        stream.thread_type = "AUTO"
        if times and times[0] > 1.0:
            container.seek(int(max(0.0, times[0] - 1.0) / stream.time_base), stream=stream, backward=True)
        frames = container.decode(stream)
        current, upcoming = None, next(frames, None)
        for t in times:
            while upcoming is not None and (upcoming.time is None or upcoming.time <= t + 1e-4):
                current, upcoming = upcoming, next(frames, None)
            yield current if current is not None else upcoming


def sound_between(path, start, end, rate=RATE):
    """The sound of a file from start to end as float32 [2, samples], silence where it has none."""
    import av

    count = int(round((end - start) * rate))
    out = np.zeros((2, count), dtype=np.float32)
    with av.open(path) as container:
        if not container.streams.audio:
            return out
        stream = container.streams.audio[0]
        if start > 1.0:
            container.seek(int(max(0.0, start - 1.0) / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        chunks, first = [], None
        have = 0
        for frame in container.decode(stream):
            if first is None:
                first = frame.time if frame.time is not None else 0.0
            for piece in resampler.resample(frame):
                chunks.append(piece.to_ndarray())
                have += chunks[-1].shape[1]
            if first + have / rate >= end:
                break
        if not chunks:
            return out
        sound = np.concatenate(chunks, axis=1)
        offset = int(round((start - first) * rate))
        src_from, dst_from = max(0, offset), max(0, -offset)
        take = min(count - dst_from, sound.shape[1] - src_from)
        if take > 0:
            out[:, dst_from:dst_from + take] = sound[:2, src_from:src_from + take]
    return out


def fitted(frame, width, height):
    """A frame as RGB [height, width, 3], fitted inside with black bars where its shape differs."""
    fw, fh = frame.width, frame.height
    scale = min(width / fw, height / fh)
    sw, sh = max(2, int(round(fw * scale / 2)) * 2), max(2, int(round(fh * scale / 2)) * 2)
    if (sw, sh) == (width, height):
        return frame.to_ndarray(format="rgb24", width=width, height=height)
    picture = frame.to_ndarray(format="rgb24", width=min(sw, width), height=min(sh, height))
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    y, x = (height - picture.shape[0]) // 2, (width - picture.shape[1]) // 2
    canvas[y:y + picture.shape[0], x:x + picture.shape[1]] = picture
    return canvas


# ---------------------------------------------------------------- filmstrip and waveform

def strip(path, cache_dir, name):
    """A clip's filmstrip and waveform, made once and kept in cache_dir as <name>.jpg and <name>.json."""
    from PIL import Image

    info_path = os.path.join(cache_dir, name + ".json")
    stamp = os.path.getmtime(path)
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
        if info.get("source_mtime") == stamp and os.path.exists(os.path.join(cache_dir, name + ".jpg")):
            return info
    except (OSError, ValueError):
        pass
    length = duration_of(path)
    times = [min(length - 0.02, i * STRIP_STEP + 0.02) for i in range(max(1, int(np.ceil(length / STRIP_STEP))))]
    pictures = []
    for frame in frames_at(path, times):
        if frame is None:
            break
        width = max(2, int(round(STRIP_HEIGHT * frame.width / frame.height)))
        pictures.append(frame.to_image().resize((width, STRIP_HEIGHT), Image.BILINEAR))
    if not pictures:
        raise ValueError("%s has no frames" % os.path.basename(path))
    tile_w = pictures[0].width
    sheet = Image.new("RGB", (tile_w * len(pictures), STRIP_HEIGHT))
    for i, picture in enumerate(pictures):
        sheet.paste(picture.resize((tile_w, STRIP_HEIGHT)), (i * tile_w, 0))
    os.makedirs(cache_dir, exist_ok=True)
    sheet.save(os.path.join(cache_dir, name + ".jpg"), quality=78)
    sound = sound_between(path, 0.0, length, 8000)
    per = 8000 // PEAKS_PER_SECOND
    usable = sound.shape[1] // per * per
    peaks = np.abs(sound[:, :usable]).max(axis=0).reshape(-1, per).max(axis=1) if usable else np.zeros(0)
    info = {"duration": round(length, 4), "step": STRIP_STEP, "count": len(pictures), "tile": [tile_w, STRIP_HEIGHT],
            "sprite": name + ".jpg", "peaks": [round(float(p), 3) for p in peaks], "peaksPerSecond": PEAKS_PER_SECOND,
            "source_mtime": stamp}
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


# ---------------------------------------------------------------- export

def export(pieces, out_path, width, height, sound=True, progress=None, cancelled=None):
    """Join pieces [(path, start, end), ...] into one MP4 at width x height.

    progress(fraction, words) is called as it goes; cancelled() returning True stops it, and the
    unfinished file is removed.
    """
    import av

    width, height = int(width) // 2 * 2, int(height) // 2 * 2
    counts = [max(1, int(round((end - start) * FPS))) for _, start, end in pieces]
    total = sum(counts)
    done = 0
    temp = out_path + ".part"
    try:
        with av.open(temp, mode="w", format="mp4", options={"movflags": "+faststart"}) as out:
            video = out.add_stream("libx264" if "libx264" in av.codecs_available else "h264", rate=FPS)
            video.width, video.height, video.pix_fmt = width, height, "yuv420p"
            video.options = {"crf": "18", "preset": "veryfast"}
            audio = None
            if sound:
                audio = out.add_stream("aac", rate=RATE, layout="stereo")
                audio.bit_rate = 192000
            samples_written = 0
            for number, ((path, start, end), count) in enumerate(zip(pieces, counts), 1):
                if progress:
                    progress(done / total, "Clip %d of %d" % (number, len(pieces)))
                last, last_rgb = None, None
                for frame in frames_at(path, [start + k / FPS for k in range(count)]):
                    if cancelled and cancelled():
                        raise Cancelled()
                    if frame is not last:
                        last, last_rgb = frame, (fitted(frame, width, height) if frame is not None
                                                 else np.zeros((height, width, 3), dtype=np.uint8))
                    for packet in video.encode(av.VideoFrame.from_ndarray(last_rgb, format="rgb24")):
                        out.mux(packet)
                    done += 1
                    if progress and done % 12 == 0:
                        progress(done / total, "Clip %d of %d" % (number, len(pieces)))
                if audio is not None:
                    # Exactly as long as the piece's picture, so sound and picture stay together across joins.
                    pcm = sound_between(path, start, start + count / FPS)
                    for at in range(0, pcm.shape[1], 1024):
                        chunk = np.ascontiguousarray(pcm[:, at:at + 1024])
                        frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                        frame.sample_rate, frame.pts = RATE, samples_written
                        samples_written += chunk.shape[1]
                        for packet in audio.encode(frame):
                            out.mux(packet)
            if progress:
                progress(1.0, "Saving…")
            for packet in video.encode():
                out.mux(packet)
            if audio is not None:
                for packet in audio.encode():
                    out.mux(packet)
        os.replace(temp, out_path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    return out_path
