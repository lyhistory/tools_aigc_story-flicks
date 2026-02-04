import base64
import time
import os
from openai import OpenAI
from app.config import get_settings
from loguru import logger
from typing import List, Dict, Any
import json
from http import HTTPStatus
from pathlib import PurePosixPath,Path
import requests
from urllib.parse import urlparse, unquote
import random
import logging
from PIL import Image, ImageDraw
import io

from tenacity import retry, stop_after_attempt, wait_exponential,retry_if_exception_type,before_sleep_log

from app.models.const import LANGUAGE_NAMES, Language
from app.exceptions import LLMResponseValidationError
import dashscope

from dashscope import ImageSynthesis
from app.schemas.llm import (
    StoryGenerationRequest,
)
settings = get_settings()

# NVIDIA client - OpenAI compatible
nvidia_client = None
if settings.nvidia_api_key:
    nvidia_client = OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url
    )
openai_client = None
if settings.openai_api_key:
   openai_client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or "https://api.openai.com/v1")
aliyun_text_client = None
if settings.aliyun_api_key:
    dashscope.api_key = settings.aliyun_api_key
    aliyun_text_client = OpenAI(base_url=settings.aliyun_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1", api_key=settings.aliyun_api_key) 
if settings.deepseek_api_key:
    deepseek_client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url or "https://api.deepseek.com/v1")
if settings.ollama_api_key:
    ollama_client = OpenAI(api_key=settings.ollama_api_key, base_url=settings.ollama_base_url or "http://localhost:11434/v1")
if settings.siliconflow_api_key:
    siliconflow_client = OpenAI(api_key=settings.siliconflow_api_key, base_url=settings.siliconflow_base_url or "https://api.siliconflow.cn/v1")

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=60, max=120),
    retry=retry_if_exception_type(Exception),  # retry on almost everything
    before_sleep=before_sleep_log(logger,logging.WARNING),
    reraise=True
)
def call_flux(invoke_url: str, headers: dict, payload: dict):
    logger.info(f"Attempting FLUX call | URL: {invoke_url} | payload keys: {list(payload.keys())}")

    response = requests.post(invoke_url, headers=headers, json=payload, timeout=300)  # 5 min timeout
    logger.info(f"HTTP status: {response.status_code}")
    response.raise_for_status()
    return response.json()

