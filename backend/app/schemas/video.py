import warnings
from enum import Enum
from typing import Any, List, Optional, Union, Dict

import pydantic
from pydantic import BaseModel, Field
from app.models.const import Language

# 忽略 Pydantic 的特定警告
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message="Field name.*shadows an attribute in parent.*",
)


class VideoConcatMode(str, Enum):
    random = "random"
    sequential = "sequential"


class VideoAspect(str, Enum):
    landscape = "16:9"
    portrait = "9:16"
    square = "1:1"

    def to_resolution(self):
        if self == VideoAspect.landscape.value:
            return 1920, 1080
        elif self == VideoAspect.portrait.value:
            return 1080, 1920
        elif self == VideoAspect.square.value:
            return 1080, 1080
        return 1080, 1920


class _Config:
    arbitrary_types_allowed = True


@pydantic.dataclasses.dataclass(config=_Config)
class MaterialInfo:
    provider: str = "pexels"
    url: str = ""
    duration: int = 0

class VideoParams(BaseModel):
    """
    {
      "video_subject": "",
      "video_aspect": "横屏 16:9（西瓜视频）",
      "voice_name": "女生-晓晓",
      "bgm_name": "random",
      "font_name": "STHeitiMedium 黑体-中",
      "text_color": "#FFFFFF",
      "font_size": 60,
      "stroke_color": "#000000",
      "stroke_width": 1.5
    }
    """

    video_subject: str
    video_script: str = ""  # Script used to generate the video
    video_terms: Optional[str | list] = None  # Keywords used to generate the video
    video_aspect: Optional[VideoAspect] = VideoAspect.portrait.value
    video_concat_mode: Optional[VideoConcatMode] = VideoConcatMode.random.value
    video_clip_duration: Optional[int] = 5
    video_count: Optional[int] = 1

    video_source: Optional[str] = "pexels"
    video_materials: Optional[List[MaterialInfo]] = None  # Materials used to generate the video

    video_language: Optional[str] = ""  # auto detect

    voice_name: Optional[str] = ""
    voice_volume: Optional[float] = 1.0
    voice_rate: Optional[float] = 1.0
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: Optional[float] = 0.2

    subtitle_enabled: Optional[bool] = True
    subtitle_position: Optional[str] = "bottom"  # top, bottom, center
    custom_position: float = 70.0
    font_name: Optional[str] = "STHeitiMedium.ttc"
    text_fore_color: Optional[str] = "#FFFFFF"
    text_background_color: Union[bool, str] = True

    font_size: int = 60
    stroke_color: Optional[str] = "#000000"
    stroke_width: float = 1.5
    n_threads: Optional[int] = 2
    paragraph_number: Optional[int] = 1


class SubtitleRequest(BaseModel):
    video_script: str
    video_language: Optional[str] = ""
    voice_name: Optional[str] = "zh-CN-XiaoxiaoNeural-Female"
    voice_volume: Optional[float] = 1.0
    voice_rate: Optional[float] = 1.2
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: Optional[float] = 0.2
    subtitle_position: Optional[str] = "bottom"
    font_name: Optional[str] = "STHeitiMedium.ttc"
    text_fore_color: Optional[str] = "#FFFFFF"
    text_background_color: Union[bool, str] = True
    font_size: int = 60
    stroke_color: Optional[str] = "#000000"
    stroke_width: float = 1.5
    video_source: Optional[str] = "local"
    subtitle_enabled: Optional[str] = "true"


class AudioRequest(BaseModel):
    video_script: str
    video_language: Optional[str] = ""
    voice_name: Optional[str] = "zh-CN-XiaoxiaoNeural-Female"
    voice_volume: Optional[float] = 1.0
    voice_rate: Optional[float] = 1.2
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: Optional[float] = 0.2
    video_source: Optional[str] = "local"


class VideoScriptParams:
    """
    {
      "video_subject": "春天的花海",
      "video_language": "",
      "paragraph_number": 1
    }
    """

    video_subject: Optional[str] = "春天的花海"
    video_language: Optional[str] = ""
    paragraph_number: Optional[int] = 1


class VideoTermsParams:
    """
    {
      "video_subject": "",
      "video_script": "",
      "amount": 5
    }
    """

    video_subject: Optional[str] = "春天的花海"
    video_script: Optional[str] = (
        "春天的花海，如诗如画般展现在眼前。万物复苏的季节里，大地披上了一袭绚丽多彩的盛装。金黄的迎春、粉嫩的樱花、洁白的梨花、艳丽的郁金香……"
    )
    amount: Optional[int] = 5


class BaseResponse(BaseModel):
    status: int = 200
    message: Optional[str] = "success"
    data: Any = None


class TaskVideoRequest(VideoParams, BaseModel):
    pass


class TaskQueryRequest(BaseModel):
    pass


class VideoScriptRequest(VideoScriptParams, BaseModel):
    pass


class VideoTermsRequest(VideoTermsParams, BaseModel):
    pass

class TaskResponse(BaseResponse):
    class TaskResponseData(BaseModel):
        task_id: str

    data: TaskResponseData

    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"task_id": "6c85c8cc-a77a-42b9-bc30-947815aa0558"},
            },
        }


class TaskQueryResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "state": 1,
                    "progress": 100,
                    "videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/final-1.mp4"
                    ],
                    "combined_videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/combined-1.mp4"
                    ],
                },
            },
        }


class TaskDeletionResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "state": 1,
                    "progress": 100,
                    "videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/final-1.mp4"
                    ],
                    "combined_videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/combined-1.mp4"
                    ],
                },
            },
        }


class VideoScriptResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "video_script": "春天的花海，是大自然的一幅美丽画卷。在这个季节里，大地复苏，万物生长，花朵争相绽放，形成了一片五彩斑斓的花海..."
                },
            },
        }


class VideoTermsResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"video_terms": ["sky", "tree"]},
            },
        }


class BgmRetrieveResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "files": [
                        {
                            "name": "output013.mp3",
                            "size": 1891269,
                            "file": "/MoneyPrinterTurbo/resource/songs/output013.mp3",
                        }
                    ]
                },
            },
        }


class BgmUploadResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"file": "/MoneyPrinterTurbo/resource/songs/example.mp3"},
            },
        }


from app.models.const import StoryType, ImageStyle, TopicType

class StoryScene(BaseModel):
    """故事场景"""
    script: str = Field(description="场景讲解脚本")
    scene_prompt: str = Field(description="图片生成提示词")
    objects: List[str] = Field(default_factory=list, description="场景中的关键对象")
    keywords: List[Dict[str, str]] = Field(default_factory=list, description="关键词与发音解释")
    url: Optional[str] = Field(default=None, description="生成的图片 URL")
    extra_images: List[str] = Field(default_factory=list, description="Additional image URLs to cycle through in order during this scene")
    is_cover: Optional[bool] = Field(default=False, description="Whether this scene is a cover/title scene")
    subject: Optional[str] = Field(default=None, description="Optional cover subject")
    topic_type: Optional[TopicType] = Field(default=None, description="Topic type of this scene")

class VideoGenerateRequest(BaseModel):
    """视频生成请求"""
    text_llm_provider: Optional[str] = Field(default=None, description="Text LLM provider")
    image_llm_provider: Optional[str] = Field(default=None, description="Image LLM provider")
    text_llm_model: Optional[str] = Field(default=None, description="Text LLM model")
    image_llm_model: Optional[str] = Field(default=None, description="Image LLM model")
    test_mode: bool = Field(default=False, description="是否为测试模式")
    task_id: Optional[str] = Field(default=None, description="任务ID")
    segments: int = Field(default=3, ge=1, le=10, description="分段数量")
    language: Language = Field(default=Language.CHINESE_CN, description="故事语言")
    story_prompt: Optional[str] = Field(default=None, description="故事提示词")
    topic_type: Optional[TopicType] = Field(default=None, description="主题类型")
    image_style: ImageStyle = Field(default=ImageStyle.realistic, description="图片风格")
    subject: Optional[str] = Field(default=None, description="Optional cover subject")
    learner_age: Optional[str] = Field(default="3-5", description="Learner age band")
    voice_provider: str = Field(default="gtts", description="语音提供商: gtts, edge-tts, google-tts")
    voice_name: str = Field(default="zh-CN-XiaoxiaoNeural", description="语音名称")
    voice_rate: float = Field(default=1.0, description="语音速率")
    karaoke: bool = Field(default=True, description="是否启用卡拉OK逐词高亮")
    chinese_subtitle_enabled: bool = Field(default=True, description="是否启用中文字幕翻译")
    resolution: Optional[str] = Field(default="1024*1024", description="分辨率")
    use_inpainting: Optional[bool] = Field(default=False, description="是否使用图生图/修复模式")
    avoid_exact_counts: Optional[bool] = Field(default=None, description="是否避免精确数量")

class RegenerateImageRequest(BaseModel):
    """单场景重新生成图片请求"""
    task_id: str = Field(description="任务ID")
    scene_index: int = Field(description="场景索引（1-based，例如第1个场景传1）")
    scene_prompt: str = Field(description="场景的提示词")
    image_llm_provider: Optional[str] = None
    image_llm_model: Optional[str] = None
    resolution: Optional[str] = "1024*1024"


class StoryboardAssembleRequest(BaseModel):
    """第二阶段：视频拼装请求"""
    task_id: str = Field(description="任务ID（Step 1 返回的）")
    scenes: List[StoryScene] = Field(description="用户确认或修改后的场景列表")
    resolution: Optional[str] = Field(default="1024*1024", description="分辨率")
    chinese_subtitle_enabled: bool = Field(default=True, description="是否启用中文字幕")
    karaoke: bool = Field(default=True, description="是否启用卡拉OK高亮")
    subtitle_font: Optional[str] = Field(default=None, description="English subtitle font variant (e.g. 'NotoSans-Bold')")
    subtitle_font_size: Optional[int] = Field(default=None, description="English subtitle font size (px); default depends on resolution")
    subtitle_color: Optional[str] = Field(default=None, description="English subtitle text color hex, e.g. '#F472B6'")


class VideoGenerateResponse(BaseModel):
    """视频生成响应"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
