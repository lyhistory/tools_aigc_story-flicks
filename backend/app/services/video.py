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
from PIL import Image, ImageDraw, ImageFont
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

async def create_video_with_scenes(
        task_dir: str, 
        scenes: List[StoryScene], 
        voice_name: str, 
        voice_rate: float, 
        language: str = "en-US",
        test_mode: bool = False) -> str:
    """创建带有场景的视频

    Args:
        task_dir (str): 任务目录
        scenes (List[StoryScene]): 场景列表
        voice_name (str): 语音名称
        voice_rate (float): 语音速率
        test_mode (bool): 是否为测试模式，如果是则使用已有的图片、音频、字幕文件
    """
    clips = []
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
                    scene.text,
                    voice_name,
                    voice_rate,
                    audio_file,
                    subtitle_file,
                    language
                )
            
            # 获取字幕的总时长
            subs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
            subtitle_duration = max([tb for ((ta, tb), txt) in subs])
                    
            # 创建图片剪辑
            image_clip = ImageClip(image_file)
            origin_image_w, origin_image_h = image_clip.size  # 获取放大后的图片尺寸
            image_scale = 1.2
            # Resize and set duration
            image_clip = image_clip.resized(image_scale)

            # Crop back to canvas size
            image_clip = image_clip.cropped(
                x_center=image_clip.w / 2,
                y_center=image_clip.h / 2,
                width=origin_image_w,
                height=origin_image_h
            )
            # 确保图片视频时长至少和字幕一样长
            image_clip = image_clip.with_duration(subtitle_duration)

            width_diff = origin_image_w * (image_scale - 1)
            def pan_position(t):
                logger.info(f"当前时间 t = {t}， subtitle_duration={subtitle_duration}， width_diff={width_diff}，{width_diff/subtitle_duration*t}")
                x = -width_diff * (t / subtitle_duration)
                y = 0
                return (x, y)
            image_clip = image_clip.with_position(pan_position)
            # 创建音频剪辑  
            audio_clip = AudioFileClip(audio_file)
            image_clip = image_clip.with_audio(audio_clip)
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
                    video_clip = CompositeVideoClip([image_clip, *text_clips], (origin_image_w, origin_image_h))
                    clips.append(video_clip)
                    logger.info(f"Added subtitles for scene {i}")
                
                except Exception as e:
                    logger.error(f"Failed to add subtitles for scene {i}: {str(e)}")
                    clips.append(image_clip.with_audio(audio_clip))
            else:
                logger.warning(f"Subtitle file not found: {subtitle_file}")
                clips.append(image_clip.with_audio(audio_clip))
        except Exception as e:
            logger.error(f"Failed to process scene {i}: {str(e)}")
            raise e
    
    if not clips:
        raise ValueError("No valid clips to combine")

    # 合并所有片段
    logger.info("Merging all clips")
    final_clip = concatenate_videoclips(clips)
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
            scenes = [StoryScene(**scene) for scene in story_data.get("scenes", [])]
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
                image_llm_model=request.image_llm_model
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
        
            scenes = [StoryScene(text=scene["text"], image_prompt=scene["image_prompt"], url=scene["url"]) for scene in story_list]
            
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
        return await create_video_with_scenes(task_dir, scenes, request.voice_name, request.voice_rate,request.language, request.test_mode)
    except Exception as e:
        logger.error(f"Failed to generate video: {e}")
        raise e