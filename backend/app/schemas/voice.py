from pydantic import BaseModel, Field


class VoiceGenerationRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="要转换为语音的文本内容")
    voice_provider: str = Field(default="gtts", description="语音提供商: gtts, edge-tts, google-tts")
    language: str = Field(default="fixed-en-GB", description="语音语言")
    voice_name: str = Field(
        default="zh-CN-XiaoxiaoNeural", 
        description="语音名称，如：zh-CN-XiaoxiaoNeural, zh-CN-YunxiNeural"
    )
    voice_rate: float = Field(
        default=0, 
        ge=-1, 
        le=1, 
        description="语速调整，范围 -1.0 到 1.0，0 表示正常速度"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": "你好，这是一个测试文本，用于生成语音和字幕文件。",
                    "voice_name": "zh-CN-XiaoxiaoNeural",
                    "voice_rate": 0
                }
            ]
        }
    }


class VoiceGenerationResponse(BaseModel):
    audio_url: str = Field(..., description="生成的音频文件URL")
    subtitle_url: str = Field(..., description="生成的字幕文件URL")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "audio_url": "/tasks/audio_1234567890_abcd1234.mp3",
                    "subtitle_url": "/tasks/subtitle_1234567890_abcd1234.srt"
                }
            ]
        }
    }
