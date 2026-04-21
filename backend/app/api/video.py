from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from app.services.video import generate_video, generate_storyboard_impl, assemble_video_impl, regenerate_image_impl, SUBTITLE_FONT_MAP, DEFAULT_SUBTITLE_FONT
from app.schemas.video import VideoGenerateRequest, VideoGenerateResponse, StoryScene, StoryboardAssembleRequest, RegenerateImageRequest
import os
import json
from app.utils.utils import extract_id

router = APIRouter()

@router.post("/generate")
async def generate_video_endpoint(
    request: VideoGenerateRequest
):
    """生成视频"""
    try:
        video_file = await generate_video(request)
        task_id = extract_id(video_file)
        # 转换为相对路径
        video_url = "http://127.0.0.1:8888/tasks/" + task_id + "/video.mp4"
        return VideoGenerateResponse(
            success=True,
            data={"video_url": video_url}
        )
    except Exception as e:
        logger.error(f"Failed to generate video: {str(e)}")
        return VideoGenerateResponse(
            success=False,
            message=str(e)
        )


@router.post("/generate_storyboard")
async def generate_storyboard_endpoint(request: VideoGenerateRequest):
    """第二阶段新增：生成分镜资源（剧本、图片、音频），返回 Timeline 数据"""
    try:
        data = await generate_storyboard_impl(request)
        return VideoGenerateResponse(
            success=True,
            data=data
        )
    except Exception as e:
        logger.error(f"Failed to generate storyboard: {str(e)}")
        return VideoGenerateResponse(
            success=False,
            message=str(e)
        )


@router.post("/assemble_video")
async def assemble_video_endpoint(request: StoryboardAssembleRequest):
    """第二阶段新增：根据前端传回的 scenes timeline，合成最终视频"""
    try:
        video_file = await assemble_video_impl(request)
        task_id = extract_id(video_file)
        video_url = "http://127.0.0.1:8888/tasks/" + task_id + "/video.mp4"
        return VideoGenerateResponse(
            success=True,
            data={"video_url": video_url}
        )
    except Exception as e:
        logger.error(f"Failed to assemble video: {str(e)}")
        return VideoGenerateResponse(
            success=False,
            message=str(e)
        )

@router.post("/regenerate_image")
async def regenerate_image_endpoint(request: RegenerateImageRequest):
    """第二阶段新增：单场景重新生成图片"""
    try:
        new_url = await regenerate_image_impl(request)
        return VideoGenerateResponse(
            success=True,
            data={"image_url": new_url}
        )
    except Exception as e:
        logger.error(f"Failed to regenerate image: {str(e)}")
        return VideoGenerateResponse(
            success=False,
            message=str(e)
        )

from app.schemas.video import RetranslateScriptRequest
from app.services.video import retranslate_script_impl

@router.post("/retranslate_script")
async def retranslate_script_endpoint(request: RetranslateScriptRequest):
    """第二阶段新增：使用LLM重新生成友好的中文翻译"""
    try:
        translated_text = await retranslate_script_impl(request)
        return VideoGenerateResponse(
            success=True,
            data={"translation": translated_text}
        )
    except Exception as e:
        logger.error(f"Failed to retranslate script: {str(e)}")
        return VideoGenerateResponse(
            success=False,
            message=str(e)
        )

@router.get("/fonts")
async def get_subtitle_fonts():
    """Return available subtitle font options."""
    fonts = [
        {"id": key, "label": key.replace("NotoSans-", "Noto Sans ").replace("-", " "), "default": key == DEFAULT_SUBTITLE_FONT}
        for key in SUBTITLE_FONT_MAP.keys()
    ]
    return {"success": True, "data": {"fonts": fonts, "default": DEFAULT_SUBTITLE_FONT}}
