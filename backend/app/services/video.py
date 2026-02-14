import os
import time
import json
import math
from typing import List
from app.schemas.llm import StoryGenerationRequest
from loguru import logger
from app.models.const import StoryType, ImageStyle
from app.schemas.video import VideoGenerateRequest, StoryScene
from app.services.llm import llm_service
from app.services.voice import generate_voice
from app.utils import utils
from moviepy import (
    VideoFileClip,
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

def fit_image_to_size(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    # Fit image inside target without stretching, return sharp fit on transparent canvas
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h
    if img_ratio > target_ratio:
        new_w = target_w
        new_h = max(1, int(target_w / img_ratio))
    else:
        new_h = target_h
        new_w = max(1, int(target_h * img_ratio))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(resized, (x, y))
    return canvas

def build_image_clips(image_file: str, target_w: int, target_h: int, duration: float, image_scale: float = 1.2):
    img = Image.open(image_file).convert("RGB")
    # Background: heavy blur of full-frame image
    bg = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    blur_radius = max(24, min(target_w, target_h) // 12)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    bg_clip = ImageClip(np.array(bg)).with_duration(duration)

    # Foreground: sharp fit on transparent canvas
    fg_rgba = fit_image_to_size(img, target_w, target_h)
    fg_np = np.array(fg_rgba)
    fg_rgb = fg_np[..., :3]
    fg_mask = fg_np[..., 3] / 255.0

    fg_clip = ImageClip(fg_rgb).with_mask(ImageClip(fg_mask, is_mask=True))
    origin_image_w, origin_image_h = fg_clip.size

    fg_clip = fg_clip.resized(image_scale)
    fg_clip = fg_clip.cropped(
        x_center=fg_clip.w / 2,
        y_center=fg_clip.h / 2,
        width=origin_image_w,
        height=origin_image_h
    )
    fg_clip = fg_clip.with_duration(duration)

    width_diff = origin_image_w * (image_scale - 1)
    def pan_position(t):
        if duration <= 0:
            return (0, 0)
        x = -width_diff * (t / duration)
        y = 0
        return (x, y)
    fg_clip = fg_clip.with_position(pan_position)
    return bg_clip, fg_clip, origin_image_w, origin_image_h

async def create_video_with_scenes(
        task_dir: str, 
        scenes: List[StoryScene], 
        voice_name: str, 
        voice_rate: float, 
        language: str = "en-US",
        voice_provider: str = "gtts",
        karaoke: bool = False,
        test_mode: bool = False,
        resolution: str = None) -> str:
    """创建带有场景的视频

    Args:
        task_dir (str): 任务目录
        scenes (List[StoryScene]): 场景列表
        voice_name (str): 语音名称
        voice_rate (float): 语音速率
        test_mode (bool): 是否为测试模式，如果是则使用已有的图片、音频、字幕文件
    """
    clips = []
    target_w, target_h = parse_resolution(resolution) if resolution else (None, None)
    overlay_dir = os.path.join(task_dir, "overlays")
    os.makedirs(overlay_dir, exist_ok=True)
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
        cleaned = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        cleaned = cleaned.replace("\ufeff", "")
        cleaned = cleaned.replace("“", "\"").replace("”", "\"").replace("’", "'").replace("‘", "'")
        return cleaned.strip()

    def normalize_token(token: str) -> str:
        t = re.sub(r"[^a-zA-Z]", "", token.lower())
        for suf in ["ing", "ed", "es", "s"]:
            if t.endswith(suf) and len(t) > len(suf) + 2:
                return t[: -len(suf)]
        return t

    def tokenize_karaoke_words(text: str) -> list[str]:
        if not text:
            return []
        return [m.group(0) for m in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+", text)]

    def load_karaoke_words(words_file: str) -> list[dict]:
        if not os.path.exists(words_file):
            return []
        try:
            with open(words_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            words = data.get("words", []) if isinstance(data, dict) else []
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
            return out
        except Exception as e:
            logger.warning(f"Failed to load karaoke words from {words_file}: {e}")
            return []

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
        highlight_bg_rgba=(255, 241, 153, 180),
    ) -> tuple[int, int]:
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
        highlight_bg_rgba=(255, 241, 153, 180),
        highlight_text_color: str | None = None,
        highlight_token_index: int | None = None,
        stroke_width: int = 0,
        stroke_fill: str | None = None,
    ) -> Image.Image | None:
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
            line_tokens = list(re.finditer(r"[A-Za-z']+|\d+", line))
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
    for i, scene in enumerate(scenes, 1):
        try:
            # 获取文件路径
            image_file = os.path.join(task_dir, f"{i}.png")
            audio_file = os.path.join(task_dir, f"{i}.mp3")
            subtitle_file = os.path.join(task_dir, f"{i}.srt")

            # Test mode check
            if test_mode:
                if not (os.path.exists(image_file) and os.path.exists(audio_file) and os.path.exists(subtitle_file)):
                    logger.warning(f"Test mode: Required files not found for scene {i}")
                    raise FileNotFoundError("Required files not found")
            else:
                # 正式模式下生成所需文件
                logger.info(f"Processing scene {i}")
                lead_silence_ms = 0
                trail_silence_ms = 0
                sentence_pause_ms = None
                if getattr(scene, "is_cover", False) and (voice_provider or "gtts") == "gtts":
                    lead_silence_ms = 300
                    trail_silence_ms = 900
                    sentence_pause_ms = 550
                audio_file, subtitle_file = await generate_voice(
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
                    karaoke=karaoke,
                )
            
            # 获取字幕的总时长
            subs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
            subtitle_duration = max([tb for ((ta, tb), txt) in subs])
            # 创建音频剪辑
            audio_clip = AudioFileClip(audio_file)
            if audio_clip.duration and audio_clip.duration > subtitle_duration:
                subtitle_duration = audio_clip.duration
            # 创建图片剪辑（统一尺寸，避免拉伸/拼贴）
            if target_w is None or target_h is None:
                base_img = Image.open(image_file)
                target_w, target_h = base_img.size
                base_img.close()
            bg_clip, fg_clip, origin_image_w, origin_image_h = build_image_clips(
                image_file=image_file,
                target_w=target_w,
                target_h=target_h,
                duration=subtitle_duration,
                image_scale=1.2
            )
            # audio will be attached to the final composite clip
            # 使用系统字体
            font_path = os.path.join(utils.resource_dir(), "fonts", "STHeitiLight.ttc")
            keyword_font_path = os.path.join(utils.resource_dir(), "fonts", "MicrosoftYaHeiNormal.ttc")
            subtitle_font_path = os.path.join(utils.resource_dir(), "fonts", "MicrosoftYaHeiBold.ttc")
            ipa_font_path = os.path.join(
                utils.resource_dir(),
                "fonts",
                "Noto_Sans",
                "NotoSans-VariableFont_wdth,wght.ttf",
            )
            noto_static_dir = os.path.join(utils.resource_dir(), "fonts", "Noto_Sans", "static")
            noto_bold = os.path.join(noto_static_dir, "NotoSans-Bold.ttf")
            noto_semibold = os.path.join(noto_static_dir, "NotoSans-SemiBold.ttf")
            noto_regular = os.path.join(noto_static_dir, "NotoSans-Regular.ttf")
            noto_light = os.path.join(noto_static_dir, "NotoSans-Light.ttf")
            if not os.path.exists(font_path):
                logger.warning("Font file not found, using default font")
                raise FileNotFoundError("Font file not found: " + font_path)
            else:
                logger.info(f"Using font: {font_path}")
            if not os.path.exists(keyword_font_path):
                keyword_font_path = font_path
            if not os.path.exists(subtitle_font_path):
                subtitle_font_path = keyword_font_path
            if os.path.exists(ipa_font_path):
                keyword_font_path = ipa_font_path
                subtitle_font_path = ipa_font_path
            if os.path.exists(noto_bold):
                subtitle_font_path = noto_bold
            if os.path.exists(noto_semibold):
                keyword_font_path = noto_semibold
            keyword_pron_font_path = noto_regular if os.path.exists(noto_regular) else keyword_font_path
            keyword_expl_font_path = noto_light if os.path.exists(noto_light) else keyword_font_path
            # 添加字幕 (PIL render directly onto frames)
            if os.path.exists(subtitle_file):
                logger.info(f"Loading subtitle file: {subtitle_file}")
                try:
                    sub = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
                    subtitle_items = []
                    karaoke_enabled = bool(karaoke)
                    words_file = os.path.join(task_dir, f"{i}.words.json")
                    karaoke_words_all = load_karaoke_words(words_file) if karaoke_enabled else []
                    karaoke_word_ptr = 0
                    cover_text = (getattr(scene, "subject", None) or "").strip()
                    if getattr(scene, "is_cover", False) and cover_text:
                        cover_font_size = max(72, int(origin_image_h * 0.08))
                        cover_img = render_text_rgba(
                            cover_text,
                            subtitle_font_path,
                            cover_font_size,
                            "#22C55E",
                            max_width=int(origin_image_w * 0.85),
                            bg_rgba=(0, 0, 0, 0),
                            stroke_width=max(2, int(cover_font_size * 0.08)),
                            stroke_fill="#0F172A",
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
                    for item_idx, item in enumerate(sub, 1):
                        phrase = clean_subtitle_text(item[1])
                        if not phrase:
                            continue
                        kw = find_keyword_in_text(phrase, scene.keywords)
                        highlight_word = (kw.get("word") or "").strip() if kw else ""
                        highlight_norm = normalize_token(highlight_word) if highlight_word else ""
                        sub_img = render_text_rgba(
                            phrase,
                            subtitle_font_path,
                            58,
                            "#F472B6",
                            max_width=int(origin_image_w * 0.9),
                            bg_rgba=(0, 0, 0, 0),
                            highlight_word=highlight_word,
                            highlight_bg_rgba=(254, 240, 138, 220),
                            highlight_text_color="#111827",
                        )
                        line_start = float(item[0][0])
                        line_end = float(item[0][1])
                        phrase_tokens = tokenize_karaoke_words(phrase)
                        karaoke_words_line = []
                        if karaoke_enabled and phrase_tokens:
                            line_dur = max(0.05, line_end - line_start)
                            approx_step = line_dur / len(phrase_tokens)
                            for token_idx, token in enumerate(phrase_tokens):
                                ws = line_start + token_idx * approx_step
                                we = line_end if token_idx == len(phrase_tokens) - 1 else line_start + (token_idx + 1) * approx_step
                                if karaoke_word_ptr < len(karaoke_words_all):
                                    w = karaoke_words_all[karaoke_word_ptr]
                                    karaoke_word_ptr += 1
                                    ws = max(line_start, float(w.get("start", ws)))
                                    we = min(line_end, float(w.get("end", we)))
                                    if we <= ws:
                                        we = min(line_end, ws + max(0.03, approx_step * 0.8))
                                karaoke_words_line.append(
                                    {
                                        "token_index": token_idx,
                                        "word": token,
                                        "start": round(ws, 4),
                                        "end": round(max(ws + 0.03, we), 4),
                                    }
                                )
                        sub_karaoke_imgs = {}
                        if karaoke_enabled and karaoke_words_line:
                            for word_info in karaoke_words_line:
                                token_idx = int(word_info["token_index"])
                                token_norm = normalize_token(word_info.get("word", ""))
                                is_keyword_token = bool(highlight_norm and token_norm == highlight_norm)
                                kara_bg = (147, 197, 253, 210)
                                if is_keyword_token:
                                    kara_bg = (254, 240, 138, 230)
                                kara_img = render_text_rgba(
                                    phrase,
                                    subtitle_font_path,
                                    58,
                                    "#F472B6",
                                    max_width=int(origin_image_w * 0.9),
                                    bg_rgba=(0, 0, 0, 0),
                                    highlight_token_index=token_idx,
                                    highlight_bg_rgba=kara_bg,
                                    highlight_text_color="#111827",
                                )
                                if kara_img is not None:
                                    sub_karaoke_imgs[token_idx] = kara_img
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
                        sub_x = (origin_image_w - sub_img.width) // 2 if sub_img else 0
                        sub_y = int(origin_image_h * 0.95 - (sub_img.height if sub_img else 0) - 50)
                        kw_x = (origin_image_w - kw_img.width) // 2 if kw_img else 0
                        kw_y = 20
                        subtitle_items.append(
                            {
                                "start": item[0][0],
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
                    base_clip = CompositeVideoClip([bg_clip, fg_clip], (origin_image_w, origin_image_h))
                    if subtitle_items:
                        logger.info(f"Subtitle items rendered for scene {i}: {len(subtitle_items)}")
                        fps = 24
                        total_frames = max(1, int(math.ceil(subtitle_duration * fps)))
                        frames = []
                        for frame_idx in range(total_frames):
                            t = frame_idx / fps
                            frame = base_clip.get_frame(t)
                            frame = overlay_frame_with_subs(frame, t, subtitle_items)
                            frames.append(frame)
                        video_clip = ImageSequenceClip(frames, fps=fps)
                    else:
                        logger.warning(f"No subtitle items rendered for scene {i}")
                        video_clip = base_clip
                    clips.append(video_clip.with_audio(audio_clip))
                    logger.info(f"Added subtitles for scene {i}")
                except Exception as e:
                    logger.error(f"Failed to add subtitles for scene {i}: {str(e)}")
                    video_clip = CompositeVideoClip([bg_clip, fg_clip], (origin_image_w, origin_image_h))
                    clips.append(video_clip.with_audio(audio_clip))
            else:
                logger.warning(f"Subtitle file not found: {subtitle_file}")
                video_clip = CompositeVideoClip([bg_clip, fg_clip], (origin_image_w, origin_image_h))
                clips.append(video_clip.with_audio(audio_clip))
        except Exception as e:
            logger.error(f"Failed to process scene {i}: {str(e)}")
            raise e
    
    if not clips:
        raise ValueError("No valid clips to combine")

    # 合并所有片段
    logger.info("Merging all clips")
    final_clip = concatenate_videoclips(clips, method="compose")
    video_file = os.path.join(task_dir, "video.mp4")
    logger.info(f"Writing video to {video_file}")
    final_clip.write_videofile(video_file, fps=24, codec='libx264', audio_codec='aac')
    
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
                )
                for scene in story_list
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
        # 生成视频
        return await create_video_with_scenes(
            task_dir=task_dir,
            scenes=scenes,
            voice_name=request.voice_name,
            voice_rate=request.voice_rate,
            language=request.language,
            voice_provider=getattr(request, "voice_provider", "gtts"),
            karaoke=getattr(request, "karaoke", False),
            test_mode=request.test_mode,
            resolution=request.resolution,
        )
    except Exception as e:
        logger.error(f"Failed to generate video: {e}")
        raise e
