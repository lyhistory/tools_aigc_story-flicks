import os
import time
import json
import math
import uuid
import hashlib
import wave
from typing import List
from app.schemas.llm import StoryGenerationRequest
from loguru import logger
from app.models.const import StoryType, ImageStyle
from app.schemas.video import (
    VideoGenerateRequest,
    StoryScene,
    StoryboardAssembleRequest,
    InteractiveAssembleRequest,
    RegenerateImageRequest,
)
from app.services.llm import llm_service
from app.services.voice import generate_voice, sanitize_text_for_tts, split_text_for_subtitles
from app.utils import utils
from moviepy import (
    VideoFileClip,
    VideoClip,
    ImageClip,
    AudioFileClip,
    TextClip,
    CompositeVideoClip,
    concatenate_videoclips,
    ImageSequenceClip,
    afx,
)
from moviepy.video.tools import subtitles
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
import requests
import random
import shutil
from moviepy.video.tools.subtitles import SubtitlesClip, file_to_subtitles
import re
import html


# Google's unofficial free translate endpoint is frequently blocked (HTTP 429)
# on datacenter IPs, so we fall back to MyMemory's free API automatically.
_TRANSLATE_GOOGLE_BLOCKED = False
_TRANSLATE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _google_translate_line(phrase: str) -> str:
    """Translate one line via Google's free endpoint; raises on any failure."""
    global _TRANSLATE_GOOGLE_BLOCKED
    if _TRANSLATE_GOOGLE_BLOCKED:
        raise RuntimeError("Google translate endpoint unavailable (previously blocked)")
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": phrase},
            headers=_TRANSLATE_HEADERS,
            timeout=6,
        )
        if response.status_code != 200:
            response.raise_for_status()
        payload = response.json()
        translated = ""
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            translated = "".join(
                str(part[0]) for part in payload[0] if isinstance(part, list) and part and part[0] is not None
            )
        translated = html.unescape(translated).strip()
        if not translated:
            raise RuntimeError("Google translate returned an empty result")
        return translated
    except Exception as err:
        _TRANSLATE_GOOGLE_BLOCKED = True
        raise RuntimeError(f"Google translate failed: {err}") from err


def _mymemory_translate_line(phrase: str) -> str:
    """Translate one line via MyMemory's free API; raises on failure/quota."""
    response = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": phrase, "langpair": "Autodetect|zh-CN"},
        headers=_TRANSLATE_HEADERS,
        timeout=8,
    )
    if response.status_code != 200:
        response.raise_for_status()
    payload = response.json()
    translated = ((payload.get("responseData") or {}).get("translatedText") or "").strip()
    if not translated or "MYMEMORY WARNING" in translated.upper() or "QUERY LENGTH LIMIT" in translated.upper():
        raise RuntimeError("MyMemory translation unavailable")
    return html.unescape(translated)


def _translate_line_to_chinese(phrase: str) -> str:
    """Translate an English subtitle line to simplified Chinese with automatic fallback."""
    last_err = None
    for translator in (_google_translate_line, _mymemory_translate_line):
        try:
            return translator(phrase)
        except Exception as err:
            last_err = err
            logger.warning(f"Translator {translator.__name__} failed for '{phrase}': {err}")
    raise RuntimeError(f"All translators failed: {last_err}")


def wrap_text(text, max_width, font="Arial", fontsize=60):
    # Create ImageFont
    font = ImageFont.truetype(font, fontsize)

    def get_text_size(inner_text):
        inner_text = inner_text.strip()
        left, top, right, bottom = font.getbbox(inner_text)
        return right - left, bottom - top

    width, height = get_text_size(text)
    if width <= max_width:
        return text, height

    # logger.warning(f"wrapping text, max_width: {max_width}, text_width: {width}, text: {text}")

    processed = True

    _wrapped_lines_ = []
    words = text.split(" ")
    _txt_ = ""
    for word in words:
        _before = _txt_
        _txt_ += f"{word} "
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            if _txt_.strip() == word.strip():
                processed = False
                break
            _wrapped_lines_.append(_before)
            _txt_ = f"{word} "
    _wrapped_lines_.append(_txt_)
    if processed:
        _wrapped_lines_ = [line.strip() for line in _wrapped_lines_]
        result = "\n".join(_wrapped_lines_).strip()
        height = len(_wrapped_lines_) * height
        # logger.warning(f"wrapped text: {result}")
        return result, height

    _wrapped_lines_ = []
    chars = list(text)
    _txt_ = ""
    for word in chars:
        _txt_ += word
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            _wrapped_lines_.append(_txt_)
            _txt_ = ""
    _wrapped_lines_.append(_txt_)
    result = "\n".join(_wrapped_lines_).strip()
    height = len(_wrapped_lines_) * height
    # logger.warning(f"wrapped text: {result}")
    return result, height

def parse_resolution(resolution: str, fallback=(768, 1344)):
    if not resolution:
        return fallback
    value = resolution.lower().replace("x", "*")
    try:
        w_str, h_str = value.split("*")
        return int(w_str.strip()), int(h_str.strip())
    except Exception:
        return fallback

# Calm, child-friendly motion presets. Motion is applied through a small virtual
# camera instead of sliding the whole picture, so edges are never exposed and the
# movement stays slow and even regardless of how long a picture is on screen.
MOTION_CHOICES = ("none", "auto", "pan", "pan_left", "pan_right", "zoom_in", "zoom_out")
TRANSITION_CHOICES = ("cut", "fade", "dissolve", "iris", "wipe")
# Soft dark slate reads as a gentle dip rather than a harsh flash of black/white.
TRANSITION_BG_RGB = (26, 32, 44)
# Never drift more than this fraction of the visible picture, so movement stays subtle.
MAX_PAN_TRAVEL = 0.05
# A touch of zoom is what gives the camera room to drift without exposing edges.
PAN_SCALE = 1.0 + MAX_PAN_TRAVEL + 0.01


def _ease(x: float) -> float:
    """Smoothstep easing so every move starts and ends softly."""
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def _resolve_motion(motion: str | None) -> str:
    choice = (motion or "auto").strip().lower()
    if choice not in MOTION_CHOICES:
        choice = "auto"
    # "auto" is the calm default: one slow push-in, never a sideways sway.
    return "zoom_in" if choice == "auto" else choice


