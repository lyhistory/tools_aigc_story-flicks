from fastapi import APIRouter, HTTPException
from app.services.llm import llm_service
from app.schemas.llm import (
    StoryGenerationRequest,
    StoryGenerationResponse,
    ImageGenerationRequest,
    ImageGenerationResponse,
)
from loguru import logger
from enum import Enum
from typing import List, Dict

router = APIRouter()

class LLMType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"


@router.post("/story", response_model=StoryGenerationResponse)
async def generate_story(request: StoryGenerationRequest) -> StoryGenerationResponse:
    """生成故事"""
    try:
        segments = llm_service.generate_story(
            request
        )
        return StoryGenerationResponse(segments=segments)
    except Exception as e:
        logger.error(f"Failed to generate story: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/image", response_model=ImageGenerationResponse)
async def generate_image(request: ImageGenerationRequest) -> ImageGenerationResponse:
    """生成图片"""
    try:
        image_url = llm_service.generate_image(prompt=request.prompt, image_llm_provider=request.image_llm_provider, image_llm_model=request.image_llm_model, resolution=request.resolution)
        return ImageGenerationResponse(image_url=image_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/story-with-images", response_model=StoryGenerationResponse)
async def generate_story_with_images(request: StoryGenerationRequest) -> StoryGenerationResponse:
    """生成故事和配图"""
    try:
        segments = llm_service.generate_story_with_images(
            segments=request.segments,
            story_prompt=request.story_prompt,
            language=request.language
        )
        return StoryGenerationResponse(segments=segments)
    except Exception as e:
        logger.error(f"Failed to generate story with images: {e}")
        raise HTTPException(status_code=500, detail=str(e))

#@router.get("/providers", response_model=Dict[str, List[str]])
@router.get("/providers")
async def get_llm_providers():
    """
    获取 LLM Provider 列表
    """
    # 这里将实现获取 LLM Provider 的逻辑
    return llm_service.get_llm_providers()
