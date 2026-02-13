from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from app.schemas.voice import VoiceGenerationRequest, VoiceGenerationResponse
from app.schemas.video import VideoGenerateResponse, StoryScene
from app.services.voice import (
    generate_voice,
    get_all_azure_voices,
    list_edge_tts_voices,
    list_google_tts_voices,
    get_voice_options,
)
from app.models.const import Language
from app.services.video import create_video_with_scenes
import os
import json
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter()


class VoiceRequest(BaseModel):
    provider: Optional[str] = "edge-tts"
    language: Optional[str] = None
    area: Optional[List[str]] = None


@router.post("/test_subtitle")
async def test_subtitle_endpoint(task_id: str = Query(..., description="任务ID，对应 storage/tasks/ 下的目录名")) -> VideoGenerateResponse:
    """测试字幕添加功能"""
    try:
        # 构建任务目录路径
        task_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "tasks", task_id)
        if not os.path.exists(task_dir):
            raise HTTPException(status_code=404, detail=f"Task directory not found: {task_id}")
            
        # 读取 story.json
        story_file = os.path.join(task_dir, "story.json")
        if not os.path.exists(story_file):
            raise HTTPException(status_code=404, detail=f"Story file not found: {story_file}")
            
        with open(story_file, 'r', encoding='utf-8') as f:
            scenes_data = json.load(f)
        
        # 转换为 StoryScene 对象
        scenes = [StoryScene(**scene) for scene in scenes_data]
        
        # 生成语音和字幕
        voice_name = "zh-CN-XiaoxiaoNeural"
        voice_rate = 0
        for i, scene in enumerate(scenes, 1):
            audio_file = os.path.join(task_dir, f"{i}.mp3")
            subtitle_file = os.path.join(task_dir, f"{i}.srt")
            await generate_voice(scene.text, voice_name, voice_rate, audio_file, subtitle_file)
        
        # 创建视频
        video_file = await create_video_with_scenes(
            task_dir=task_dir,
            scenes=scenes,
            voice_name=voice_name,
            voice_rate=voice_rate,
            voice_provider="gtts",
        )
        
        video_url = "/" + video_file.split("/tasks/")[-1]
        return VideoGenerateResponse(video_url=video_url, scenes=scenes)
    except Exception as e:
        logger.error(f"Failed to test subtitle: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate", response_model=VoiceGenerationResponse)
async def generate_voice_api(request: Request) -> VoiceGenerationResponse:
    """
    生成语音和字幕文件
    
    Args:
        request: 包含文本内容和语音配置的请求
        
    Returns:
        生成的音频和字幕文件的URL
    """
    try:
        # 手动解析请求体
        body = await request.json()
        req = VoiceGenerationRequest(**body)
        
        audio_file, subtitle_file = await generate_voice(
            text=req.text,
            voice_name=req.voice_name,
            voice_rate=req.voice_rate,
            language=req.language,
            voice_provider=req.voice_provider,
        )
        
        if not audio_file or not subtitle_file:
            raise HTTPException(status_code=500, detail="Failed to generate voice")
            
        # 将文件路径转换为URL路径
        audio_url = f"/tasks/{os.path.basename(audio_file)}"
        subtitle_url = f"/tasks/{os.path.basename(subtitle_file)}"
        
        return VoiceGenerationResponse(
            audio_url=audio_url,
            subtitle_url=subtitle_url
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voices")
async def list_voices(request: VoiceRequest) -> dict:
    """
    获取所有支持的语音列表
    """
    provider = (request.provider or "edge-tts").lower()
    if provider == "google-tts":
        return {"voices": list_google_tts_voices(request.language, request.area)}
    if provider == "gtts":
        return {"voices": []}
    return {"voices": list_edge_tts_voices(request.language, request.area)}

@router.get("/options")
async def voice_options() -> dict:
    allowed = [lang.value for lang in Language if not lang.value.startswith("fixed-")]
    return get_voice_options(allowed)
