from pydantic import BaseModel, Field
from typing import List, Dict, Any
from app.models.const import Language, TopicType
from typing import Optional

class StoryGenerationRequest(BaseModel):
    resolution: Optional[str] = Field(default="1024*1024", description="分辨率")
    text_llm_provider: Optional[str] = Field(default=None, description="Text LLM provider")
    text_llm_model: Optional[str] = Field(default=None, description="Text LLM model")
    image_llm_provider: Optional[str] = Field(default=None, description="Image LLM provider")
    image_llm_model: Optional[str] = Field(default=None, description="Image LLM model")
    segments: int = Field(..., ge=1, le=10, description="Number of story segments to generate")
    story_prompt: str = Field(..., min_length=1, max_length=4000, description="Theme or topic of the story")
    language: Language = Field(default=Language.CHINESE_CN, description="Story language")
    topic_type: Optional[TopicType] = Field(default=None, description="Topic type for storyboard generation")
    use_inpainting: Optional[bool] = Field(default=False, description="Whether to use img2img/inpainting for image generation")
    avoid_exact_counts: Optional[bool] = Field(default=None, description="Avoid exact numeric counts in prompts")


class StorySegment(BaseModel):
    script: str = Field(..., description="Narration script for subtitles/voice")
    scene_prompt: str = Field(..., description="Visual scene prompt for image generation")
    objects: List[str] = Field(default_factory=list, description="Key plural objects to show in the scene")
    url: str = Field(None, description="Generated image URL")


class StoryGenerationResponse(BaseModel):
    segments: List[StorySegment] = Field(..., description="Generated story segments")


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000, description="Description of the image to generate")
    image_llm_provider: Optional[str] = Field(default=None, description="Image LLM provider")
    image_llm_model: Optional[str] = Field(default=None, description="Image LLM model")
    resolution: Optional[str] = Field(default="1024*1024", description="Image resolution")


class ImageGenerationResponse(BaseModel):
    image_url: str = Field(..., description="Generated image URL")