class LLMService:
    def __init__(self):
        self.openai_client = openai_client
        self.aliyun_text_client = aliyun_text_client
        self.nvidia_client = nvidia_client
        self.text_llm_model = settings.text_llm_model
        self.image_llm_model = settings.image_llm_model
    
    async def generate_story(self, request: StoryGenerationRequest) -> List[Dict[str, Any]]:
        """生成故事场景
        Args:
            story_prompt (str, optional): 故事提示. Defaults to None.
            segments (int, optional): 故事分段数. Defaults to 3.

        Returns:
            List[Dict[str, Any]]: 故事场景列表
        """
        if request.segments == 1:
            # Special case: exactly 1 segment → skip LLM, directly use user-provided prompt
            logger.info("segments == 1 → skipping LLM, using story_prompt directly as single scene")
            
            # Create image prompt from story_prompt (you can customize this logic)
            image_prompt = f"Detailed, family-friendly illustration of: {request.story_prompt}. Suitable for children, bright colors, no violence."

            single_scene = {
                "text": request.story_prompt.strip(),
                "image_prompt": image_prompt.strip()
            }
            
            return [single_scene]
            
        if request.language == Language.CHINESE_CN:
            system_content = "你是一个专业的故事创作者，善于创作引人入胜的故事。请只返回JSON格式的内容。"
            base_start = "讲一个故事，主题是："
            text_lang_note = "written in Chinese (简体中文)"
        else:
            system_content = "You are a professional storyteller, skilled at creating engaging stories. Please return only JSON format content."
            base_start = "Tell a story about:"
            text_lang_note = "written in English"
        
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": await self._get_story_prompt(request.story_prompt, request.language, request.segments, base_start, text_lang_note)}
        ]
        logger.info(f"generate_story called | provider: {request.text_llm_provider} | model: {request.text_llm_model}")
        logger.info(f"prompt messages: {json.dumps(messages, indent=4, ensure_ascii=False)}")
        response = await self._generate_response(text_llm_provider = request.text_llm_provider or None, text_llm_model = request.text_llm_model or None, messages=messages, response_format="json_object")
        response = response["list"]
        response = self.normalize_keys(response)

        logger.info(f"Generated story: {json.dumps(response, indent=4, ensure_ascii=False)}")
        # 验证响应格式
        self._validate_story_response(response)
        
        return response
    def normalize_keys(self, data):
        """
        阿里云和 openai 的模型返回结果不一致，处理一下
        修改对象中非 `text` 的键为 `image_prompt`
        - 如果是字典，替换 `text` 以外的单个键为 `image_prompt`
        - 如果是列表，对列表中的每个对象递归处理
        """
        if isinstance(data, dict):
            # 如果是字典，处理键值
            if "text" in data:
                # 找到非 `text` 的键
                other_keys = [key for key in data.keys() if key != "text"]
                # 确保只处理一个非 `text` 键的情况
                if len(other_keys) == 1:
                    data["image_prompt"] = data.pop(other_keys[0])
                elif len(other_keys) > 1:
                    raise ValueError(f"Unexpected extra keys: {other_keys}. Only one non-'text' key is allowed.")
            return data
        elif isinstance(data, list):
            # 如果是列表，递归处理每个对象
            return [self.normalize_keys(item) for item in data]
        else:
            raise TypeError("Input must be a dict or list of dicts")
        
    def generate_image(self, 
                       *, 
                       prompt: str, 
                       image_llm_provider: str = None, 
                       image_llm_model: str = None, 
                       resolution: str = "1024x1024",
                       task_dir: str = None,
                       segment_index: int = 1,
                       **img2img_kwargs  # ← add this to catch init_image_base64, strength, mask, etc.
                       ) -> str:
        # return "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/1d/56/20250118/3c4cc727/4fc622b5-54a6-484c-bf1f-f1cfb66ace2d-1.png?Expires=1737290655&OSSAccessKeyId=LTAI5tQZd8AEcZX6KZV4G8qL&Signature=W8D4CN3uonQ2pL1e9xGMWufz33E%3D"
        """生成图片

        Args:
            prompt (str): 图片描述
            resolution (str): 图片分辨率，默认为 1024x1024

        Returns:
            str: 图片URL
        """

        
        image_llm_provider =  image_llm_provider or settings.image_provider
        image_llm_model = image_llm_model or settings.image_llm_model

        logger.info(f"generate_image called | provider: {image_llm_provider} | model: {image_llm_model} | resolution: {resolution}")
        
        try:
            # 添加安全提示词
            safe_prompt = f"Create a safe, family-friendly illustration. {prompt} The image should be appropriate for all ages, non-violent, and non-controversial."
            
            if image_llm_provider == "aliyun":
                rsp = ImageSynthesis.call(model=image_llm_model,
                              prompt=prompt,
                              size=resolution,)
                if rsp.status_code == HTTPStatus.OK:
                    # print("aliyun image response", rsp.output)
                    for result in rsp.output.results:
                        return result.url
                else:
                    error_message = f'Failed, status_code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}'
                    logger.error(error_message)
                    raise Exception(error_message)
            elif image_llm_provider == "openai":
                if (resolution != None):
                    resolution = resolution.replace("*", "x")
                response = self.openai_client.images.generate(
                    model=image_llm_model,
                    prompt=safe_prompt,
                    size=resolution,
                    quality="standard",
                    n=1
                )
                logger.info("image generate res", response.data[0].url)
                return response.data[0].url
            elif image_llm_provider == "siliconflow":
                if (resolution != None):
                    resolution = resolution.replace("*", "x")
                payload = {
                    "model": image_llm_model,
                    "prompt": safe_prompt,
                    "seed": random.randint(1000000, 4999999999),
                    "image_size": resolution,
                    "guidance_scale": 7.5,
                    "batch_size": 1,
                }
                headers = {
                    "Authorization": "Bearer " + settings.siliconflow_api_key,
                    "Content-Type": "application/json"
                }
                response = requests.request("POST", "https://api.siliconflow.cn/v1/images/generations", json=payload, headers=headers)
                if response.text != None:
                    response = json.loads(response.text)
                    return response["images"][0]["url"]
                else:
                    raise Exception(response.text)
            elif image_llm_provider == "huggingface":
                logger.info("Entering Hugging Face Inference branch")
                if not image_llm_model:
                    image_llm_model = settings.image_llm_model or "black-forest-labs/FLUX.1-dev"
                
                headers = {
                    "Authorization": f"Bearer {settings.huggingface_api_key}",
                    "Content-Type": "application/json"
                }
                # Parse resolution
                try:
                    w_str, h_str = resolution.split("*")
                    width = int(w_str.strip())
                    height = int(h_str.strip())
                except:
                    width, height = 1024, 1024
                    logger.warning(f"Invalid resolution '{resolution}', using 1024x1024")

                payload = {
                    "inputs": safe_prompt,
                    "parameters": {
                        "negative_prompt": "blurry, ugly, deformed, low quality, extra limbs",
                        "num_inference_steps": 28,
                        "guidance_scale": 3.5,
                        "width": width,
                        "height": height,
                        "seed": random.randint(1, 2147483647)
                    }
                }
                logger.info(f"HF FLUX payload: {payload}")
                try:
                    response = requests.post(
                        f"https://api-inference.huggingface.co/models/{image_llm_model}",
                        headers=headers,
                        json=payload
                    )
                    if response.status_code == 200:
                        # HF returns image bytes directly
                        image_bytes = response.content
                        task_path = Path(task_dir)
                        task_path.mkdir(parents=True, exist_ok=True)
                        image_filename = f"{segment_index}.png"
                        image_path = task_path / image_filename
                        image_path.write_bytes(img_bytes)
                        
                        logger.info(f"HF FLUX image saved to: {image_path}")
                        return str(image_path)
                    else:
                        error_text = response.text[:500]
                        logger.error(f"HF error {response.status_code}: {error_text}")
                        return ""
                except Exception as e:
                    logger.error(f"HF Inference failed: {str(e)}", exc_info=True)
                    return ""
            elif image_llm_provider == "nvidia":
                logger.info("Entering NVIDIA SD3-medium branch")
                
                if not image_llm_model:
                    image_llm_model = settings.image_llm_model or "stabilityai/stable-diffusion-3-medium"
                
                invoke_url = f"https://ai.api.nvidia.com/v1/genai/{image_llm_model}"
                
                headers = {
                    "Authorization": f"Bearer {settings.nvidia_api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
                
                # Parse resolution to aspect_ratio (SD3 uses "16:9", "9:16", "1:1", etc.)
                try:
                    w_str, h_str = resolution.split("*")
                    w, h = int(w_str.strip()), int(h_str.strip())
                    if w == h:
                        aspect = "1:1"
                    elif w > h:
                        aspect = "16:9" if w / h > 1.7 else "3:2"
                    else:
                        aspect = "9:16" if h / w > 1.7 else "2:3"
                except:
                    aspect = "1:1"
                    logger.warning(f"Invalid resolution '{resolution}', using 1:1")
                
                payload = {
                    "prompt": safe_prompt,
                    "negative_prompt": "blurry, low quality, deformed, ugly",
                    "cfg_scale": 5.0,
                    "aspect_ratio": aspect,
                    "seed": random.randint(1, 2147483647),
                    "steps": 40,  # 30-50 typical for SD3-medium
                }
                
                logger.info(f"SD3 payload: {payload}")
                
                try:
                    result = call_flux(invoke_url, headers, payload)  # your retry-wrapped function
                    
                    logger.info(f"SD3 success: {result}")
                    
                    # Handle base64 response (common for SD3-medium on NVIDIA)
                    if "image" in result and isinstance(result["image"], str):
                        base64_str = result["image"]
                        
                        # Decode base64 to bytes
                        import base64
                        try:
                            img_bytes = base64.b64decode(base64_str)
                        except Exception as decode_err:
                            logger.error(f"Base64 decode failed: {decode_err}")
                            return ""

                        task_path = Path(task_dir)
                        task_path.mkdir(parents=True, exist_ok=True)
                        image_filename = f"{segment_index}.png"
                        image_path = task_path / image_filename
                        image_path.write_bytes(img_bytes)
                        
                        logger.info(f"SD3 base64 saved as PNG: {image_path}")
                        return str(image_path)
                    
                    # Fallback if somehow URL format
                    elif "images" in result and result["images"]:
                        return result["images"][0].get("url") or ""
                    elif "image" in result and isinstance(result["image"], dict) and "url" in result["image"]:
                        return result["image"]["url"]
                    
                    else:
                        logger.error(f"Unexpected SD3 response format: {result}")
                        return ""
                
                except Exception as e:
                    logger.error(f"SD3 failed: {str(e)}")
                    return ""
            elif image_llm_provider == "nvidia-flux-notwork":
                logger.info("Entering NVIDIA FLUX custom branch")
                if not image_llm_model:
                    image_llm_model = settings.image_llm_model  # fallback to env setting
                
                # FLUX.1-dev uses custom NIM endpoint, not OpenAI-compatible images.generate()
                invoke_url = f"https://ai.api.nvidia.com/v1/genai/{image_llm_model}"
                
                headers = {
                    "Authorization": f"Bearer {settings.nvidia_api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
                
                # Parse resolution like "1080*1920" or "1920*1080" or "1024*1024"
                try:
                    width_str, height_str = resolution.split("*")
                    width = int(width_str.strip())
                    height = int(height_str.strip())
                except Exception:
                    # fallback to square if parsing fails
                    width, height = 1024, 1024
                    logger.warning(f"Invalid resolution format '{resolution}', using 1024x1024")
                
                payload = {
                    "prompt": safe_prompt,
                    "mode": "base",           # or "turbo" if you want faster but lower quality
                    "cfg_scale": 3.5,         # 3.5~7.0 recommended range for FLUX
                    "width": width,
                    "height": height,
                    "seed": random.randint(1, 2147483647),   #random.randint(0, 2147483647),  large range for variety
                    "steps": 28               # 20~50, 28 is good balance of speed & quality
                }
                logger.info(f"FLUX payload sent: {payload}")
                try:
                    result = call_flux(invoke_url, headers, payload)
                    logger.info(f"FLUX success: {result}")
                     # Different FLUX versions may return slightly different structures
                    # Try common patterns
                    if "images" in result and result["images"]:
                        # Most common format
                        return result["images"][0]["url"]
                    elif "image" in result and "url" in result["image"]:
                        return result["image"]["url"]
                    elif "image_url" in result:
                        return result["image_url"]
                    else:
                        logger.error(f"Unexpected FLUX response format: {result}")
                        return ""
                except requests.exceptions.RequestException as e:
                    logger.error(f"FLUX generation failed after retried: {str(e)}")
                    return ""
                except Exception as e:
                    logger.error(f"Unexpected error in NVIDIA FLUX: {str(e)}")
                    return ""
            elif image_llm_provider == "cloudflare":
                logger.info("Entering Cloudflare Workers AI branch")
                
                if not image_llm_model:
                    image_llm_model = settings.image_llm_model or "@cf/runwayml/stable-diffusion-v1-5-inpainting"
                
                api_base_url = f"{settings.cloudflare_base_url}/{settings.cloudflare_account_id}/ai/run/"
                headers = {
                    "Authorization": f"Bearer {settings.cloudflare_api_key}",
                    "Content-Type": "application/json"
                }
                
                # Parse resolution (inpainting model expects same size as input image)
                try:
                    width_str, height_str = resolution.split("*")
                    width = int(width_str.strip())
                    height = int(height_str.strip())
                except:
                    width, height = 1024, 1024
                    logger.warning(f"Invalid resolution '{resolution}', using 1024x1024")
                
                # For inpainting: need an input image + mask
                # Assume you have first image as reference (base64 from previous generation)
                # If no init_image, fallback to text-to-image mode (use a different model)
                img2img_kwargs = img2img_kwargs or {}  # ensure dict, never None
                if "init_image_base64" in img2img_kwargs and img2img_kwargs["init_image_base64"]:
                    # Validate base64 first
                    base64_str = img2img_kwargs["init_image_base64"]

                    if not base64_str or len(base64_str) < 100:
                        raise ValueError("init_image_base64 is empty or too short")  
                    logger.info(f"init_image_base64, size: {len(base64_str)} bytes")
                    # Inpainting mode
                    logger.info("Inpainting mode - attempting to use previous image")
                    try:
                        import base64
                        image_bytes = base64.b64decode(img2img_kwargs["init_image_base64"])

                        logger.info(f"Decoded init_image_base64 successfully, size: {len(image_bytes)} bytes")

                        # Create simple mask (white rectangle in center for demo - customize!)
                        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                        mask = Image.new("L", img.size, 0)  # black mask
                        width, height = img.size
                        draw = ImageDraw.Draw(mask)
                        draw.rectangle((width//4, height//4, width*3//4, height*3//4), fill=255)  # white center
                        
                        mask_bytes = io.BytesIO()
                        mask.save(mask_bytes, format="PNG")
                        mask_bytes = mask_bytes.getvalue()
                        
                        payload = {
                            "prompt": f"""
                                    Using the attached image as a strict visual reference:

                                    - Keep the same characters, faces, body proportions, clothing, and skin tones
                                    - Keep the same background, environment, lighting, camera angle, and art style
                                    - Do NOT change character identity or scene composition

                                    Only make the following changes:
                                    {prompt}
                                    The result should look like the same moment in the same scene,
                                    with subtle action changes or added details,
                                    not a new illustration.

                                    Style: consistent, cohesive, high visual continuity
                                    Content: safe, family-friendly, non-violent, appropriate for all ages
                                    """,
                            "negative_prompt": """
                                blurry, low quality, deformed, 
                                different character, different face, different body,
                                changed background, new environment,
                                distorted anatomy, wide body, short body,
                                stretched proportions, deformed limbs,
                                style change, camera change, perspective change
                                """,
                            "image": list(image_bytes),  # array of bytes
                            "mask": list(mask_bytes),    # array of bytes
                            "num_steps": 20,
                            "strength": 0.3,            # how much to change masked area
                            "seed": random.randint(1, 2147483647)
                        }
                    except base64.binascii.Error as b64_err:
                        logger.error(f"Invalid base64 in init_image_base64: {b64_err}")
                        logger.warning("Falling back to text-to-image due to bad base64")
                        # continue with text-only payload (remove image/mask)
                        payload.pop("image", None)
                        payload.pop("mask", None)
                    
                    except Exception as prep_err:
                        logger.error(f"Inpainting prep failed: {str(prep_err)}", exc_info=True)
                        logger.warning("Falling back to text-to-image")    
                else:
                    # Fallback to text-to-image (use a different model if needed)
                    logger.warning("No init_image provided - falling back to text-to-image")
                    # Switch to text model or use same model without image/mask
                    payload = {
                        "prompt": safe_prompt,
                        "negative_prompt": "blurry, low quality",
                        "width": width,
                        "height": height,
                        "num_steps": 20,
                        "seed": random.randint(1, 2147483647)
                    }
                    image_llm_model = "@cf/stabilityai/stable-diffusion-xl-base-1.0"  # text-to-image model
                
                logger.info(f"Cloudflare payload keys: {list(payload.keys())}")
                
                try:
                    response = requests.post(f"{api_base_url}{image_llm_model}", headers=headers, json=payload, timeout=180)
                    logger.info(f"Cloudflare status: {response.status_code}")
                    
                    content_type = response.headers.get("content-type", "").lower()
                    logger.info(f"Cloudflare content_type: {content_type}")
                    if response.status_code == 200:
                        if "image/" in content_type:  # image/png, image/jpeg, etc.
                            # Binary image response - this is the SUCCESS case for image models
                            img_bytes = response.content
                            
                            task_path = Path(task_dir)
                            task_path.mkdir(parents=True, exist_ok=True)
                            image_filename = f"{segment_index}.png"
                            image_path = task_path / image_filename
                            image_path.write_bytes(img_bytes)
                            
                            logger.info(f"Cloudflare binary image saved: {image_path} (content-type: {content_type})")
                            return str(image_path)
                        
                        elif "application/json" in content_type:
                            # JSON response - only for errors or text models
                            try:
                                result = response.json()
                                logger.info(f"Cloudflare JSON response: {result}")
                                
                                if "result" in result and isinstance(result["result"], str):
                                    base64_str = result["result"]
                                    img_bytes = base64.b64decode(base64_str)
                                    
                                    task_path = Path(task_dir)
                                    task_path.mkdir(parents=True, exist_ok=True)
                                    image_filename = f"scene_{segment_index}.png"
                                    image_path = task_path / image_filename
                                    image_path.write_bytes(img_bytes)
                                    
                                    logger.info(f"Cloudflare base64 image saved: {image_path}")
                                    return str(image_path)
                                else:
                                    logger.error(f"Unexpected JSON format: {result}")
                                    return ""
                            except json.JSONDecodeError as json_err:
                                logger.error(f"JSON decode failed on Cloudflare response: {json_err}")
                                logger.error(f"Raw response body preview: {response.text[:500]}")
                                return ""
                        
                        else:
                            logger.error(f"Unknown content-type: {content_type}")
                            logger.error(f"Raw response preview: {response.text[:500]}")
                            return ""
                    
                    else:
                        error_text = response.text[:500]
                        logger.error(f"Cloudflare error {response.status_code}: {error_text}")
                        return ""
                except Exception as e:
                    logger.error(f"Cloudflare generation failed: {str(e)}")
                    return ""
        except Exception as e:
            logger.error(f"Failed to generate image: {e}")
            return ""

    async def generate_story_with_images(self, request: StoryGenerationRequest, task_id: str = None, task_dir: str = None) -> List[Dict[str, Any]]:
        """生成故事和配图
        Args:
            story_prompt (str, optional): 故事提示. Defaults to None.
            language (Language, optional): 语言. Defaults to Language.CHINESE.
            segments (int, optional): 故事分段数. Defaults to 3.

        Returns:
            List[Dict[str, Any]]: 故事场景列表，每个场景包含文本、图片提示词和图片URL
        """
        # 先生成故事
        story_segments = await self.generate_story(
            request,
        )

        # Create task dir early (or receive from upper caller)
        if not task_dir:
            task_id = str(int(time.time() * 1000))
            task_dir = str(Path("/app/tasks") / task_id)
            Path(task_dir).mkdir(parents=True, exist_ok=True)

        previous_base64 = None

        # 为每个场景生成图片
        for idx, segment in enumerate(story_segments, 1):
            logger.info(f"Wait for 2 mins, free api have concurrency limits")
            time.sleep(120)
            logger.info(f"2 mins passed")
            
            try:
                img2img_kwargs = {"init_image_base64": previous_base64} if previous_base64 else {}
                
                image_url = self.generate_image(
                    prompt=segment["image_prompt"], 
                    resolution=request.resolution, 
                    image_llm_provider=request.image_llm_provider, 
                    image_llm_model=request.image_llm_model,
                    task_dir=str(task_dir),           # pass task dir
                    segment_index=idx,                 # pass 1-based index
                    **img2img_kwargs
                )
                segment["url"] = image_url
                if image_url:
                    try:
                        if os.path.exists(image_url):
                            with open(image_url, "rb") as f:
                                previous_base64 = base64.b64encode(f.read()).decode()
                            logger.info(f"Encode previous_base64 successfully, size: {len(previous_base64)} bytes")
                        else:
                            parsed = urlparse(image_url)
                            if parsed.scheme in ("http", "https"):
                                resp = requests.get(image_url, timeout=30)
                                resp.raise_for_status()
                                previous_base64 = base64.b64encode(resp.content).decode()
                                logger.info(f"Downloaded + encoded previous_base64, size: {len(previous_base64)} bytes")
                            else:
                                logger.warning(f"previous_base64 skipped, unknown path: {image_url}")
                    except Exception as e:
                        logger.warning(f"Failed to encode previous_base64: {e}")
            except Exception as e:
                logger.error(f"Failed to generate image for segment: {e}")
                segment["url"] = None

        return story_segments
    
    def get_llm_providers(self) -> Dict[str, List[str]]:
        imgLLMList = []
        textLLMList = []
        if settings.openai_api_key:
            textLLMList.append("openai")
            imgLLMList.append("openai")
        if settings.aliyun_api_key:
            textLLMList.append("aliyun")
            imgLLMList.append("aliyun")
        if settings.deepseek_api_key:
            textLLMList.append("deepseek")
        if settings.ollama_api_key:
            textLLMList.append("ollama")
        if settings.siliconflow_api_key:
            textLLMList.append("siliconflow")
            imgLLMList.append("siliconflow")
        if settings.nvidia_api_key:                             # ← new
            textLLMList.append("nvidia")
            imgLLMList.append("nvidia")
        if settings.cloudflare_api_key:                             # ← new
            textLLMList.append("cloudflare")
            imgLLMList.append("cloudflare")
        
        return { "textLLMProviders": textLLMList, "imageLLMProviders": imgLLMList, "text_llm_model": settings.text_llm_model, "image_llm_model": settings.image_llm_model, "resolution": settings.image_resolution }

    def _validate_story_response(self, response: any) -> None:
        """验证故事生成响应

        Args:
            response: LLM 响应

        Raises:
            LLMResponseValidationError: 响应格式错误
        """
        if not isinstance(response, list):
            raise LLMResponseValidationError("Response must be an array")

        for i, scene in enumerate(response):
            if not isinstance(scene, dict):
                raise LLMResponseValidationError(f"story item {i} must be an object")
            
            if "text" not in scene:
                raise LLMResponseValidationError(f"Scene {i} missing 'text' field")
            
            if "image_prompt" not in scene:
                raise LLMResponseValidationError(f"Scene {i} missing 'image_prompt' field")
            
            if not isinstance(scene["text"], str):
                raise LLMResponseValidationError(f"Scene {i} 'text' must be a string")
            
            if not isinstance(scene["image_prompt"], str):
                raise LLMResponseValidationError(f"Scene {i} 'image_prompt' must be a string")

    async def _generate_response(self, *, text_llm_provider: str = None, text_llm_model: str = None, messages: List[Dict[str, str]], response_format: str = "json_object") -> any:
        """生成 LLM 响应

        Args:
            messages: 消息列表
            response_format: 响应格式，默认为 json_object

        Returns:
            Dict[str, Any]: 解析后的响应

        Raises:
            Exception: 请求失败或解析失败时抛出异常
        """
        logger.info(f"Sending chat completion request | provider={text_llm_provider} | model={text_llm_model}")
        if text_llm_provider == None:
            text_llm_provider = settings.text_llm_provider
        if text_llm_provider == "aliyun":
            text_client = self.aliyun_text_client
        elif text_llm_provider == "openai":
            text_client = self.openai_client
        elif text_llm_provider == "deepseek":
            text_client = deepseek_client
        elif text_llm_provider == "ollama":
            text_client = ollama_client
        elif text_llm_provider == "siliconflow":
            text_client = siliconflow_client
        elif text_llm_provider == "nvidia":
            text_client = nvidia_client
        elif text_llm_provider == "nvidia2":                     # ← new
            # Special handling for large models like 405B on NIM endpoint
            invoke_url = f"https://integrate.api.nvidia.com/v1"  # or check exact path in NVIDIA console
            headers = {
                "Authorization": f"Bearer {settings.nvidia_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "model": text_llm_model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "response_format": {"type": response_format}
            }

            try:
                logger.info(f"Sending NIM request to {invoke_url}")
                response = requests.post(invoke_url, headers=headers, json=payload, timeout=300)
                logger.info(f"NIM HTTP status: {response.status_code}")
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                logger.info(f"Parsed result: {parsed}")
                return parsed
            except Exception as e:
                logger.error(f"NIM chat failed: {str(e)}", exc_info=True)
                raise
        elif text_llm_provider == "cloudflare": 
            logger.info(f"todo...........................................")
        if text_llm_model == None:
            text_llm_model = settings.text_llm_model
        
        
        try:
            response = text_client.chat.completions.create(
                model= text_llm_model,
                response_format={"type": response_format},
                messages=messages,
                temperature=0.7,
                max_tokens=2048,
                timeout=180
            )
            logger.info(f"Raw API response received: {response}")
            content = response.choices[0].message.content
            result = json.loads(content)
            logger.info(f"LLM full response: {result}")
            return result
        except Exception as e:
            logger.error(f"LLM call failed | provider={text_llm_provider} | model={text_llm_model}", exc_info=True)
            logger.error(f"Failed to parse response: {e}")
            raise e

    async def _get_story_prompt(self, story_prompt: str = None, language: Language = Language.CHINESE_CN, segments: int = 3, base_start: str = "讲一个故事，主题是：", text_lang_note: str = "written in Chinese (简体中文)") -> str:
        """生成故事提示词

        Args:
            story_prompt (str, optional): 故事提示. Defaults to None.
            segments (int, optional): 故事分段数. Defaults to 3.

        Returns:
            str: 完整的提示词
        """

        languageValue = LANGUAGE_NAMES[language]

        if story_prompt:
            base_prompt = f"{base_start}{story_prompt}"
        else:
            base_prompt = base_start.rstrip("：")  # clean up
        
        # Dynamic wording for 1 segment vs multiple
        if segments == 1:
            segment_instruction = "The story must be told in exactly one single scene."
            array_note = "The 'list' array must contain exactly one object."
        else:
            segment_instruction = f"The story needs to be divided into {segments} scenes"
            array_note = f"The 'list' array must contain exactly {segments} objects."
            
        return f"""
        {base_prompt}. {segment_instruction}, and each scene must include descriptive text and an image prompt.

        Please return the result in the following JSON format, where the key `list` contains an array of objects:

        **Expected JSON format**:
        {{
            "list": [
                {{
                    "text": "Descriptive text for the scene",
                    "image_prompt": "Detailed image generation prompt, described in English"
                }}
                ,// ... {segments-1} more if segments > 1
            ]
        }}

        **Requirements**:
        1. The root object must contain a key named `list`, and its value must be an array of scene objects.
        2. Each object in the `list` array must include:
            - `text`: A descriptive text for the scene, written in {languageValue}.
            - `image_prompt`: A detailed prompt for generating an image, written in English.
        3. Ensure the JSON format matches the above example exactly. Avoid extra fields or incorrect key names like `cimage_prompt` or `inage_prompt`.
        {array_note}

        **Important**:
        - Do not include explanations, comments, markdown code blocks, or any text outside the JSON.
        - Output **only** the valid JSON object.
        """

    

# 创建服务实例
llm_service = LLMService()