def _cover_resize(img: Image.Image, target_w: int, target_h: int, scale: float = 1.0) -> Image.Image:
    """Scale to cover target*scale (keeping aspect) and center-crop in one pass."""
    target_w = max(1, int(round(target_w * scale)))
    target_h = max(1, int(round(target_h * scale)))
    ratio = max(target_w / img.width, target_h / img.height)
    new_w = max(1, int(round(img.width * ratio)))
    new_h = max(1, int(round(img.height * ratio)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _display_size(img: Image.Image, out_w: int, out_h: int, framing: str):
    """Size of the picture as drawn inside the frame, before any camera move."""
    if framing == "fill":
        return out_w, out_h
    img_ratio = img.width / img.height
    out_ratio = out_w / out_h
    if img_ratio >= out_ratio:
        return out_w, max(1, int(round(out_w / img_ratio)))
    return max(1, int(round(out_h * img_ratio))), out_h


def _render_camera_frame(pre_img: Image.Image, disp_w: int, disp_h: int,
                         scale: float, pan_x: float, pan_y: float) -> np.ndarray:
    """Sample one frame of the camera move with a float affine transform.

    Sampling with fractional coefficients keeps the motion perfectly smooth;
    rounding a crop box to whole pixels is what made the picture visibly jitter.
    """
    win_w = pre_img.width / max(1.0, float(scale))
    win_h = pre_img.height / max(1.0, float(scale))
    max_dx = max(0.0, min((pre_img.width - win_w) / 2.0, win_w * MAX_PAN_TRAVEL))
    max_dy = max(0.0, min((pre_img.height - win_h) / 2.0, win_h * MAX_PAN_TRAVEL))
    cx = pre_img.width / 2.0 + max(-1.0, min(1.0, pan_x)) * max_dx
    cy = pre_img.height / 2.0 + max(-1.0, min(1.0, pan_y)) * max_dy
    matrix = (win_w / disp_w, 0.0, cx - win_w / 2.0, 0.0, win_h / disp_h, cy - win_h / 2.0)
    return np.array(pre_img.transform(
        (disp_w, disp_h), Image.AFFINE, matrix, resample=Image.Resampling.BILINEAR
    ))


def build_image_clips(image_file: str, target_w: int, target_h: int, duration: float,
                      motion: str = "auto", motion_strength: float = 0.5,
                      framing: str = "fit", motion_seed: int = 0):
    """Build the static blurred backdrop plus the gently animated foreground.

    The picture is scaled once up front, then each frame is sampled with a float
    affine transform, so slow zooms stay smooth instead of snapping between pixels.
    The foreground frame is opaque and already composited on the backdrop.
    """
    img = Image.open(image_file).convert("RGB")
    strength = max(0.0, min(1.0, float(motion_strength)))
    requested = (motion or "auto").strip().lower()
    auto_mode = requested in ("", "auto")
    resolved = _resolve_motion(requested)

    blur_radius = max(24, min(target_w, target_h) // 12)
    backdrop = img.resize((target_w, target_h), Image.Resampling.LANCZOS).filter(
        ImageFilter.GaussianBlur(radius=blur_radius)
    )
    bg_clip = ImageClip(np.array(backdrop)).with_duration(duration)
    disp_w, disp_h = _display_size(img, target_w, target_h, framing)

    def compose(region: np.ndarray) -> np.ndarray:
        if framing == "fill":
            return region
        canvas = backdrop.copy()
        canvas.paste(Image.fromarray(region), ((target_w - disp_w) // 2, (target_h - disp_h) // 2))
        return np.array(canvas)

    if resolved == "none" or duration <= 0 or strength <= 0.0:
        frame = compose(np.array(_cover_resize(img, disp_w, disp_h, 1.0)))
        return bg_clip, ImageClip(frame).with_duration(duration), target_w, target_h

    # "auto" is intentionally the calmest option: a smaller push-in, one direction only.
    zoom_amount = (0.07 if auto_mode else 0.10) * strength
    pan_scale = PAN_SCALE + 0.04 * strength
    overscan = (1.0 + zoom_amount if resolved in ("zoom_in", "zoom_out") else pan_scale) + 0.01
    pre_img = _cover_resize(img, disp_w, disp_h, overscan)

    if resolved == "pan_left":
        direction = -1.0
    elif resolved == "pan_right":
        direction = 1.0
    else:
        direction = 1.0 if random.Random(motion_seed * 104729 + 3).random() < 0.5 else -1.0

    def camera(t: float):
        progress = _ease(t / duration)
        if resolved == "zoom_in":
            return 1.0 + zoom_amount * progress, 0.0, 0.0
        if resolved == "zoom_out":
            return 1.0 + zoom_amount * (1.0 - progress), 0.0, 0.0
        return pan_scale, direction * (2.0 * progress - 1.0) * (strength * 0.85), 0.0

    def frame_fn(t: float, _pre=pre_img, _dw=disp_w, _dh=disp_h):
        scale, pan_x, pan_y = camera(t)
        return compose(_render_camera_frame(_pre, _dw, _dh, scale, pan_x, pan_y))

    fg_clip = VideoClip(frame_function=frame_fn, duration=duration)
    return bg_clip, fg_clip.with_duration(duration), target_w, target_h


def _transition_backdrop_rgb(target_w: int, target_h: int) -> np.ndarray:
    frame = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    frame[:, :] = np.array(TRANSITION_BG_RGB, dtype=np.uint8)
    return frame


def _make_transition_mask(kind: str, target_w: int, target_h: int):
    """Precompute a soft reveal mask (1.0 = incoming scene) for shaped transitions."""
    if kind == "iris":
        ys, xs = np.mgrid[0:target_h, 0:target_w]
        cx = (target_w - 1) / 2.0
        cy = (target_h - 1) / 2.0
        max_r = math.hypot(max(cx, target_w - 1 - cx), max(cy, target_h - 1 - cy))
        dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(1.0, max_r)
        soft = 0.18

        def mask(progress, _dist=dist, _soft=soft):
            edge = progress * (1.0 + 2.0 * _soft) - _soft
            return np.clip((edge - _dist) / _soft + 0.5, 0.0, 1.0)

        return mask
    if kind == "wipe":
        xs = np.linspace(0.0, 1.0, target_w, dtype=np.float32)[None, :]
        soft = 0.14

        def mask(progress, _xs=xs, _soft=soft):
            edge = progress * (1.0 + 2.0 * _soft) - _soft
            return np.clip((edge - _xs) / _soft + 0.5, 0.0, 1.0)

        return mask
    return None


def _blend_transition(prev_frame, next_frame, progress, kind, mask_fn) -> np.ndarray:
    p = _ease(progress)
    if kind in ("iris", "wipe") and mask_fn is not None:
        m = mask_fn(p)[..., None].astype(np.float32)
        blended = prev_frame.astype(np.float32) * (1.0 - m) + next_frame.astype(np.float32) * m
        return blended.astype(np.uint8)
    blended = prev_frame.astype(np.float32) * (1.0 - p) + next_frame.astype(np.float32) * p
    return blended.astype(np.uint8)


def _slot_line_index(value, line_count: int):
    """Clamp a 0-based SRT line index exactly as stored by the storyboard UI."""
    if value is None or line_count <= 0:
        return None
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(line_count - 1, idx))


def _compute_slot_windows(slots, srt_times, scene_duration: float):
    """Map ordered image slots onto one scene's timeline, in seconds.

    ``sub_start`` / ``sub_end`` are 0-based SRT line indices, matching what the
    storyboard UI sends. A slot begins at the start of its first assigned line;
    when that is missing we fall back to the end of the previous assigned range.
    Windows are always ordered, gap-free and cover the whole scene, so a picture
    never freezes on a stale frame or flashes a blank gap. When there is no
    usable subtitle timing we split the scene evenly instead.
    """
    n = len(slots)
    scene_duration = max(0.0, float(scene_duration))
    if n <= 1:
        return [(0.0, scene_duration)]

    line_count = len(srt_times or [])

    def line_start(value):
        idx = _slot_line_index(value, line_count)
        return srt_times[idx][0] if idx is not None else None

    def line_end(value):
        idx = _slot_line_index(value, line_count)
        return srt_times[idx][1] if idx is not None else None

    def even_split():
        per = scene_duration / n
        return [
            (i * per, scene_duration if i + 1 == n else (i + 1) * per)
            for i in range(n)
        ]

    has_assignments = line_count > 0 and any(
        s.get("sub_start") is not None or s.get("sub_end") is not None for s in slots
    )
    if not has_assignments:
        return even_split()

    boundaries = [0.0]
    cursor = 0.0
    for i in range(1, n):
        boundary = line_start(slots[i].get("sub_start"))
        if boundary is None:
            boundary = line_end(slots[i - 1].get("sub_end"))
        if boundary is not None:
            cursor = max(cursor, min(scene_duration, float(boundary)))
        boundaries.append(cursor)

    windows = []
    for i in range(n):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < n else scene_duration
        windows.append((start, max(start, min(scene_duration, end))))
    windows[-1] = (windows[-1][0], scene_duration)

    # Every slot must own at least a sliver of time; otherwise fall back so no
    # picture ends up hidden behind the last one.
    if any(end - start <= 1e-3 for start, end in windows):
        return even_split()

    return windows


def _parse_rgba_color(value, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Parse '#RGB', '#RRGGBB', '#RRGGBBAA', 'rgb(...)' or 'rgba(...)' to RGBA."""
    if not value:
        return default
    s = str(value).strip()
    try:
        if s.startswith("#"):
            h = s.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), default[3])
            if len(h) == 8:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
        m = re.match(r"rgba?\(([^)]+)\)", s, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            r, g, b = (int(round(float(p))) for p in parts[:3])
            a = round(float(parts[3]) * 255) if len(parts) > 3 else default[3]
            clamp = lambda v: max(0, min(255, int(v)))
            return (clamp(r), clamp(g), clamp(b), clamp(a))
    except Exception as e:
        logger.warning(f"Failed to parse color {value!r}: {e}")
    return default


COUNTDOWN_POSITION_ANCHORS = {
    "top_left": (0.07, 0.10, "left", "top"),
    "top_center": (0.50, 0.10, "center", "top"),
    "top_right": (0.93, 0.10, "right", "top"),
    "center_left": (0.07, 0.50, "left", "center"),
    "center": (0.50, 0.50, "center", "center"),
    "center_right": (0.93, 0.50, "right", "center"),
    "bottom_left": (0.07, 0.90, "left", "bottom"),
    "bottom": (0.50, 0.90, "center", "bottom"),
    "bottom_right": (0.93, 0.90, "right", "bottom"),
}


def _countdown_overlay_position(
    position: str,
    image_width: int,
    image_height: int,
    overlay_width: int,
    overlay_height: int,
) -> tuple[int, int]:
    """Return top-left pixel position for a countdown badge."""
    ax, ay, align_x, align_y = COUNTDOWN_POSITION_ANCHORS.get(
        (position or "center").strip().lower(),
        COUNTDOWN_POSITION_ANCHORS["center"],
    )
    anchor_x = int(image_width * ax)
    anchor_y = int(image_height * ay)
    if align_x == "left":
        x = anchor_x
    elif align_x == "right":
        x = anchor_x - overlay_width
    else:
        x = anchor_x - overlay_width // 2
    if align_y == "top":
        y = anchor_y
    elif align_y == "bottom":
        y = anchor_y - overlay_height
    else:
        y = anchor_y - overlay_height // 2
    return (
        max(0, min(image_width - overlay_width, x)),
        max(0, min(image_height - overlay_height, y)),
    )


SUBTITLE_POSITION_ANCHORS = {
    "top_left": (0.07, 0.10, "left", "top"),
    "top_center": (0.50, 0.10, "center", "top"),
    "top_right": (0.93, 0.10, "right", "top"),
    "center_left": (0.07, 0.50, "left", "center"),
    "center": (0.50, 0.50, "center", "center"),
    "center_right": (0.93, 0.50, "right", "center"),
    "bottom_left": (0.07, 0.90, "left", "bottom"),
    "bottom": (0.50, 0.90, "center", "bottom"),
    "bottom_right": (0.93, 0.90, "right", "bottom"),
}


def _subtitle_overlay_position(
    position: str | None,
    image_width: int,
    image_height: int,
    overlay_width: int,
    overlay_height: int,
) -> tuple[int, int]:
    """Return the top-left pixel position for a subtitle block.

    The default bottom placement keeps the historical layout; explicit
    positions use a 3x3 anchor grid so short answers can be moved.
    """
    key = (position or "bottom").strip().lower()
    if key in ("", "bottom"):
        return (
            max(0, (image_width - overlay_width) // 2),
            max(0, int(image_height * 0.95 - overlay_height - 50)),
        )
    ax, _, align_x, align_y = SUBTITLE_POSITION_ANCHORS.get(
        key,
        SUBTITLE_POSITION_ANCHORS["bottom"],
    )
    anchor_x = int(image_width * ax)
    if align_x == "left":
        x = anchor_x
    elif align_x == "right":
        x = anchor_x - overlay_width
    else:
        x = anchor_x - overlay_width // 2
    if align_y == "top":
        y = int(image_height * 0.10)
    elif align_y == "bottom":
        y = int(image_height * 0.95) - overlay_height - 50
    else:
        y = int(image_height * 0.50) - overlay_height // 2
    return (
        max(0, min(image_width - overlay_width, x)),
        max(0, min(image_height - overlay_height, y)),
    )


def _apply_highlight_regions(image_file: str, regions) -> None:
    """Dim outside and outline answer highlights directly in the scene image."""
    if not regions or not os.path.exists(image_file):
        return
    try:
        base = Image.open(image_file).convert("RGBA")
        width, height = base.size
        if width <= 0 or height <= 0:
            return
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        cleaned = []
        for region in regions:
            try:
                rx = max(0.0, min(1.0, float(getattr(region, "x", 0.0) or 0.0)))
                ry = max(0.0, min(1.0, float(getattr(region, "y", 0.0) or 0.0)))
                rw = max(0.005, min(1.0 - rx, float(getattr(region, "width", 0.0) or 0.0)))
                rh = max(0.005, min(1.0 - ry, float(getattr(region, "height", 0.0) or 0.0)))
            except (TypeError, ValueError):
                continue
            x1, y1 = int(rx * width), int(ry * height)
            x2, y2 = int((rx + rw) * width), int((ry + rh) * height)
            if x2 <= x1 or y2 <= y1:
                continue
            shape = (getattr(region, "shape", "rect") or "rect").strip().lower()
            if shape == "ellipse":
                mask_draw.ellipse([x1, y1, x2, y2], fill=255)
            else:
                mask_draw.rounded_rectangle(
                    [x1, y1, x2, y2],
                    radius=max(4, int(min(x2 - x1, y2 - y1) * 0.08)),
                    fill=255,
                )
            cleaned.append((x1, y1, x2, y2, shape, getattr(region, "color", "#FFD54F")))
        if not cleaned:
            return

        dim_alpha = Image.eval(mask, lambda value: 0 if value else 118)
        dim = Image.new("RGBA", (width, height), (8, 15, 23, 0))
        dim.putalpha(dim_alpha)
        base = Image.alpha_composite(base, dim)
        draw = ImageDraw.Draw(base)
        outline_width = max(4, int(min(width, height) * 0.007))
        for x1, y1, x2, y2, shape, color_value in cleaned:
            color = _parse_rgba_color(color_value, (255, 213, 79, 255))
            outline = (color[0], color[1], color[2], 255)
            shadow = (15, 23, 42, 220)
            if shape == "ellipse":
                draw.ellipse([x1, y1, x2, y2], outline=shadow, width=outline_width + 4)
                draw.ellipse([x1, y1, x2, y2], outline=outline, width=outline_width)
            else:
                radius = max(4, int(min(x2 - x1, y2 - y1) * 0.08))
                draw.rounded_rectangle(
                    [x1, y1, x2, y2], radius=radius,
                    outline=shadow, width=outline_width + 4,
                )
                draw.rounded_rectangle(
                    [x1, y1, x2, y2], radius=radius,
                    outline=outline, width=outline_width,
                )
        base.convert("RGB").save(image_file, format="PNG")
        logger.info(f"Applied {len(cleaned)} highlight region(s) to {image_file}")
    except Exception as err:
        logger.warning(f"Could not apply answer highlights to {image_file}: {err}")


def _write_countdown_audio(audio_file: str, duration_seconds: int, sound: str) -> None:
    """Synthesize a short countdown bed with optional tick/beep/ding cues."""
    sample_rate = 44100
    duration = max(1, int(duration_seconds or 5))
    total_samples = duration * sample_rate
    samples = np.zeros(total_samples, dtype=np.float32)
    kind = (sound or "none").strip().lower()

    def add_tone(start_s: float, tone_duration: float, frequency: float, volume: float) -> None:
        start = max(0, int(start_s * sample_rate))
        count = min(total_samples - start, max(1, int(tone_duration * sample_rate)))
        if count <= 0:
            return
        t = np.arange(count, dtype=np.float32) / sample_rate
        attack = np.clip(t / 0.012, 0.0, 1.0)
        release = np.clip((tone_duration - t) / 0.06, 0.0, 1.0)
        envelope = attack * release
        samples[start:start + count] += (
            volume * envelope * np.sin(2.0 * np.pi * frequency * t)
        ).astype(np.float32)

    if kind in ("tick", "tick_ding", "beep"):
        tick_seconds = range(duration) if kind != "tick_ding" else range(max(0, duration - 1))
        for second in tick_seconds:
            if kind == "beep":
                add_tone(float(second), 0.16, 660.0, 0.20)
            else:
                add_tone(float(second), 0.075, 980.0, 0.22)
    if kind in ("ding", "tick_ding"):
        ding_start = max(0.0, duration - 0.78)
        add_tone(ding_start, 0.68, 1320.0, 0.24)
        add_tone(ding_start, 0.68, 1980.0, 0.08)

    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(audio_file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _normalize_token(token) -> str:
    """Lowercase, strip non-letters and simple suffixes, for fuzzy word matching."""
    t = re.sub(r"[^a-zA-Z]", "", str(token or "").lower())
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) > len(suf) + 2:
            return t[: -len(suf)]
    return t


def _align_karaoke_words(phrase_tokens, words, start_ptr: int,
                         line_start: float, line_end: float,
                         look_ahead: int = 4):
    """Bind one subtitle line's tokens to the ASR word timings.

    Each token is matched against the word stream within a small look-ahead
    window using normalized text, instead of blindly consuming the next word.
    A mis-heard word (e.g. "with that" transcribed as "without") used to shift
    the highlight for every later line in the scene; matching by text stops that
    cascade, and a token with no match keeps its even-timing estimate. Returns
    ``(word_infos, next_ptr)``.
    """
    line_start = float(line_start)
    line_end = float(line_end)
    approx_step = max(0.05, line_end - line_start) / max(1, len(phrase_tokens))
    ptr = int(start_ptr)
    prev_end = line_start
    infos = []
    last = len(phrase_tokens) - 1
    for token_idx, token in enumerate(phrase_tokens):
        ws = line_start + token_idx * approx_step
        we = line_end if token_idx == last else line_start + (token_idx + 1) * approx_step
        tok_norm = _normalize_token(token)
        match_idx = None
        if tok_norm:
            for offset in range(look_ahead):
                cand = ptr + offset
                if cand >= len(words):
                    break
                if _normalize_token(words[cand].get("word", "")) == tok_norm:
                    match_idx = cand
                    break
        if match_idx is not None:
            w = words[match_idx]
            ptr = match_idx + 1
            ws = max(line_start, prev_end, float(w.get("start", ws)))
            we = min(line_end, float(w.get("end", we)))
            asr_word = str(w.get("word", ""))
        else:
            ws = max(line_start, prev_end, ws)
            asr_word = None
        if we <= ws:
            we = ws + max(0.03, approx_step * 0.8)
        prev_end = we
        infos.append({
            "token_index": token_idx,
            "word": token,
            "asr_word": asr_word,
            "start": round(ws, 4),
            "end": round(we, 4),
        })
    return infos, ptr


def _scene_voice_signature(
    scene: StoryScene,
    voice_name: str,
    voice_rate: float,
    language: str,
    voice_provider: str,
    karaoke: bool,
) -> str:
    payload = {
        "script": getattr(scene, "script", "") or "",
        "timing_version": 2,
        "voice_name": voice_name or "",
        "voice_rate": voice_rate,
        "language": language or "",
        "voice_provider": (voice_provider or "gtts").lower(),
        "karaoke": bool(karaoke),
        "is_cover": bool(getattr(scene, "is_cover", False)),
        "subject": getattr(scene, "subject", None) or "",
        "topic_type": str(getattr(scene, "topic_type", None) or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


async def generate_scene_assets(
    task_dir: str,
    scenes: List[StoryScene],
    voice_name: str,
    voice_rate: float,
    language: str,
    voice_provider: str,
    karaoke: bool,
    reuse_unchanged: bool = False,
):
    """生成每个场景所需的音频和字幕资产 (Step 1)
    """
    for __i, scene in enumerate(scenes, 1):
        i = getattr(scene, "original_index", None) or __i
        try:
            image_file = os.path.join(task_dir, f"{i}.png")
            audio_file = os.path.join(task_dir, f"{i}.mp3")
            subtitle_file = os.path.join(task_dir, f"{i}.srt")
            words_file = os.path.join(task_dir, f"{i}.words.json")
            signature_file = os.path.join(task_dir, f"{i}.voice.json")
            signature = _scene_voice_signature(
                scene, voice_name, voice_rate, language, voice_provider, karaoke
            )

            if reuse_unchanged and os.path.exists(audio_file) and os.path.exists(subtitle_file) and os.path.exists(signature_file):
                try:
                    with open(signature_file, "r", encoding="utf-8") as f:
                        cached_signature = json.load(f).get("signature")
                    if cached_signature == signature:
                        logger.info(f"Reusing unchanged voice assets for scene {i}")
                        continue
                except Exception as cache_err:
                    logger.warning(f"Could not read voice cache for scene {i}: {cache_err}")

            if reuse_unchanged and os.path.exists(words_file):
                try:
                    os.remove(words_file)
                except Exception as remove_err:
                    logger.warning(f"Could not remove stale karaoke file for scene {i}: {remove_err}")

            logger.info(f"Generating voice assets for scene {i}")
            is_cover_scene = bool(getattr(scene, "is_cover", False)) or bool((getattr(scene, "subject", None) or "").strip())
            is_sequence_scene = (getattr(scene, "topic_type", None) == "sequence") and not is_cover_scene
            lead_silence_ms = 0
            trail_silence_ms = 0
            sentence_pause_ms = None
            if is_cover_scene and (voice_provider or "gtts") == "gtts":
                lead_silence_ms = 300
                trail_silence_ms = 900
                sentence_pause_ms = 550
            
            await generate_voice(
                scene.script,
                voice_name,
                voice_rate,
                audio_file,
                subtitle_file,
                language,
                voice_provider,
                lead_silence_ms=lead_silence_ms,
                trail_silence_ms=trail_silence_ms,
                sentence_pause_ms=sentence_pause_ms,
                karaoke=(karaoke or is_sequence_scene),
                sequence_mode=is_sequence_scene,
            )
            with open(signature_file, "w", encoding="utf-8") as f:
                json.dump({"signature": signature}, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to process assets for scene {i}: {str(e)}")
            raise e


SUBTITLE_FONT_MAP = {
    # Noto Sans variants
    "NotoSans-Regular": ("Noto_Sans", "static", "NotoSans-Regular.ttf"),
    "NotoSans-Light": ("Noto_Sans", "static", "NotoSans-Light.ttf"),
    "NotoSans-SemiBold": ("Noto_Sans", "static", "NotoSans-SemiBold.ttf"),
    "NotoSans-Bold": ("Noto_Sans", "static", "NotoSans-Bold.ttf"),
    "NotoSans-ExtraBold": ("Noto_Sans", "static", "NotoSans-ExtraBold.ttf"),
    "NotoSans-Italic": ("Noto_Sans", "static", "NotoSans-Italic.ttf"),
    "NotoSans-BoldItalic": ("Noto_Sans", "static", "NotoSans-BoldItalic.ttf"),
    "NotoSans-Condensed-Bold": ("Noto_Sans", "static", "NotoSans_Condensed-Bold.ttf"),
    "NotoSans-Condensed-SemiBold": ("Noto_Sans", "static", "NotoSans_Condensed-SemiBold.ttf"),
    "NotoSans-Condensed-Regular": ("Noto_Sans", "static", "NotoSans_Condensed-Regular.ttf"),
    # Custom / decorative fonts
    "BabyPlums": ("BabyPlums-rv2gL.ttf",),
    "QuickCat": ("Quick Cat.ttf",),
    "SuperAdorable": ("SuperAdorable-MAvyp.ttf",),
    "SuperJoyful": ("SuperJoyful-lxwPq.ttf",),
    "SuperMaples": ("SuperMaples-2vR2w.ttf",),
    "SuperScribble": ("SuperScribble-pg3qr.ttf",),
    "UbuntuCondensed": ("UbuntuCondensed-Regular.ttf",),
}
DEFAULT_SUBTITLE_FONT = "NotoSans-Bold"


def resolve_subtitle_font(font_name: str | None) -> str:
    """Map friendly font name to absolute path."""
    candidate = (font_name or DEFAULT_SUBTITLE_FONT).strip()
    parts = SUBTITLE_FONT_MAP.get(candidate)
    if not parts:
        parts = SUBTITLE_FONT_MAP[DEFAULT_SUBTITLE_FONT]
    path = os.path.join(utils.resource_dir(), "fonts", *parts)
    if not os.path.exists(path):
        # fallback to hardcoded bold
        fallback = os.path.join(utils.resource_dir(), "fonts", "Noto_Sans", "static", "NotoSans-Bold.ttf")
        return fallback
    return path


async def render_final_video(
        task_dir: str,
        scenes: List[StoryScene],
        resolution: str = None,
        karaoke: bool = True,
        chinese_subtitle_enabled: bool = True,
        subtitle_font: str | None = None,
        subtitle_font_size: int | None = None,
        subtitle_color: str | None = None,
        karaoke_highlight_color: str | None = None,
        motion_effect: str | None = "auto",
        motion_strength: float = 0.5,
        framing: str | None = "fit",
        transition: str | None = "dissolve",
        transition_duration: float = 0.5) -> str:
    """组装最终的视频片段 (Step 2)
    
    Args:
        task_dir (str): 任务目录
        scenes (List[StoryScene]): 场景列表
    """
    clips = []
    scene_transitions: list[str] = []
    motion_strength = max(0.0, min(1.0, float(motion_strength or 0.0)))
    framing = (framing or "fit").strip().lower()
    if framing not in ("fit", "fill"):
        framing = "fit"
    motion_effect = (motion_effect or "auto").strip().lower()
    transition = (transition or "dissolve").strip().lower()
    transition_duration = max(0.0, float(transition_duration or 0.0))
    target_w, target_h = parse_resolution(resolution) if resolution else (None, None)
    overlay_dir = os.path.join(task_dir, "overlays")
    os.makedirs(overlay_dir, exist_ok=True)
    
    # Alpha stays high by default so the karaoke highlight reads clearly over busy
    # pictures instead of washing out; the colour picker can still lower it.
    parsed_highlight_bg_rgba = _parse_rgba_color(karaoke_highlight_color, (255, 241, 153, 235))
            
    zh_subtitle_cache: dict[str, str] = {}
    def sanitize_pronunciation(text: str) -> str:
        if not text:
            return ""
        # Remove dots and duplicated slashes that render as boxes in some fonts
        cleaned = text.replace("·", "").replace(".", "")
        cleaned = cleaned.replace("//", "/")
        return cleaned.strip()

    def clean_subtitle_text(text: str) -> str:
        if not text:
            return ""
        cleaned = (
            str(text)
            .replace("\\u200b", "")
            .replace("\\u200c", "")
            .replace("\\u200d", "")
            .replace("\\ufeff", "")
            .replace("\u200b", "")
            .replace("\u200c", "")
            .replace("\u200d", "")
        )
        cleaned = cleaned.replace("\ufeff", "")
        cleaned = cleaned.replace("“", "\"").replace("”", "\"").replace("’", "'").replace("‘", "'")
        return cleaned.strip()

    def should_translate_to_zh(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(r"[A-Za-z]", text))

    def translate_to_zh(text: str) -> str:
        phrase = clean_subtitle_text(text)
        if not phrase:
            return ""
        cached = zh_subtitle_cache.get(phrase)
        if cached is not None:
            return cached
        try:
            translated = clean_subtitle_text(_translate_line_to_chinese(phrase))
            zh_subtitle_cache[phrase] = translated
            return translated
        except Exception as e:
            logger.warning(f"Subtitle translation failed, keep English only: {e}")
            zh_subtitle_cache[phrase] = ""
            return ""

    def normalize_token(token: str) -> str:
        return _normalize_token(token)

    def tokenize_karaoke_words(text: str) -> list[str]:
        if not text:
            return []
        return [m.group(0) for m in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+", text)]

    def subtitle_stroke(fill_hex: str, font_size: int) -> tuple[str, int]:
        """Pick an outline that keeps the subtitle readable on any picture.

        Light text gets a darker outline (and vice versa) so a pastel colour
        stays crisp instead of dissolving into a bright, busy photo.
        """
        try:
            h = (fill_hex or "").lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except Exception:
            return ("#FFFFFF", max(2, int(font_size * 0.06)))
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        # 140 keeps bright/pastel picks (e.g. the pink default, luma ~147) on the
        # dark-outline side so they stay crisp instead of glowing white.
        return (("#1F2937" if luma >= 140 else "#FFFFFF"), max(2, int(font_size * 0.06)))

    def readable_text_on(rgba) -> str:
        """Dark or light text, whichever reads better on the given background."""
        try:
            r, g, b = int(rgba[0]), int(rgba[1]), int(rgba[2])
        except Exception:
            return "#111827"
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "#111827" if luma >= 140 else "#FFFFFF"

    def load_karaoke_words(words_file: str) -> tuple[list[dict], str]:
        if not os.path.exists(words_file):
            return [], "unknown"
        try:
            with open(words_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            words = data.get("words", []) if isinstance(data, dict) else []
            timing_quality = (
                str(data.get("timing_quality", "unknown")).strip().lower()
                if isinstance(data, dict)
                else "unknown"
            )
            out = []
            for w in words:
                if not isinstance(w, dict):
                    continue
                word = str(w.get("word", "")).strip()
                if not word:
                    continue
                start = float(w.get("start", 0.0) or 0.0)
                end = float(w.get("end", start + 0.03) or (start + 0.03))
                if end <= start:
                    end = start + 0.03
                out.append({"word": word, "start": start, "end": end})
            return out, timing_quality
        except Exception as e:
            logger.warning(f"Failed to load karaoke words from {words_file}: {e}")
            return [], "unknown"

    def find_keyword_in_text(text: str, keywords: list) -> dict:
        if not text or not keywords:
            return {}
        text_norm = normalize_token(text)
        for kw in keywords:
            word = (kw.get("word") or "").strip()
            if not word:
                continue
            if re.search(rf"\\b{re.escape(word)}\\b", text, flags=re.IGNORECASE):
                return kw
            w_norm = normalize_token(word)
            if w_norm and w_norm in text_norm:
                return kw
        return {}

    def render_text_png(
        text: str,
        font_path: str,
        font_size: int,
        color: str,
        max_width: int,
        file_path: str,
        bg_rgba=(0, 0, 0, 0),
        highlight_word: str | None = None,
        highlight_bg_rgba=None,
    ) -> tuple[int, int]:
        highlight_bg_rgba = highlight_bg_rgba or parsed_highlight_bg_rgba
        if not text:
            return 0, 0
        wrapped_txt, _ = wrap_text(text, max_width=max_width, font=font_path, fontsize=font_size)
        lines = wrapped_txt.split("\n")
        font = ImageFont.truetype(font_path, font_size)
        line_sizes = []
        for line in lines:
            bbox = font.getbbox(line)
            w = max(1, bbox[2] - bbox[0])
            h = max(1, bbox[3] - bbox[1])
            line_sizes.append((line, bbox, w, h))
        pad_x = max(8, int(font_size * 0.35))
        pad_y = max(6, int(font_size * 0.3))
        width = max(w for _, _, w, _ in line_sizes) + pad_x * 2
        line_spacing = max(4, int(font_size * 0.15))
        height = sum(h for _, _, _, h in line_sizes) + line_spacing * (len(line_sizes) - 1) + pad_y * 2
        img = Image.new("RGBA", (width, height), bg_rgba)
        draw = ImageDraw.Draw(img)
        highlight = (highlight_word or "").strip()
        y = pad_y
        for line, bbox, w, h in line_sizes:
            x = (width - w) // 2 - bbox[0]
            if highlight:
                match = re.search(rf"\\b{re.escape(highlight)}\\b", line, flags=re.IGNORECASE)
                if match:
                    pre = line[: match.start()]
                    mid = line[match.start() : match.end()]
                    pre_w = font.getlength(pre) if pre else 0
                    mid_w = font.getlength(mid) if mid else 0
                    hi_pad_x = max(4, int(font_size * 0.12))
                    hi_pad_y = max(2, int(font_size * 0.12))
                    draw.rectangle(
                        [x + pre_w - hi_pad_x, y - hi_pad_y, x + pre_w + mid_w + hi_pad_x, y + h + hi_pad_y],
                        fill=highlight_bg_rgba,
                    )
            draw.text((x, y - bbox[1]), line, font=font, fill=color)
            y += h + line_spacing
        img.save(file_path, format="PNG")
        return width, height

    def render_tag_png(text: str, font_path: str, font_size: int, text_color: str, file_path: str, bg_rgba=(255, 255, 0, 160)) -> tuple[int, int]:
        if not text:
            return 0, 0
        font = ImageFont.truetype(font_path, font_size)
        bbox = font.getbbox(text)
        text_w = max(1, bbox[2] - bbox[0])
        text_h = max(1, bbox[3] - bbox[1])
        pad_x = max(6, int(font_size * 0.3))
        pad_y = max(4, int(font_size * 0.2))
        img = Image.new("RGBA", (text_w + pad_x * 2, text_h + pad_y * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, img.size[0], img.size[1]], fill=bg_rgba)
        draw.text((pad_x - bbox[0], pad_y - bbox[1]), text, font=font, fill=text_color)
        img.save(file_path, format="PNG")
        return img.size

    def render_text_rgba(
        text: str,
        font_path: str,
        font_size: int,
        color: str,
        max_width: int,
        bg_rgba=(0, 0, 0, 0),
        highlight_word: str | None = None,
        highlight_bg_rgba=None,
        highlight_text_color: str | None = None,
        highlight_token_index: int | None = None,
        stroke_width: int = 0,
        stroke_fill: str | None = None,
    ) -> Image.Image | None:
        highlight_bg_rgba = highlight_bg_rgba or parsed_highlight_bg_rgba
        if not text:
            return None
        wrapped_txt, _ = wrap_text(text, max_width=max_width, font=font_path, fontsize=font_size)
        lines = wrapped_txt.split("\n")
        font = ImageFont.truetype(font_path, font_size)
        line_sizes = []
        for line in lines:
            bbox = font.getbbox(line)
            w = max(1, bbox[2] - bbox[0])
            h = max(1, bbox[3] - bbox[1])
            line_sizes.append((line, bbox, w, h))
        pad_x = max(8, int(font_size * 0.35))
        pad_y = max(6, int(font_size * 0.3))
        width = max(w for _, _, w, _ in line_sizes) + pad_x * 2
        line_spacing = max(4, int(font_size * 0.15))
        height = sum(h for _, _, _, h in line_sizes) + line_spacing * (len(line_sizes) - 1) + pad_y * 2
        img = Image.new("RGBA", (width, height), bg_rgba)
        draw = ImageDraw.Draw(img)
        highlight = (highlight_word or "").strip()
        highlight_norm = normalize_token(highlight) if highlight else ""
        y = pad_y
        token_cursor = 0
        token_highlight_done = False
        for line, bbox, w, h in line_sizes:
            x = (width - w) // 2 - bbox[0]
            match = None
            line_tokens = list(re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+", line))
            if highlight_token_index is not None and not token_highlight_done:
                for m in line_tokens:
                    if token_cursor == highlight_token_index:
                        match = m
                        token_highlight_done = True
                    token_cursor += 1
            elif highlight_token_index is not None:
                token_cursor += len(line_tokens)
            elif highlight_norm:
                for m in line_tokens:
                    token = m.group(0)
                    if normalize_token(token) == highlight_norm:
                        match = m
                        break
            if match:
                pre = line[: match.start()]
                mid = line[match.start() : match.end()]
                pre_w = font.getlength(pre) if pre else 0
                mid_w = font.getlength(mid) if mid else 0
                hi_pad_x = max(4, int(font_size * 0.12))
                hi_pad_y = max(2, int(font_size * 0.12))
                draw.rectangle(
                    [x + pre_w - hi_pad_x, y - hi_pad_y, x + pre_w + mid_w + hi_pad_x, y + h + hi_pad_y],
                    fill=highlight_bg_rgba,
                )
            draw.text(
                (x, y - bbox[1]),
                line,
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )
            if highlight and match and highlight_text_color:
                draw.text(
                    (x + pre_w, y - bbox[1]),
                    mid,
                    font=font,
                    fill=highlight_text_color,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
            y += h + line_spacing
        return img

    def stack_subtitle_images(primary_img: Image.Image | None, secondary_img: Image.Image | None, gap: int = 8) -> Image.Image | None:
        if primary_img is None:
            return secondary_img
        if secondary_img is None:
            return primary_img
        width = max(primary_img.width, secondary_img.width)
        height = primary_img.height + max(0, gap) + secondary_img.height
        stacked = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        stacked.alpha_composite(primary_img, ((width - primary_img.width) // 2, 0))
        stacked.alpha_composite(secondary_img, ((width - secondary_img.width) // 2, primary_img.height + max(0, gap)))
        return stacked

    def build_keyword_panel(
        word: str,
        pron_us: str,
        pron_uk: str,
        expl: str,
        word_font_path: str,
        pron_font_path: str,
        expl_font_path: str,
        max_width: int,
        origin_image_h: int,
    ) -> Image.Image | None:
        if not word:
            return None
        word_img = render_text_rgba(
            word,
            word_font_path,
            max(34, int(origin_image_h * 0.045)),
            "#1E2A36",
            max_width=max_width,
            bg_rgba=(0, 0, 0, 0),
        )
        pron_us_line = f"US /{pron_us}/" if pron_us else "US /.../"
        pron_uk_line = f"UK /{pron_uk}/" if pron_uk else "UK /.../"
        pron_us_img = render_text_rgba(
            pron_us_line,
            pron_font_path,
            max(24, int(origin_image_h * 0.03)),
            "#3B556D",
            max_width=max_width,
            bg_rgba=(0, 0, 0, 0),
        )
        pron_uk_img = render_text_rgba(
            pron_uk_line,
            pron_font_path,
            max(24, int(origin_image_h * 0.03)),
            "#3B556D",
            max_width=max_width,
            bg_rgba=(0, 0, 0, 0),
        )
        body_img = None
        if expl:
            body_img = render_text_rgba(
                expl,
                expl_font_path,
                max(22, int(origin_image_h * 0.028)),
                "#2B3A45",
                max_width=max_width,
                bg_rgba=(0, 0, 0, 0),
            )
        parts = [img for img in [word_img, pron_us_img, pron_uk_img, body_img] if img]
        if not parts:
            return None
        widths = [p.width for p in parts]
        heights = [p.height for p in parts]
        gap = 6
        panel_w = max(widths)
        panel_h = sum(heights) + gap * (len(parts) - 1)
        panel_bg = Image.new("RGBA", (panel_w + 24, panel_h + 16), (255, 255, 255, 170))
        y = 8
        for idx, part in enumerate(parts):
            x = (panel_w - part.width) // 2 + 12
            panel_bg.alpha_composite(part, (x, y))
            y += part.height + gap
        return panel_bg

    def overlay_frame_with_subs(frame: np.ndarray, t: float, items: list[dict]) -> np.ndarray:
        if not items:
            return frame
        base = Image.fromarray(frame).convert("RGBA")
        for item in items:
            if item["start"] <= t <= item["end"]:
                if item.get("cover_img") is not None:
                    base.alpha_composite(item["cover_img"], item["cover_pos"])
                if item.get("kw_img") is not None:
                    base.alpha_composite(item["kw_img"], item["kw_pos"])
                sub_img = item.get("sub_img")
                karaoke_words = item.get("karaoke_words") or []
                karaoke_imgs = item.get("sub_karaoke_imgs") or {}
                if karaoke_words and karaoke_imgs:
                    active_idx = None
                    for w in karaoke_words:
                        if float(w["start"]) <= t <= float(w["end"]):
                            active_idx = int(w["token_index"])
                            break
                    if active_idx is not None and active_idx in karaoke_imgs:
                        sub_img = karaoke_imgs[active_idx]
                if sub_img is not None:
                    base.alpha_composite(sub_img, item["sub_pos"])
        return np.array(base.convert("RGB"))

    def imageclip_from_png(file_path: str) -> ImageClip:
        try:
            img = Image.open(file_path).convert("RGBA")
            arr = np.array(img)
            rgb = arr[:, :, :3]
            alpha = arr[:, :, 3] / 255.0
            clip = ImageClip(rgb)
            if alpha.ndim == 2:
                clip = clip.with_mask(ImageClip(alpha, is_mask=True))
            return clip
        except Exception as e:
            logger.warning(f"Failed to load PNG with alpha for overlay: {file_path} ({e})")
            return ImageClip(file_path)

    shown_keyword_words = set()
    for __i, scene in enumerate(scenes, 1):
        i = getattr(scene, "original_index", None) or __i
        scene_motion = (getattr(scene, "motion_effect", None) or motion_effect).strip().lower()
        scene_transition = (getattr(scene, "transition", None) or transition).strip().lower()
        scene_subtitle_font_size = getattr(scene, "subtitle_font_size", None)
        scene_subtitle_position = getattr(scene, "subtitle_position", None)
        scene_transitions.append(scene_transition)
        try:
            # 获取文件路径
            image_file = os.path.join(task_dir, f"{i}.png")
            is_countdown = (getattr(scene, "render_mode", "normal") or "normal").strip().lower() == "countdown"
            audio_file = os.path.join(task_dir, f"{i}.wav" if is_countdown else f"{i}.mp3")
            subtitle_file = os.path.join(task_dir, f"{i}.srt")

            required_files_exist = (
                os.path.exists(image_file)
                and os.path.exists(audio_file)
                and (is_countdown or os.path.exists(subtitle_file))
            )
            if not required_files_exist:
                logger.warning(f"Required files not found for scene {i}, skipping scene video render or expecting error.")
                # We do not raise error immediately, let it break normally if it fails to load files.
            # 创建音频剪辑。Countdown scenes use a synthesized WAV bed and no subtitles.
            audio_clip = AudioFileClip(audio_file)
            if is_countdown:
                subs = []
                subtitle_duration = float(audio_clip.duration or getattr(scene, "countdown_seconds", 5) or 5)
            else:
                # 获取字幕的总时长
                subs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
                subtitle_duration = max([tb for ((ta, tb), txt) in subs]) if subs else 0.0
                if audio_clip.duration and audio_clip.duration > subtitle_duration:
                    subtitle_duration = audio_clip.duration
            raw_slots = list(getattr(scene, "image_slots", None) or [])
            tasks_root = os.path.dirname(task_dir)

            def _resolve_url_to_local(url: str) -> str | None:
                """Try to map a frontend URL back to a local filesystem path."""
                if not url:
                    return None
                
                # Strip query params like ?t=...
                clean_url = url.split("?")[0]
                
                # Already a local path
                if os.path.exists(clean_url):
                    return clean_url
                # URL pattern: /tasks/<task_id>/<filename>
                rel = clean_url.split("/tasks/", 1)[-1] if "/tasks/" in clean_url else None
                if rel:
                    candidate = os.path.join(tasks_root, rel.replace("/", os.sep))
                    if os.path.exists(candidate):
                        return candidate
                return None

            if raw_slots:
                # Structured slots with optional subtitle assignments
                resolved_slots: list[dict] = []
                for slot in raw_slots:
                    local_path = _resolve_url_to_local(str(slot.url))
                    if local_path:
                        resolved_slots.append({
                            "path": local_path,
                            "sub_start": slot.sub_start,
                            "sub_end": slot.sub_end,
                        })
                    else:
                        logger.warning(f"Scene {i}: image_slot URL not found locally: {slot.url}")
            else:
                # Fall back to old extra_images list (even split, no subtitle assignment)
                extra_urls = list(getattr(scene, "extra_images", None) or [])
                resolved_slots = []
                # Only fallback to original image_file if scene.url was actually populated.
                # If scene.url is strictly deleted/empty, user removed all slots manually.
                if getattr(scene, "url", None):
                    scene_url = _resolve_url_to_local(str(scene.url))
                    resolved_slots.append({"path": scene_url or image_file, "sub_start": None, "sub_end": None})
                    for ex_idx, ex_url in enumerate(extra_urls):
                        local = _resolve_url_to_local(str(ex_url))
                        if local:
                            resolved_slots.append({"path": local, "sub_start": None, "sub_end": None})
                        else:
                            logger.warning(f"Scene {i} extra_image[{ex_idx}] not found: {ex_url}")

            n_images = len(resolved_slots)

            # --- Map each image slot onto the scene timeline using SRT timing ---
            # subs is already loaded above; build a list of (start_s, end_s) per SRT line
            srt_times = [(float(ta), float(tb)) for (ta, tb), _ in subs]  # 0-indexed
            image_time_windows = _compute_slot_windows(resolved_slots, srt_times, subtitle_duration)

            all_image_files = [s["path"] for s in resolved_slots]
            logger.info(f"Scene {i} resolved image files: {all_image_files}")
            logger.info(f"Scene {i} time windows: {image_time_windows}")

            # Build a base ImageClip for each image
            if target_w is None or target_h is None:
                # fallback dims
                target_w, target_h = (1080, 1920)
                if all_image_files:
                    try:
                        base_img = Image.open(all_image_files[0])
                        target_w, target_h = base_img.size
                        base_img.close()
                    except Exception:
                        pass

            # Build (bg_clip, fg_clip) pairs; duration is window length for each
            image_clips: list[tuple] = []
            
            if not all_image_files:
                # Empty slot case: push a black clip so video doesn't crash assembling
                dur = max(0.1, subtitle_duration)
                black_clip = ImageClip(np.zeros((target_h, target_w, 3), dtype=np.uint8)).with_duration(dur)
                image_clips.append((black_clip, black_clip))
                origin_image_w, origin_image_h = target_w, target_h
            else:
                for img_idx, (img_file, (win_start, win_end)) in enumerate(
                    zip(all_image_files, image_time_windows)
                ):
                    dur = max(0.1, win_end - win_start)
                    _bg, _fg, origin_image_w, origin_image_h = build_image_clips(
                        image_file=img_file,
                        target_w=target_w,
                        target_h=target_h,
                        duration=dur,
                        motion=scene_motion,
                        motion_strength=motion_strength,
                        framing=framing,
                        motion_seed=i * 131 + img_idx,
                    )
                    image_clips.append((_bg, _fg))

            def _fallback_scene_clip():
                """Whole-scene, narration-length clip used if subtitle rendering fails.

                Rebuilds the first picture across the full scene duration so the
                audio timeline always stays aligned with the visuals on errors.
                """
                dur = max(0.1, subtitle_duration)
                if all_image_files:
                    fb_bg, fb_fg, fb_w, fb_h = build_image_clips(
                        image_file=all_image_files[0],
                        target_w=target_w,
                        target_h=target_h,
                        duration=dur,
                        motion=scene_motion,
                        motion_strength=motion_strength,
                        framing=framing,
                        motion_seed=i * 131,
                    )
                    return CompositeVideoClip([fb_bg, fb_fg], (fb_w, fb_h)).with_duration(dur).with_fps(24)
                return ImageClip(
                    np.zeros((target_h, target_w, 3), dtype=np.uint8)
                ).with_duration(dur).with_fps(24)

            # audio will be attached to the final composite clip
            # Fonts
            font_path = os.path.join(utils.resource_dir(), "fonts", "STHeitiLight.ttc")
            noto_static_dir = os.path.join(utils.resource_dir(), "fonts", "Noto_Sans", "static")
            noto_regular = os.path.join(noto_static_dir, "NotoSans-Regular.ttf")
            noto_semibold = os.path.join(noto_static_dir, "NotoSans-SemiBold.ttf")
            noto_light = os.path.join(noto_static_dir, "NotoSans-Light.ttf")
            chinese_subtitle_font_path = os.path.join(utils.resource_dir(), "fonts", "MicrosoftYaHeiNormal.ttc")
            if not os.path.exists(font_path):
                raise FileNotFoundError("Font file not found: " + font_path)
            # Use the user-selected subtitle font, fall back to NotoSans-Bold
            subtitle_font_path = resolve_subtitle_font(subtitle_font)
            keyword_font_path = noto_semibold if os.path.exists(noto_semibold) else subtitle_font_path
            if not os.path.exists(chinese_subtitle_font_path):
                chinese_subtitle_font_path = subtitle_font_path
            keyword_pron_font_path = noto_regular if os.path.exists(noto_regular) else keyword_font_path
            keyword_expl_font_path = noto_light if os.path.exists(noto_light) else keyword_font_path
            logger.info(f"Subtitle font: {subtitle_font_path}")
            # 添加字幕 (PIL render directly onto frames)
            if is_countdown:
                countdown_bg, countdown_fg = image_clips[0]
                countdown_scene_clip = CompositeVideoClip(
                    [countdown_bg, countdown_fg],
                    (origin_image_w, origin_image_h),
                ).with_duration(subtitle_duration).with_fps(24)

                def build_countdown_badge(number_text: str, label_text: str) -> Image.Image | None:
                    base_size = max(68, int(min(origin_image_w, origin_image_h) * 0.18))
                    number_img = render_text_rgba(
                        number_text,
                        subtitle_font_path,
                        base_size,
                        "#0F172A",
                        max_width=max(120, int(origin_image_w * 0.28)),
                        bg_rgba=(0, 0, 0, 0),
                        stroke_width=max(1, int(base_size * 0.025)),
                        stroke_fill="#FFFFFF",
                    )
                    if number_img is None:
                        return None
                    padding = max(16, int(base_size * 0.26))
                    circle_size = max(number_img.width, number_img.height) + padding * 2
                    badge = Image.new("RGBA", (circle_size, circle_size), (0, 0, 0, 0))
                    badge_draw = ImageDraw.Draw(badge)
                    border_width = max(5, int(circle_size * 0.035))
                    badge_draw.ellipse(
                        [0, 0, circle_size - 1, circle_size - 1],
                        fill=(255, 255, 255, 236),
                        outline=(245, 158, 11, 255),
                        width=border_width,
                    )
                    badge.alpha_composite(
                        number_img,
                        (
                            (circle_size - number_img.width) // 2,
                            (circle_size - number_img.height) // 2,
                        ),
                    )
                    if not label_text:
                        return badge
                    label_font_path = (
                        chinese_subtitle_font_path
                        if re.search(r"[\u4e00-\u9fff]", label_text)
                        else subtitle_font_path
                    )
                    label_img = render_text_rgba(
                        label_text,
                        label_font_path,
                        max(28, int(base_size * 0.30)),
                        "#FFFFFF",
                        max_width=max(160, int(origin_image_w * 0.72)),
                        bg_rgba=(15, 23, 42, 225),
                        stroke_width=max(1, int(base_size * 0.018)),
                        stroke_fill="#111827",
                    )
                    if label_img is None:
                        return badge
                    gap = max(8, int(base_size * 0.10))
                    width = max(badge.width, label_img.width)
                    height = label_img.height + gap + badge.height
                    combined = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                    combined.alpha_composite(label_img, ((width - label_img.width) // 2, 0))
                    combined.alpha_composite(
                        badge,
                        ((width - badge.width) // 2, label_img.height + gap),
                    )
                    return combined

                subtitle_items = []
                countdown_seconds = max(1, min(15, int(getattr(scene, "countdown_seconds", 5) or 5)))
                countdown_label = str(getattr(scene, "countdown_label", "") or "").strip()
                countdown_position = str(getattr(scene, "countdown_position", "center") or "center")
                for step in range(countdown_seconds):
                    badge_img = build_countdown_badge(str(countdown_seconds - step), countdown_label)
                    if badge_img is None:
                        continue
                    badge_x, badge_y = _countdown_overlay_position(
                        countdown_position,
                        origin_image_w,
                        origin_image_h,
                        badge_img.width,
                        badge_img.height,
                    )
                    subtitle_items.append(
                        {
                            "start": float(step),
                            "end": float(step + 1),
                            "sub_img": badge_img,
                            "sub_pos": (badge_x, badge_y),
                            "kw_img": None,
                            "kw_pos": (0, 0),
                        }
                    )

                def make_countdown_frame(t, _scene=countdown_scene_clip, _items=subtitle_items):
                    return overlay_frame_with_subs(_scene.get_frame(t), t, _items)

                video_clip = VideoClip(
                    frame_function=make_countdown_frame,
                    duration=subtitle_duration,
                ).with_fps(24)
                clips.append(video_clip.with_audio(audio_clip))
                logger.info(
                    f"Added countdown scene {i}: {countdown_seconds}s at {countdown_position} "
                    f"(sound={getattr(scene, 'countdown_sound', 'none')})"
                )
            elif os.path.exists(subtitle_file):
                logger.info(f"Loading subtitle file: {subtitle_file}")
                try:
                    sub = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
                    subtitle_items = []
                    karaoke_enabled = bool(karaoke)
                    is_cover_scene = bool(getattr(scene, "is_cover", False)) or bool((getattr(scene, "subject", None) or "").strip())
                    is_sequence_scene = (getattr(scene, "topic_type", None) == "sequence") and not is_cover_scene
                    sequence_timing_enabled = is_sequence_scene
                    words_file = os.path.join(task_dir, f"{i}.words.json")
                    karaoke_words_all: list[dict] = []
                    zh_translations = []
                    if getattr(scene, "chinese_translation", None):
                        zh_translations = [t.strip() for t in scene.chinese_translation.split("\n")]
                    karaoke_timing_quality = "unknown"
                    if karaoke_enabled or sequence_timing_enabled:
                        karaoke_words_all, karaoke_timing_quality = load_karaoke_words(words_file)
                    if karaoke_enabled and not is_sequence_scene and karaoke_timing_quality != "precise":
                        logger.info(
                            f"Scene {i}: disabling word-by-word karaoke highlight "
                            f"because timing quality is '{karaoke_timing_quality}'"
                        )
                        karaoke_enabled = False
                    karaoke_word_ptr = 0
                    cover_text = (getattr(scene, "subject", None) or "").strip()
                    if not cover_text and is_cover_scene:
                        script_text = clean_subtitle_text(getattr(scene, "script", "") or "")
                        m = re.search(r"learn\s+(.+?)[\.\!\?]?$", script_text, flags=re.IGNORECASE)
                        if m:
                            cover_text = m.group(1).strip()
                        elif script_text:
                            cover_text = script_text
                    if is_cover_scene and cover_text:
                        cover_font_size = max(72, int(origin_image_h * 0.08))
                        cover_img = render_text_rgba(
                            cover_text,
                            subtitle_font_path,
                            cover_font_size,
                            "#0F172A",
                            max_width=int(origin_image_w * 0.85),
                            bg_rgba=(255, 255, 255, 210),
                            stroke_width=max(2, int(cover_font_size * 0.06)),
                            stroke_fill="#FFFFFF",
                        )
                        if cover_img is not None:
                            cover_x = (origin_image_w - cover_img.width) // 2
                            cover_y = (origin_image_h - cover_img.height) // 2
                            subtitle_items.append(
                                {
                                    "start": 0.0,
                                    "end": subtitle_duration,
                                    "cover_img": cover_img,
                                    "cover_pos": (cover_x, cover_y),
                                    "sub_img": None,
                                    "sub_pos": (0, 0),
                                    "kw_img": None,
                                    "kw_pos": (0, 0),
                                }
                            )
                            logger.info(f"Cover overlay added for scene {i}: '{cover_text}' size={cover_img.width}x{cover_img.height}")
                        else:
                            logger.warning(f"Cover overlay render returned None for scene {i}")
                    # Cover scene: keep it clean and stable by showing subject title only.
                    render_cover_only = bool(is_cover_scene and cover_text)
                    if is_sequence_scene and karaoke_words_all:
                        lead_display_s = 0.28
                        for w in karaoke_words_all:
                            token = clean_subtitle_text(str(w.get("word", "")).strip())
                            if not token:
                                continue
                            ws = float(w.get("start", 0.0) or 0.0)
                            we = float(w.get("end", ws + 0.08) or (ws + 0.08))
                            start_t = max(0.0, ws - lead_display_s)
                            end_t = max(start_t + 0.08, we)
                            sub_img = render_text_rgba(
                                token,
                                subtitle_font_path,
                                max(176, int(origin_image_h * 0.2)),
                                "#2563EB",
                                max_width=int(origin_image_w * 0.9),
                                bg_rgba=(0, 0, 0, 0),
                                stroke_width=max(3, int(origin_image_h * 0.008)),
                                stroke_fill="#FFFFFF",
                            )
                            if sub_img is None:
                                continue
                            sub_x = (origin_image_w - sub_img.width) // 2
                            sub_y = (origin_image_h - sub_img.height) // 2
                            subtitle_items.append(
                                {
                                    "start": start_t,
                                    "end": end_t,
                                    "sub_img": sub_img,
                                    "sub_pos": (sub_x, sub_y),
                                    "kw_img": None,
                                    "kw_pos": (0, 0),
                                    "karaoke_words": [],
                                    "sub_karaoke_imgs": {},
                                }
                            )
                    if not render_cover_only and not (is_sequence_scene and karaoke_words_all):
                        for item_idx, item in enumerate(sub, 1):
                            phrase = clean_subtitle_text(item[1])
                            if not phrase:
                                continue
                            kw = {} if is_sequence_scene else find_keyword_in_text(phrase, scene.keywords)
                            highlight_word = (kw.get("word") or "").strip() if kw else ""
                            highlight_norm = normalize_token(highlight_word) if highlight_word else ""
                            if is_sequence_scene:
                                sub_font_size = 148
                                sub_color = "#1F2937"
                                sub_stroke_w = max(3, int(origin_image_h * 0.008))
                                sub_stroke_fill = "#FFFFFF"
                            else:
                                sub_font_size = int(
                                    scene_subtitle_font_size or subtitle_font_size or 58
                                )
                                sub_font_size = max(16, min(200, sub_font_size))
                                sub_color = subtitle_color or "#F472B6"
                                # A matching outline keeps any chosen colour crisp
                                # on busy art instead of looking washed out.
                                sub_stroke_fill, sub_stroke_w = subtitle_stroke(sub_color, sub_font_size)
                            sub_img_en = render_text_rgba(
                                phrase,
                                subtitle_font_path,
                                sub_font_size,
                                sub_color,
                                max_width=int(origin_image_w * 0.9),
                                bg_rgba=(0, 0, 0, 0),
                                highlight_word=highlight_word,
                                highlight_bg_rgba=(254, 240, 138, 220),
                                highlight_text_color="#111827",
                                stroke_width=sub_stroke_w,
                                stroke_fill=sub_stroke_fill,
                            )
                            zh_sub_img = None
                            if (
                                chinese_subtitle_enabled
                                and not is_sequence_scene
                                and should_translate_to_zh(phrase)
                            ):
                                zh_phrase = zh_translations[item_idx - 1] if (item_idx - 1) < len(zh_translations) and zh_translations[item_idx - 1] else translate_to_zh(phrase)
                                if zh_phrase:
                                    zh_font_size = (
                                        max(18, int(sub_font_size * 0.42))
                                        if scene_subtitle_font_size
                                        else max(20, int(origin_image_h * 0.024))
                                    )
                                    zh_sub_img = render_text_rgba(
                                        zh_phrase,
                                        chinese_subtitle_font_path,
                                        zh_font_size,
                                        "#F8FAFC",
                                        max_width=int(origin_image_w * 0.85),
                                        bg_rgba=(0, 0, 0, 0),
                                        stroke_width=max(1, int(origin_image_h * 0.002)),
                                        stroke_fill="#111827",
                                    )
                            sub_img = stack_subtitle_images(
                                sub_img_en,
                                zh_sub_img,
                                gap=max(6, int(origin_image_h * 0.006)),
                            )
                            line_start = float(item[0][0])
                            line_end = float(item[0][1])
                            phrase_tokens = tokenize_karaoke_words(phrase)
                            karaoke_words_line = []
                            if karaoke_enabled and phrase_tokens:
                                karaoke_words_line, karaoke_word_ptr = _align_karaoke_words(
                                    phrase_tokens,
                                    karaoke_words_all,
                                    karaoke_word_ptr,
                                    line_start,
                                    line_end,
                                )
                            sub_karaoke_imgs = {}
                            if karaoke_enabled and karaoke_words_line and not is_sequence_scene:
                                for word_info in karaoke_words_line:
                                    token_idx = int(word_info["token_index"])
                                    kara_bg = parsed_highlight_bg_rgba
                                    kara_img_en = render_text_rgba(
                                        phrase,
                                        subtitle_font_path,
                                        sub_font_size,
                                        sub_color,
                                        max_width=int(origin_image_w * 0.9),
                                        bg_rgba=(0, 0, 0, 0),
                                        highlight_token_index=token_idx,
                                        highlight_bg_rgba=kara_bg,
                                        highlight_text_color=readable_text_on(kara_bg),
                                        stroke_width=sub_stroke_w,
                                        stroke_fill=sub_stroke_fill,
                                    )
                                    kara_img = stack_subtitle_images(
                                        kara_img_en,
                                        zh_sub_img,
                                        gap=max(6, int(origin_image_h * 0.006)),
                                    )
                                    if kara_img is not None:
                                        sub_karaoke_imgs[token_idx] = kara_img
                            # A very large per-scene size can make a single long
                            # word wider than the frame. Scale the rendered block
                            # down proportionally instead of clipping its edge.
                            if sub_img is not None and sub_img.width > origin_image_w:
                                scale = origin_image_w / float(sub_img.width)
                                sub_img = sub_img.resize(
                                    (
                                        origin_image_w,
                                        max(1, int(sub_img.height * scale)),
                                    ),
                                    Image.LANCZOS,
                                )
                                for token_idx, kara_img in list(sub_karaoke_imgs.items()):
                                    if kara_img is None:
                                        continue
                                    sub_karaoke_imgs[token_idx] = kara_img.resize(
                                        (
                                            max(1, int(kara_img.width * scale)),
                                            max(1, int(kara_img.height * scale)),
                                        ),
                                        Image.LANCZOS,
                                    )
                            kw_img = None
                            if kw:
                                word = (kw.get("word") or "").strip()
                                word_key = normalize_token(word)
                                if word_key and word_key not in shown_keyword_words:
                                    pron_us = sanitize_pronunciation(kw.get("pronunciation_us", ""))
                                    pron_uk = sanitize_pronunciation(kw.get("pronunciation_uk", ""))
                                    expl = (kw.get("explanation") or "").strip()
                                    kw_img = build_keyword_panel(
                                        word=word,
                                        pron_us=pron_us,
                                        pron_uk=pron_uk,
                                        expl=expl,
                                        word_font_path=keyword_font_path,
                                        pron_font_path=keyword_pron_font_path,
                                        expl_font_path=keyword_expl_font_path,
                                        max_width=int(origin_image_w * 0.9),
                                        origin_image_h=origin_image_h,
                                    )
                                    shown_keyword_words.add(word_key)
                            if sub_img is None and kw_img is None:
                                continue
                            sub_w = sub_img.width if sub_img else 0
                            sub_h = sub_img.height if sub_img else 0
                            if is_sequence_scene:
                                sub_x = (origin_image_w - sub_w) // 2
                                sub_y = (origin_image_h - sub_h) // 2
                            else:
                                sub_x, sub_y = _subtitle_overlay_position(
                                    scene_subtitle_position,
                                    origin_image_w,
                                    origin_image_h,
                                    sub_w,
                                    sub_h,
                                )
                            kw_x = (origin_image_w - kw_img.width) // 2 if kw_img else 0
                            kw_y = 20
                            subtitle_items.append(
                                {
                                    "start": (max(0.0, float(item[0][0]) - 0.25) if is_sequence_scene else item[0][0]),
                                    "end": item[0][1],
                                    "sub_img": sub_img,
                                    "sub_pos": (sub_x, sub_y),
                                    "kw_img": kw_img,
                                    "kw_pos": (kw_x, kw_y),
                                    "karaoke_words": karaoke_words_line,
                                    "sub_karaoke_imgs": sub_karaoke_imgs,
                                }
                            )
                            if kw_img is not None:
                                logger.info(
                                    f"Keyword overlay added: {(kw.get('word') or '').strip()} for subtitle '{phrase}'"
                                )
                    # Build base composites for each image
                    base_clips = [
                        CompositeVideoClip([bg, fg], (origin_image_w, origin_image_h))
                        for bg, fg in image_clips
                    ]

                    fps = 24
                    _windows = image_time_windows  # captured in the closure
                    _base_clips = base_clips

                    # Slot-to-slot transitions inside a scene. These are visual
                    # only: the narration audio and subtitle timeline are never
                    # touched, so a dissolve between pictures cannot desync the
                    # voice-over from the captions. A "fade" dip is a scene
                    # boundary effect, so slots cross-dissolve instead of flashing.
                    slot_kind = (scene_transition or "dissolve").strip().lower()
                    if slot_kind not in TRANSITION_CHOICES:
                        slot_kind = "dissolve"
                    if slot_kind == "fade":
                        slot_kind = "dissolve"
                    slot_boundaries = [_windows[k][1] for k in range(len(_windows) - 1)]
                    slot_td = 0.0
                    if slot_kind != "cut" and slot_boundaries and len(_base_clips) > 1:
                        min_win = min(max(1e-3, we - ws) for ws, we in _windows)
                        slot_td = min(transition_duration, 0.5, 0.6 * min_win)
                    slot_mask = (
                        _make_transition_mask(slot_kind, target_w, target_h)
                        if slot_kind in ("iris", "wipe") else None
                    )

                    def scene_image_frame(t, _windows=_windows, _base_clips=_base_clips,
                                          _bounds=slot_boundaries, _td=slot_td,
                                          _kind=slot_kind, _mask=slot_mask, _fps=fps):
                        def _slot_local(idx, tt):
                            win_start, win_end = _windows[idx]
                            win_dur = max(0.0, win_end - win_start)
                            local_t = max(0.0, min(tt - win_start, max(0.0, win_dur - 1.0 / _fps)))
                            return _base_clips[idx].get_frame(local_t)

                        idx = 0
                        for k, (ws, _we) in enumerate(_windows):
                            if t + 1e-9 >= ws:
                                idx = k
                            else:
                                break
                        if _td > 0:
                            half = _td / 2.0
                            for k, boundary in enumerate(_bounds):
                                if k + 1 >= len(_base_clips):
                                    break
                                if abs(t - boundary) <= half:
                                    progress = (t - (boundary - half)) / _td
                                    return _blend_transition(
                                        _slot_local(k, t),
                                        _slot_local(k + 1, t),
                                        progress,
                                        _kind,
                                        _mask,
                                    )
                        return _slot_local(idx, t)

                    if subtitle_items:
                        logger.info(
                            f"Subtitle items rendered for scene {i}: {len(subtitle_items)} "
                            f"| images: {n_images} | slot_transition={slot_kind}({slot_td:.2f}s)"
                        )

                        def make_frame(t, _scene=scene_image_frame, _items=subtitle_items):
                            return overlay_frame_with_subs(_scene(t), t, _items)
                    else:
                        logger.warning(f"No subtitle items rendered for scene {i}")

                        def make_frame(t, _scene=scene_image_frame):
                            return _scene(t)

                    video_clip = VideoClip(
                        frame_function=make_frame,
                        duration=subtitle_duration,
                    ).with_fps(fps)
                    clips.append(video_clip.with_audio(audio_clip))
                    logger.info(f"Added subtitles for scene {i}")
                except Exception as e:
                    logger.exception(f"Failed to add subtitles for scene {i}: {str(e)}")
                    clips.append(_fallback_scene_clip().with_audio(audio_clip))
            else:
                logger.warning(f"Subtitle file not found: {subtitle_file}")
                clips.append(_fallback_scene_clip().with_audio(audio_clip))
        except Exception as e:
            logger.exception(f"Failed to process scene {i}: {str(e)}")
            raise e
    
    if not clips:
        raise ValueError("No valid clips to combine")

    fps = 24
    durations = [max(0.05, float(c.duration or 0.0)) for c in clips]
    starts: list[float] = []
    cursor = 0.0
    for dur in durations:
        starts.append(cursor)
        cursor += dur
    total_duration = cursor

    # Narration keeps its original timeline: transitions are rendered on the visual
    # side only, so soft dissolves can never desync the voice over.
    carrier = concatenate_videoclips(clips, method="compose")
    carried_audio = getattr(carrier, "audio", None)

    kinds: list[str] = []
    for idx in range(len(clips)):
        override = scene_transitions[idx] if idx < len(scene_transitions) else transition
        kind = (override or transition or "dissolve").strip().lower()
        kinds.append(kind if kind in TRANSITION_CHOICES else "dissolve")
    if len(clips) < 2:
        kinds = ["cut"] * len(clips)

    td = 0.0
    if len(clips) > 1 and any(k != "cut" for k in kinds):
        td = min(transition_duration, 0.9 * min(durations))

    mask_cache: dict[str, object] = {}

    def get_mask(kind: str):
        if kind not in mask_cache:
            mask_cache[kind] = _make_transition_mask(kind, target_w, target_h)
        return mask_cache[kind]

    backdrop_rgb = _transition_backdrop_rgb(target_w, target_h)

    def scene_frame(idx: int, local_t: float):
        local_t = max(0.0, min(local_t, durations[idx] - 1.0 / fps))
        return clips[idx].get_frame(local_t)

    def final_frame(t: float):
        idx = 0
        for k in range(len(starts)):
            if t + 1e-6 >= starts[k]:
                idx = k
            else:
                break
        local = t - starts[idx]

        # Incoming transition (its shape is decided by the incoming scene).
        if td > 0 and idx > 0 and local < td:
            kind = kinds[idx]
            next_frame = scene_frame(idx, local)
            if kind == "fade":
                return _blend_transition(backdrop_rgb, next_frame, local / td, "dissolve", None)
            if kind in ("dissolve", "iris", "wipe"):
                prev_frame = scene_frame(idx - 1, durations[idx - 1])
                return _blend_transition(prev_frame, next_frame, local / td, kind, get_mask(kind))

        # Outgoing dip-to-colour (decided by the next scene's transition setting).
        if (td > 0 and idx < len(clips) - 1 and local > durations[idx] - td
                and kinds[idx + 1] == "fade"):
            cur = scene_frame(idx, local)
            progress = (local - (durations[idx] - td)) / td
            return _blend_transition(cur, backdrop_rgb, progress, "dissolve", None)

        return scene_frame(idx, local)

    video_file = os.path.join(task_dir, "video.mp4")
    logger.info(
        f"Merging all clips (motion={motion_effect}, framing={framing}, "
        f"transitions={sorted(set(kinds))}, transition_duration={td:.2f}s)"
    )
    try:
        final_clip = VideoClip(frame_function=final_frame, duration=total_duration).with_fps(fps)
        if carried_audio is not None:
            final_clip = final_clip.with_audio(carried_audio)
        logger.info(f"Writing video to {video_file}")
        final_clip.write_videofile(video_file, fps=fps, codec='libx264', audio_codec='aac')
    except Exception as e:
        logger.error(f"Transition-aware render failed ({e}); falling back to plain concatenation")
        final_clip = concatenate_videoclips(clips, method="compose")
        final_clip.write_videofile(video_file, fps=fps, codec='libx264', audio_codec='aac')

    return video_file


async def generate_video(request: VideoGenerateRequest):
    """生成视频

    Args:
        request (VideoGenerateRequest): 视频生成请求
    """
    try:
        # 测试模式下，从 story.json 中读取请求参数
        if request.test_mode:
            task_id = request.task_id or str(int(time.time()))
            task_dir = utils.task_dir(task_id)
            if not os.path.exists(task_dir):
                raise ValueError(f"Task directory not found: {task_dir}")
            # 从 story.json 中读取数据
            story_file = os.path.join(task_dir, "story.json")
            if not os.path.exists(story_file):
                raise ValueError(f"Story file not found: {story_file}")
            
            with open(story_file, "r", encoding="utf-8") as f:
                story_data = json.load(f)
                print("story_data", story_data)
            
            request = VideoGenerateRequest(**story_data)
            request.test_mode = True
            scenes = []
            for scene in story_data.get("scenes", []):
                scenes.append(
                    StoryScene(
                        script=scene.get("script", scene.get("text", "")),
                        scene_prompt=scene.get("scene_prompt", scene.get("image_prompt", "")),
                        objects=scene.get("objects", []),
                        keywords=scene.get("keywords", []),
                        url=scene.get("url"),
                        is_cover=scene.get("is_cover", False),
                        subject=scene.get("subject"),
                        topic_type=scene.get("topic_type"),
                    )
                )
        else:
            task_id = str(int(time.time()))
            task_dir = utils.task_dir(task_id)
            os.makedirs(task_dir, exist_ok=True)
            req = StoryGenerationRequest(
                resolution=request.resolution,
                story_prompt=request.story_prompt,
                language=request.language,
                segments=request.segments,
                text_llm_provider=request.text_llm_provider,
                text_llm_model=request.text_llm_model,
                image_llm_provider=request.image_llm_provider,
                image_llm_model=request.image_llm_model,
                use_inpainting=request.use_inpainting,
                avoid_exact_counts=request.avoid_exact_counts,
                topic_type=request.topic_type,
                subject=request.subject,
                learner_age=request.learner_age,
                use_provided_content=request.use_provided_content,
            )
            logger.info(f"generate_video StoryGenerationRequest: {req}")
            story_list = await llm_service.generate_story_with_images(
                request=req,
                task_id=task_id,
                task_dir=task_dir)
            
            for i, scene in enumerate(story_list, 1):
                image_url = scene.get("url")
                logger.info(f"Scene {i} - Generated image URL: {image_url}")
                
                if image_url:
                    image_path = os.path.join(task_dir, f"{i}.png")
                    logger.info(f"  → URL starts with: {image_url[:100]}...")
                    # Check if it's a local file path (starts with / or C:\ or relative)
                    if image_url.startswith('/') or image_url.startswith('\\') or image_url.startswith('./') or image_url.startswith('..'):
                        # Local file — check if it exists
                        if os.path.exists(image_url):
                            logger.info(f"  → Local file exists: {image_url}")
                        else:
                            logger.warning(f"  → Local file NOT found: {image_url}")
                    else:
                        # Remote URL — try download
                        try:
                            response = requests.get(image_url, timeout=20)
                            logger.info(f"  → Download test status: {response.status_code}")
                            if response.status_code != 200:
                                logger.warning(f"  → Image may be invalid/expired (status {response.status_code})")
                        except Exception as e:
                            logger.error(f"  → Immediate download test failed: {str(e)}")
                else:
                    logger.warning(f"Scene {i} has no image URL!")
        
            scenes = [
                StoryScene(
                    script=scene.get("script", scene.get("text", "")),
                    scene_prompt=scene.get("scene_prompt", scene.get("image_prompt", "")),
                    objects=scene.get("objects", []),
                    keywords=scene.get("keywords", []),
                    url=scene.get("url"),
                    is_cover=scene.get("is_cover", False),
                    subject=scene.get("subject"),
                    topic_type=scene.get("topic_type"),
                    original_index=idx,
                )
                for idx, scene in enumerate(story_list, 1)
            ]
            
            # 保存 story.json
            story_data = request.model_dump()
            story_data["scenes"] = [scene.model_dump() for scene in scenes]
            
            story_file = os.path.join(task_dir, "story.json")
            for i, scene in enumerate(story_list, 1):
                if scene.get("url"):
                    image_path = os.path.join(task_dir, f"{i}.png")
                    # Check if it's already a local path
                    if scene["url"].startswith('/') or scene["url"].startswith('\\') or os.path.isabs(scene["url"]):
                        # Local path — just copy/rename to expected name
                        if os.path.exists(scene["url"]):
                            logger.info(f"  → Local file exists: {scene['url']}")
                            # If path is different from expected, copy it
                            if scene["url"] != image_path:
                                try:
                                    shutil.copy2(scene["url"], image_path)
                                    logger.info(f"  → Copied local image to expected path: {image_path}")
                                except Exception as copy_err:
                                    logger.error(f"  → Copy failed: {copy_err}")
                        else:
                            logger.warning(f"  → Local file NOT found: {scene['url']}")
                    else:
                        # Remote URL — download as before
                        try:
                            response = requests.get(scene["url"])
                            if response.status_code == 200:
                                with open(image_path, "wb") as f:
                                    f.write(response.content)
                                logger.info(f"Downloaded image {i} to {image_path}")
                        except Exception as e:
                            logger.error(f"Failed to download image {i}: {e}")
                else:
                    logger.warning(f"No image URL for scene {i} — skipping")
            with open(story_file, "w", encoding="utf-8") as f:
                json.dump(story_data, f, ensure_ascii=False, indent=2)
        # return ""
        # If test_mode is False, we generate voice for everything sequentially
        if not request.test_mode:
            await generate_scene_assets(
                task_dir=task_dir,
                scenes=scenes,
                voice_name=request.voice_name,
                voice_rate=request.voice_rate,
                language=request.language,
                voice_provider=getattr(request, "voice_provider", "gtts"),
                karaoke=getattr(request, "karaoke", True),
            )

        # 生成视频
        return await render_final_video(
            task_dir=task_dir,
            scenes=scenes,
            karaoke=getattr(request, "karaoke", True),
            chinese_subtitle_enabled=getattr(request, "chinese_subtitle_enabled", True),
            resolution=request.resolution,
        )
    except Exception as e:
        logger.error(f"Failed to generate video: {e}")
        raise e


async def generate_storyboard_impl(request: VideoGenerateRequest) -> dict:
    """Storyboard Step 1: LLM text, image generation, voice and subtitles, then return data"""
    try:
        task_id = request.task_id or str(int(time.time()))
        task_dir = utils.task_dir(task_id)
        os.makedirs(task_dir, exist_ok=True)
        
        req = StoryGenerationRequest(
            resolution=request.resolution,
            story_prompt=request.story_prompt,
            language=request.language,
            segments=request.segments,
            text_llm_provider=request.text_llm_provider,
            text_llm_model=request.text_llm_model,
            image_llm_provider=request.image_llm_provider,
            image_llm_model=request.image_llm_model,
            use_inpainting=request.use_inpainting,
            avoid_exact_counts=request.avoid_exact_counts,
            topic_type=request.topic_type,
            subject=request.subject,
            learner_age=request.learner_age,
            use_provided_content=request.use_provided_content,
        )
        logger.info(f"generate_storyboard_impl StoryGenerationRequest: {req}")
        story_list = await llm_service.generate_story_with_images(
            request=req,
            task_id=task_id,
            task_dir=task_dir)
        
        scenes = [
            StoryScene(
                script=scene.get("script", scene.get("text", "")),
                scene_prompt=scene.get("scene_prompt", scene.get("image_prompt", "")),
                objects=scene.get("objects", []),
                keywords=scene.get("keywords", []),
                url=scene.get("url"),
                is_cover=scene.get("is_cover", False),
                subject=scene.get("subject"),
                topic_type=scene.get("topic_type"),
                original_index=idx,
            )
            for idx, scene in enumerate(story_list, 1)
        ]

        story_data = request.model_dump()
        story_data["task_id"] = task_id
        story_data["scenes"] = [scene.model_dump() for scene in scenes]
        
        story_file = os.path.join(task_dir, "story.json")
        for i, scene in enumerate(story_list, 1):
            if scene.get("url"):
                image_path = os.path.join(task_dir, f"{i}.png")
                # Handle URLs or local paths just like regular mode
                if scene["url"].startswith('/') or scene["url"].startswith('\\') or os.path.isabs(scene["url"]):
                    if os.path.exists(scene["url"]):
                        if scene["url"] != image_path:
                            try:
                                shutil.copy2(scene["url"], image_path)
                            except Exception as copy_err:
                                logger.error(f"Copy failed: {copy_err}")
                else:
                    try:
                        response = requests.get(scene["url"])
                        if response.status_code == 200:
                            with open(image_path, "wb") as f:
                                f.write(response.content)
                    except Exception as e:
                        logger.error(f"Failed to download image {i}: {e}")
        
        with open(story_file, "w", encoding="utf-8") as f:
            json.dump(story_data, f, ensure_ascii=False, indent=2)

        # Map local image paths to accessible URLs for frontend
        for i, scene in enumerate(scenes, 1):
            scene.url = f"http://127.0.0.1:8889/tasks/{task_id}/{i}.png"

        # Generate audio assets
        await generate_scene_assets(
            task_dir=task_dir,
            scenes=scenes,
            voice_name=request.voice_name,
            voice_rate=request.voice_rate,
            language=request.language,
            voice_provider=getattr(request, "voice_provider", "gtts"),
            karaoke=getattr(request, "karaoke", True),
        )

        def _clean_sub(text: str) -> str:
            return str(text).replace("\\u200b", "").replace("\u200b", "").replace("\ufeff", "").strip()

        if getattr(request, "chinese_subtitle_enabled", True):
            from moviepy.video.tools import subtitles
            for i, scene in enumerate(scenes, 1):
                subtitle_file = os.path.join(task_dir, f"{i}.srt")
                if os.path.exists(subtitle_file):
                    try:
                        subs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
                        translated_lines = []
                        for item in subs:
                            phrase = _clean_sub(item[1])
                            if bool(re.search(r"[A-Za-z]", phrase)):
                                try:
                                    translated_lines.append(_translate_line_to_chinese(phrase))
                                except Exception as err:
                                    logger.warning(f"Translate failed for '{phrase}': {err}")
                                    translated_lines.append("")
                            else:
                                translated_lines.append("")
                        scene.chinese_translation = "\n".join(translated_lines)
                    except Exception as err:
                        logger.error(f"Failed to parse or translate SRT for scene {i}: {err}")

        return {"task_id": task_id, "scenes": [scene.model_dump() for scene in scenes]}
    except Exception as e:
        logger.error(f"Failed to generate storyboard: {e}")
        raise e

async def retranslate_script_impl(request: "RetranslateScriptRequest") -> str:
    from app.services.llm import LLMService
    llm_service = LLMService()
    prompt = f"""You are an expert proofreader for children's educational content. 
I have a machine-translated Chinese script, provided line-by-line.
Rewrite it to be kid-friendly, natural, and simple Chinese (Simplified). 
For example, change inappropriate literal translations like '喝酒' (drink alcohol) to '喝水/喝饮料' (drink water/juice) when the context is for kids.

IMPORTANT RULES:
1. Return EXACTLY the same number of lines as the input.
2. Rewrite line-by-line. DO NOT merge or split lines.
3. If an input line is empty, return an empty line.

Original Machine-Translated Chinese Script:
{request.script}
"""
    try:
        response = await llm_service.generate_text(
            prompt=prompt,
            text_llm_provider=request.text_llm_provider,
            text_llm_model=request.text_llm_model,
            system_prompt="You are a helpful assistant that accurately translates texts while strictly preserving the line structure."
        )
        return response.strip()
    except Exception as e:
        logger.error(f"LLM translation failed: {e}")
        raise e

def translate_scenes_to_chinese(scripts: List[str]) -> List[str]:
    """Translate each scene into one Chinese line per subtitle line.

    The split mirrors the subtitle pipeline (sanitize whitespace, then split on
    sentence punctuation) so every line lines up with the SRT renderer.
    """
    cache: dict[str, str] = {}
    translations: List[str] = []
    for script in scripts:
        clean_text = sanitize_text_for_tts(script or "")
        lines = split_text_for_subtitles(clean_text)
        translated_lines: List[str] = []
        for line in lines:
            phrase = line.strip()
            if not phrase or not re.search(r"[A-Za-z]", phrase):
                translated_lines.append("")
                continue
            if phrase not in cache:
                try:
                    translated = _translate_line_to_chinese(phrase)
                    cache[phrase] = re.sub(r"\s*\n+\s*", " ", translated or "").strip()
                except Exception as err:
                    logger.warning(f"Translate failed for '{phrase}': {err}")
                    cache[phrase] = ""
            translated_lines.append(cache[phrase])
        translations.append("\n".join(translated_lines))
    return translations


async def assemble_video_impl(request: "StoryboardAssembleRequest") -> str:
    """Storyboard Step 2: Assemble Video from edited scenes list"""
    try:
        task_id = request.task_id
        task_dir = utils.task_dir(task_id)
        if not os.path.exists(task_dir):
            raise ValueError(f"Task directory not found: {task_dir}. Assets must be generated first.")
            
        story_file = os.path.join(task_dir, "story.json")
        story_data = {}
        if os.path.exists(story_file):
            with open(story_file, "r", encoding="utf-8") as f:
                story_data = json.load(f)
        story_data["task_id"] = task_id
        story_data["scenes"] = [scene.model_dump() for scene in request.scenes]
        if request.generate_audio:
            story_data["mode"] = "free"
        with open(story_file, "w", encoding="utf-8") as f:
            json.dump(story_data, f, ensure_ascii=False, indent=2)

        # Pre-process edited scenes: make every image URL resolvable from this task dir.
        def localize_image(url: str, stem: str) -> str:
            if not url:
                return url
            url_path = url.split("?")[0]
            target_img = os.path.join(task_dir, f"{stem}.png")
            local_url = f"http://127.0.0.1:8889/tasks/{task_id}/{stem}.png"
            if "/tasks/" in url_path:
                rel_path = url_path.split("/tasks/")[-1]
                tasks_root = os.path.dirname(task_dir)
                source_img = os.path.join(tasks_root, rel_path.replace("/", os.sep))
                if os.path.exists(source_img):
                    if os.path.abspath(source_img) != os.path.abspath(target_img):
                        shutil.copy2(source_img, target_img)
                        logger.info(f"Reused image: copied {source_img} to {target_img}")
                    return local_url
            elif url_path.startswith("http"):
                try:
                    response = requests.get(url_path, timeout=10)
                    if response.status_code == 200:
                        with open(target_img, "wb") as f:
                            f.write(response.content)
                        logger.info(f"Reused image: downloaded external {url_path} to {target_img}")
                        return local_url
                    logger.error(f"Failed to download {url_path}, status: {response.status_code}")
                except Exception as e:
                    logger.error(f"Failed to download external image {url_path}: {e}")
            elif os.path.exists(url_path):
                if os.path.abspath(url_path) != os.path.abspath(target_img):
                    shutil.copy2(url_path, target_img)
                return local_url
            return url

        for i, scene in enumerate(request.scenes, 1):
            original_index = getattr(scene, "original_index", None) or i
            if scene.url:
                scene.url = localize_image(scene.url, str(original_index))
            for slot_idx, slot in enumerate(getattr(scene, "image_slots", None) or [], 1):
                if slot.url:
                    slot.url = localize_image(slot.url, f"{original_index}_slot{slot_idx}")

        if request.generate_audio:
            for i, scene in enumerate(request.scenes, 1):
                if not (getattr(scene, "script", "") or "").strip():
                    raise ValueError(f"Scene {i} has no transcript. Add a transcript before generating the video.")
                has_image = bool(getattr(scene, "url", None)) or any(
                    bool(getattr(slot, "url", None))
                    for slot in (getattr(scene, "image_slots", None) or [])
                )
                if not has_image:
                    raise ValueError(f"Scene {i} has no image. Upload or select an image before generating the video.")

            logger.info(f"Free mode: preparing voice and subtitle assets for {len(request.scenes)} scenes")
            await generate_scene_assets(
                task_dir=task_dir,
                scenes=request.scenes,
                voice_name=request.voice_name or "default",
                voice_rate=request.voice_rate,
                language=request.language or "fixed-en-GB",
                voice_provider=request.voice_provider or "gtts",
                karaoke=request.karaoke,
                reuse_unchanged=True,
            )


        return await render_final_video(
            task_dir=task_dir,
            scenes=request.scenes,
            karaoke=request.karaoke,
            chinese_subtitle_enabled=request.chinese_subtitle_enabled,
            resolution=request.resolution,
            subtitle_font=request.subtitle_font,
            subtitle_font_size=request.subtitle_font_size,
            subtitle_color=request.subtitle_color,
            karaoke_highlight_color=request.karaoke_highlight_color,
            motion_effect=request.motion_effect,
            motion_strength=request.motion_strength,
            framing=request.framing,
            transition=request.transition,
            transition_duration=request.transition_duration,
        )
    except Exception as e:
        logger.error(f"Failed to assemble video: {e}")
        raise e


async def assemble_interactive_impl(request: "InteractiveAssembleRequest") -> str:
    """Expand interactive rounds into question/countdown/answer scenes and render them."""
    try:
        task_id = request.task_id
        task_dir = utils.task_dir(task_id)
        if not os.path.exists(task_dir):
            raise ValueError(f"Task directory not found: {task_dir}.")
        if not request.rounds:
            raise ValueError("Add at least one interactive round before generating the video.")

        def localize_image(url: str, stem: str) -> str:
            if not url:
                return url
            url_path = url.split("?")[0]
            target_img = os.path.join(task_dir, f"{stem}.png")
            local_url = f"http://127.0.0.1:8889/tasks/{task_id}/{stem}.png"
            if "/tasks/" in url_path:
                rel_path = url_path.split("/tasks/")[-1]
                tasks_root = os.path.dirname(task_dir)
                source_img = os.path.join(tasks_root, rel_path.replace("/", os.sep))
                if os.path.exists(source_img):
                    if os.path.abspath(source_img) != os.path.abspath(target_img):
                        shutil.copy2(source_img, target_img)
                    return local_url
            elif url_path.startswith("http"):
                try:
                    response = requests.get(url_path, timeout=10)
                    if response.status_code == 200:
                        with open(target_img, "wb") as f:
                            f.write(response.content)
                        return local_url
                    logger.error(f"Failed to download interactive image {url_path}: HTTP {response.status_code}")
                except Exception as err:
                    logger.error(f"Failed to download interactive image {url_path}: {err}")
            elif os.path.exists(url_path):
                if os.path.abspath(url_path) != os.path.abspath(target_img):
                    shutil.copy2(url_path, target_img)
                return local_url
            return url

        scenes: List[StoryScene] = []
        next_index = 1

        def append_bookend(bookend, label: str) -> None:
            nonlocal next_index
            image_url = (bookend.image_url or "").strip()
            script = (bookend.script or "").strip()
            if not image_url:
                raise ValueError(f"{label}: image is missing.")
            if not script:
                raise ValueError(f"{label}: narration is empty.")
            localized_url = localize_image(image_url, str(next_index))
            image_file = os.path.join(task_dir, f"{next_index}.png")
            if not os.path.exists(image_file):
                raise ValueError(f"{label}: image could not be loaded.")
            scenes.append(
                StoryScene(
                    script=script,
                    scene_prompt="",
                    objects=[],
                    keywords=[],
                    url=localized_url,
                    original_index=next_index,
                    chinese_translation=(bookend.translation or "").strip() or None,
                    motion_effect="none",
                    transition="cut",
                    render_mode="normal",
                )
            )
            next_index += 1

        if request.intro is not None:
            append_bookend(request.intro, "Intro scene")

        for round_index, round_item in enumerate(request.rounds, 1):
            question_script = (round_item.question_script or "").strip()
            answer_script = (round_item.answer_script or "").strip()
            if not question_script:
                raise ValueError(f"Round {round_index}: question narration is empty.")
            if not answer_script:
                raise ValueError(f"Round {round_index}: answer narration is empty.")
            if not (round_item.question_image_url or "").strip():
                raise ValueError(f"Round {round_index}: question image is missing.")

            question_idx = next_index
            countdown_idx = next_index + 1
            answer_idx = next_index + 2
            question_url = localize_image(round_item.question_image_url, str(question_idx))
            question_file = os.path.join(task_dir, f"{question_idx}.png")
            if not os.path.exists(question_file):
                raise ValueError(f"Round {round_index}: question image could not be loaded.")

            countdown_file = os.path.join(task_dir, f"{countdown_idx}.png")
            shutil.copy2(question_file, countdown_file)
            countdown_url = f"http://127.0.0.1:8889/tasks/{task_id}/{countdown_idx}.png"

            answer_source_url = round_item.answer_image_url or question_url
            answer_url = localize_image(answer_source_url, str(answer_idx))
            answer_file = os.path.join(task_dir, f"{answer_idx}.png")
            if not os.path.exists(answer_file):
                raise ValueError(f"Round {round_index}: answer image could not be loaded.")
            if round_item.highlights:
                _apply_highlight_regions(answer_file, round_item.highlights)

            scenes.append(
                StoryScene(
                    script=question_script,
                    scene_prompt="",
                    objects=[],
                    keywords=[],
                    url=question_url,
                    original_index=question_idx,
                    chinese_translation=(round_item.question_translation or "").strip() or None,
                    motion_effect="none",
                    transition="cut",
                    render_mode="normal",
                )
            )
            scenes.append(
                StoryScene(
                    script="",
                    scene_prompt="",
                    objects=[],
                    keywords=[],
                    url=countdown_url,
                    original_index=countdown_idx,
                    chinese_translation=None,
                    motion_effect="none",
                    transition="cut",
                    render_mode="countdown",
                    countdown_seconds=round_item.countdown_seconds,
                    countdown_position=round_item.countdown_position,
                    countdown_sound=round_item.countdown_sound,
                    countdown_label=(round_item.countdown_label or "").strip(),
                )
            )
            scenes.append(
                StoryScene(
                    script=answer_script,
                    scene_prompt="",
                    objects=[],
                    keywords=[],
                    url=answer_url,
                    original_index=answer_idx,
                    chinese_translation=(round_item.answer_translation or "").strip() or None,
                    motion_effect="none",
                    transition="cut",
                    render_mode="answer",
                    highlight_regions=list(round_item.highlights),
                    subtitle_font_size=round_item.answer_subtitle_font_size,
                    subtitle_position=round_item.answer_subtitle_position,
                )
            )
            next_index = answer_idx + 1

        if request.outro is not None:
            append_bookend(request.outro, "Outro scene")

        story_file = os.path.join(task_dir, "story.json")
        story_data = {
            "task_id": task_id,
            "mode": "interactive",
            "intro": request.intro.model_dump() if request.intro else None,
            "outro": request.outro.model_dump() if request.outro else None,
            "rounds": [round_item.model_dump() for round_item in request.rounds],
            "scenes": [scene.model_dump() for scene in scenes],
        }
        with open(story_file, "w", encoding="utf-8") as f:
            json.dump(story_data, f, ensure_ascii=False, indent=2)
        with open(os.path.join(task_dir, "interactive.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "task_id": task_id,
                    "resolution": request.resolution,
                    "intro": request.intro.model_dump() if request.intro else None,
                    "outro": request.outro.model_dump() if request.outro else None,
                    "rounds": [round_item.model_dump() for round_item in request.rounds],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        voice_scenes = [scene for scene in scenes if scene.render_mode != "countdown"]
        logger.info(f"Interactive mode: generating voice assets for {len(voice_scenes)} question/answer scenes")
        await generate_scene_assets(
            task_dir=task_dir,
            scenes=voice_scenes,
            voice_name=request.voice_name or "default",
            voice_rate=request.voice_rate,
            language=request.language or "fixed-en-GB",
            voice_provider=request.voice_provider or "gtts",
            karaoke=request.karaoke,
            reuse_unchanged=True,
        )

        for scene in scenes:
            if scene.render_mode != "countdown":
                continue
            scene_index = int(scene.original_index or 0)
            audio_file = os.path.join(task_dir, f"{scene_index}.wav")
            _write_countdown_audio(
                audio_file,
                int(scene.countdown_seconds or 5),
                str(scene.countdown_sound or "none"),
            )
            for stale_extension in ("mp3", "srt", "words.json"):
                stale_file = os.path.join(task_dir, f"{scene_index}.{stale_extension}")
                if os.path.exists(stale_file):
                    try:
                        os.remove(stale_file)
                    except OSError:
                        pass

        return await render_final_video(
            task_dir=task_dir,
            scenes=scenes,
            karaoke=request.karaoke,
            chinese_subtitle_enabled=request.chinese_subtitle_enabled,
            resolution=request.resolution,
            subtitle_font=request.subtitle_font,
            subtitle_font_size=request.subtitle_font_size,
            subtitle_color=request.subtitle_color,
            karaoke_highlight_color=request.karaoke_highlight_color,
            motion_effect="none",
            motion_strength=0.0,
            framing=request.framing,
            transition="cut",
            transition_duration=0.0,
        )
    except Exception as e:
        logger.error(f"Failed to assemble interactive video: {e}")
        raise e


async def regenerate_image_impl(request: RegenerateImageRequest) -> str:
    """Regenerate a specific scene image and overwrite it in the task dir."""
    try:
        task_id = request.task_id
        task_dir = utils.task_dir(task_id)
        if not os.path.exists(task_dir):
            raise ValueError(f"Task directory not found: {task_dir}.")
        
        logger.info(f"Regenerating image for scene {request.scene_index} in task {task_id}")
        import time
        import asyncio
        from app.services.llm import llm_service
        
        timestamp = int(time.time())
        filename = f"{request.scene_index}_{timestamp}.png"
        image_path = os.path.join(task_dir, filename)

        image_url = await asyncio.to_thread(
            llm_service.generate_image,
            prompt=request.scene_prompt,
            image_llm_provider=request.image_llm_provider,
            image_llm_model=request.image_llm_model,
            resolution=request.resolution,
            task_dir=task_dir,
            segment_index=request.scene_index,
            image_filename=filename
        )
        if not image_url:
            raise ValueError(f"No image returned from LLM provider for prompt: {request.scene_prompt}")

        # Download and save as a new file (avoid overwriting the original)

        
        if image_url.startswith('/') or image_url.startswith('\\') or os.path.isabs(image_url):
            if os.path.exists(image_url):
                if image_url != image_path:
                    shutil.copy2(image_url, image_path)
        else:
            response = requests.get(image_url, timeout=30)
            if response.status_code == 200:
                with open(image_path, "wb") as f:
                    f.write(response.content)
            else:
                raise ValueError(f"Failed to download image: {response.status_code}")
        
        # Return accessible URL for the newly generated image, bypassing browser cache
        return f"http://127.0.0.1:8889/tasks/{task_id}/{filename}?t={timestamp}"
    except Exception as e:
        logger.error(f"Failed to regenerate image: {e}")
        raise e
