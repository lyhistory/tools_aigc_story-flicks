import os
import time
import json
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
    afx,
)
from moviepy.video.tools import subtitles
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
import requests
import random
import shutil

from moviepy import ImageClip, CompositeVideoClip, AudioFileClip, TextClip, concatenate_videoclips
from moviepy.video.tools.subtitles import SubtitlesClip, file_to_subtitles

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
                audio_file, subtitle_file = await generate_voice(
                    scene.script,
                    voice_name,
                    voice_rate,
                    audio_file,
                    subtitle_file,
                    language
                )
            
            # 获取字幕的总时长
            subs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
            subtitle_duration = max([tb for ((ta, tb), txt) in subs])
                    
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
            # 创建音频剪辑  
            audio_clip = AudioFileClip(audio_file)
            # audio will be attached to the final composite clip
            # 使用系统字体
            font_path = os.path.join(utils.resource_dir(), "fonts", "STHeitiLight.ttc")
            if not os.path.exists(font_path):
                logger.warning("Font file not found, using default font")
                raise FileNotFoundError("Font file not found: " + font_path)
            else:
                logger.info(f"Using font: {font_path}")
            
            print(f"Using font: {font_path}")
            # 添加字幕
            if os.path.exists(subtitle_file):
                logger.info(f"Loading subtitle file: {subtitle_file}")
                try:
                    def make_textclip(text):
                        return TextClip(
                            text=text,
                            font=font_path,
                            font_size=60,
                            # color='white',
                            # stroke_color='black',
                            # stroke_width=2,
                            # method='caption',
                            # size=(origin_image_w * 0.9, None)
                        )
                    def create_text_clip(subtitle_item):
                        phrase = subtitle_item[1]
                        max_width = (origin_image_w * 0.9)
                        wrapped_txt, txt_height = wrap_text(
                            phrase, max_width=max_width, font=font_path, fontsize=60
                        )
                        _clip = TextClip(
                            text=wrapped_txt,
                            font=font_path,
                            font_size=60,
                            color="white",
                            stroke_color="black",
                            stroke_width=2,
                        )
                        duration = subtitle_item[0][1] - subtitle_item[0][0]
                        _clip = _clip.with_start(subtitle_item[0][0])
                        _clip = _clip.with_end(subtitle_item[0][1])
                        _clip = _clip.with_duration(duration)
                        _clip = _clip.with_position(("center", origin_image_h * 0.95 - _clip.h - 50))
                        return _clip

                    # Create subtitles clip
                    sub = SubtitlesClip(subtitle_file, encoding="utf-8", make_textclip=make_textclip)
                    text_clips = []
                    for item in sub.subtitles:
                        clip = create_text_clip(subtitle_item=item)
                        text_clips.append(clip)
                    video_clip = CompositeVideoClip([bg_clip, fg_clip, *text_clips], (origin_image_w, origin_image_h))
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
                        url=scene.get("url"),
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
                topic_type=request.topic_type
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
                    url=scene.get("url"),
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
            task_dir,
            scenes,
            request.voice_name,
            request.voice_rate,
            request.language,
            request.test_mode,
            request.resolution,
        )
    except Exception as e:
        logger.error(f"Failed to generate video: {e}")
        raise e
