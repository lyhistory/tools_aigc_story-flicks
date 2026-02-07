from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os

class Settings(BaseSettings):
    app_name: str = "Story Flicks"
    debug: bool = True
    version: str = "1.0.0"
    
    # provider configuration
    text_provider: str = "openai"
    image_provider: str = "openai"

    # base url configuration
    openai_base_url: str = "https://api.openai.com/v1"
    aliyun_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    # NVIDIA
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_api_key: str = ""
    
    #cloudflare
    cloudflare_base_url: str = "https://api.cloudflare.com/client/v4/accounts"
    cloudflare_api_key: str = ""
    cloudflare_account_id: str = ""
    
    # api key
    openai_api_key: str = ""
    aliyun_api_key: str = ""
    deepseek_api_key: str = ""
    ollama_api_key: str = ""
    siliconflow_api_key: str = ""

    text_llm_model: str = "meta/llama-3.1-70b-instruct" #"gpt-4o"
    image_llm_model: str = "black-forest-labs/flux-1-dev" #"dall-e-3"
    
    image_resolution: str = "1080*1920"
    avoid_exact_counts: bool = False

    class Config:
        env_file = ".env"
        # env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

@lru_cache()
def get_settings() -> Settings:
    return Settings()
