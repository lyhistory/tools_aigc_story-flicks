import base64
import time
import os
import re
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

    @staticmethod
    def _strip_invisible_chars(text: str) -> str:
        if not text:
            return text
        cleaned = str(text)
        # Remove literal escaped zero-width sequences if they were passed as raw text.
        cleaned = (
            cleaned.replace("\\u200b", "")
            .replace("\\u200c", "")
            .replace("\\u200d", "")
            .replace("\\ufeff", "")
            .replace("\\u2060", "")
            .replace("\\u00ad", "")
        )
        # Remove actual invisible unicode control/format chars.
        cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060\u00ad]", "", cleaned)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return cleaned

    @staticmethod
    def _collapse_prompt_whitespace(text: str) -> str:
        if not text:
            return text
        return re.sub(r"\s+", " ", str(text)).strip()

    async def _normalize_story_prompt(
        self,
        story_prompt: str,
        language: Language = Language.CHINESE_CN,
        text_llm_provider: str = None,
        text_llm_model: str = None,
    ) -> str:
        cleaned = self._collapse_prompt_whitespace(
            self._strip_invisible_chars((story_prompt or "")).strip()
        )
        if not cleaned:
            return cleaned

        language_name = LANGUAGE_NAMES.get(language, "the same language as the input")
        messages = [
            {
                "role": "system",
                "content": (
                    "You improve user-entered topic text. "
                    "If grammar is incorrect or phrasing sounds non-native, rewrite it naturally. "
                    "If it is already natural and correct, keep the meaning unchanged. "
                    "Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Rewrite the topic only when needed.\n"
                    "Return strictly JSON: {\"corrected_prompt\":\"...\"}\n"
                    "Rules:\n"
                    "1. Preserve original meaning and scope.\n"
                    "2. Keep numbers, ranges, and sequence terms intact whenever possible.\n"
                    "3. Do not add examples, teaching content, or extra context.\n"
                    f"4. Output in {language_name}.\n"
                    f"Topic: {cleaned}"
                ),
            },
        ]
        try:
            result = await self._generate_response(
                text_llm_provider=text_llm_provider,
                text_llm_model=text_llm_model,
                messages=messages,
                response_format="json_object",
            )
            if isinstance(result, dict):
                candidate = (
                    result.get("corrected_prompt")
                    or result.get("corrected_topic")
                    or result.get("prompt")
                    or result.get("text")
                )
                if isinstance(candidate, str):
                    normalized = self._collapse_prompt_whitespace(
                        self._strip_invisible_chars(candidate).strip()
                    )
                    if normalized:
                        if normalized != cleaned:
                            logger.info(f"story_prompt normalized: '{cleaned}' -> '{normalized}'")
                        return normalized
            logger.warning(f"story_prompt normalization returned unexpected payload: {result}")
        except Exception as e:
            logger.warning(f"story_prompt normalization failed, using original prompt: {e}")
        return cleaned

    @staticmethod
    def _word_number_to_int(token: str) -> int | None:
        if token is None:
            return None
        raw = str(token).strip().lower()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        raw = raw.replace("-", " ")
        raw = re.sub(r"\band\b", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            return None
        units = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19,
        }
        tens = {
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
        }
        total = 0
        current = 0
        for part in raw.split():
            if part in units:
                current += units[part]
            elif part in tens:
                current += tens[part]
            elif part == "hundred":
                if current == 0:
                    current = 1
                current *= 100
            elif part == "thousand":
                if current == 0:
                    current = 1
                total += current * 1000
                current = 0
            else:
                return None
        return total + current

    @staticmethod
    def _int_to_english(n: int) -> str:
        if n < 0:
            return str(n)
        if n == 0:
            return "zero"
        units = [
            "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen",
        ]
        tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

        def under_1000(x: int) -> str:
            parts = []
            if x >= 100:
                parts.append(f"{units[x // 100]} hundred")
                x %= 100
            if x >= 20:
                t = tens[x // 10]
                u = x % 10
                parts.append(f"{t}-{units[u]}" if u else t)
            elif x > 0:
                parts.append(units[x])
            return " ".join(parts).strip()

        if n < 1000:
            return under_1000(n)
        if n < 1_000_000:
            thousands = n // 1000
            rem = n % 1000
            if rem:
                return f"{under_1000(thousands)} thousand {under_1000(rem)}".strip()
            return f"{under_1000(thousands)} thousand".strip()
        return str(n)

    @staticmethod
    def _is_ordered_sequence_prompt(story_prompt: str) -> bool:
        lower = (story_prompt or "").lower()
        ordered_tokens = ["count", "counting", "number", "numbers", "month", "months", "weekday", "weekdays", "days of week"]
        if any(token in lower for token in ordered_tokens):
            return True
        if re.search(r"\b(\d{1,4})\s*(?:to|-)\s*(\d{1,4})\b", lower):
            return True
        for m in re.finditer(r"\b([a-z][a-z\s-]{0,40}?)\s*(?:to|-)\s*([a-z][a-z\s-]{0,40}?)\b", lower):
            a = LLMService._word_number_to_int(m.group(1))
            b = LLMService._word_number_to_int(m.group(2))
            if a is not None and b is not None:
                return True
        return False

    @staticmethod
    def _build_sequence_items(story_prompt: str) -> List[str]:
        prompt = (story_prompt or "").strip()
        lower = prompt.lower()
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        category_items = {
            "fruit": [
                "apple", "banana", "orange", "grape", "strawberry", "watermelon", "pear", "peach",
                "pineapple", "mango", "kiwi", "cherry", "blueberry", "lemon", "tomato",
            ],
            "vegetable": [
                "carrot", "potato", "tomato", "cucumber", "broccoli", "spinach", "onion", "pepper",
                "corn", "cabbage", "pumpkin", "eggplant", "lettuce", "pea", "bean",
            ],
            "tree": [
                "oak tree", "pine tree", "maple tree", "palm tree", "apple tree", "cherry tree", "willow tree",
                "birch tree", "bamboo", "fir tree", "cedar tree", "spruce tree",
            ],
            "houseware": [
                "cup", "plate", "spoon", "fork", "knife", "bowl", "bottle", "kettle", "pan", "pot",
                "chair", "table", "lamp", "clock", "blanket",
            ],
        }

        if any(k in lower for k in ["month", "months"] + [m.lower() for m in months]):
            return months
        if any(k in lower for k in ["weekday", "weekdays", "days of week"] + [d.lower() for d in weekdays]):
            return weekdays

        range_match = re.search(r"\b(\d{1,4})\s*(?:to|-)\s*(\d{1,4})\b", lower)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                max_items = 300
                return [str(n) for n in range(start, min(end, start + max_items - 1) + 1)]

        for m in re.finditer(r"\b([a-z][a-z\s-]{0,40}?)\s*(?:to|-)\s*([a-z][a-z\s-]{0,40}?)\b", lower):
            start = LLMService._word_number_to_int(m.group(1))
            end = LLMService._word_number_to_int(m.group(2))
            if start is None or end is None or start > end:
                continue
            max_items = 300
            return [LLMService._int_to_english(n) for n in range(start, min(end, start + max_items - 1) + 1)]

        if any(k in lower for k in ["fruit", "fruits"]):
            return category_items["fruit"]
        if any(k in lower for k in ["vegetable", "vegetables"]):
            return category_items["vegetable"]
        if any(k in lower for k in ["tree", "trees"]):
            return category_items["tree"]
        if any(k in lower for k in ["houseware", "housewares", "household", "kitchenware"]):
            return category_items["houseware"]

        csv_items = [p.strip() for p in re.split(r"[,\n;]+", prompt) if p.strip()]
        if len(csv_items) >= 2:
            return csv_items[:200]

        return [str(n) for n in range(1, 21)]
    
    async def generate_story(self, request: StoryGenerationRequest) -> List[Dict[str, Any]]:
        """生成故事场景
        Args:
            story_prompt (str, optional): 故事提示. Defaults to None.
            segments (int, optional): 故事分段数. Defaults to 3.

        Returns:
            List[Dict[str, Any]]: 故事场景列表
        """
        request.story_prompt = self._strip_invisible_chars((request.story_prompt or "")).strip()
        if request.story_prompt:
            request.story_prompt = await self._normalize_story_prompt(
                request.story_prompt,
                request.language,
                request.text_llm_provider or None,
                request.text_llm_model or None,
            )
        topic_type = getattr(request, "topic_type", None)
        if topic_type == "sequence":
            items = self._build_sequence_items(request.story_prompt)
            ordered_mode = self._is_ordered_sequence_prompt(request.story_prompt)

            if ordered_mode or int(getattr(request, "segments", 1) or 1) <= 1:
                script = ". ".join(items).strip()
                if script and script[-1] not in ".!?":
                    script += "."
                return [{
                    "script": script,
                    "scene_prompt": (
                        "Create a clean, minimal educational background with soft colors and no text. "
                        "No characters. No objects that distract. Keep the center area clear for overlaid words."
                    ),
                    "objects": [],
                    "topic_type": "sequence",
                }]

            scene_count = max(1, min(int(request.segments), len(items)))
            multi_segments = []
            for item in items[:scene_count]:
                word = self._strip_invisible_chars(item).strip()
                if not word:
                    continue
                article = "an" if re.match(r"^[aeiouAEIOU]", word) else "a"
                multi_segments.append(
                    {
                        "script": f"{word}.",
                        "scene_prompt": (
                            "Create a clean educational flashcard-style illustration for kids. "
                            f"Show {article} large clear {word} centered. "
                            "Simple background, no clutter, no text, no characters."
                        ),
                        "objects": [word],
                        "topic_type": "sequence",
                    }
                )
            return multi_segments or [{
                "script": "word.",
                "scene_prompt": "Create a clean educational flashcard-style illustration with one large centered object.",
                "objects": [],
                "topic_type": "sequence",
            }]

        if request.segments == 1:
            # Special case: exactly 1 segment -> skip scene-generation LLM and use normalized prompt
            logger.info("segments == 1 -> skipping scene-generation LLM, using normalized story_prompt")
            
            # Create scene prompt from story_prompt (you can customize this logic)
            scene_prompt = f"Clear, family-friendly illustration teaching: {request.story_prompt}. Suitable for children, bright colors, no violence. Include concrete plural objects."

            single_scene = {
                "script": request.story_prompt.strip(),
                "scene_prompt": scene_prompt.strip(),
                "objects": []
            }
            
            return [single_scene]
            
        if request.language == Language.CHINESE_CN:
            system_content = "你是一个专业的英语老师，擅长用适合幼儿的方式讲解英语知识。请只返回JSON格式的内容。"
            base_start = "请讲解一个英语学习话题，主题是："
            text_lang_note = "written in Chinese (简体中文)"
        else:
            system_content = "You are a professional UK English teacher who explains topics in a way that is clear, friendly, and suitable for young children. Please return only JSON format content."
            base_start = "Discuss an English learning topic about:"
            text_lang_note = "written in English (UK)"
        
        extra_requirements = ""
        sp = (request.story_prompt or "").lower()
        age_band = (getattr(request, "learner_age", None) or "3-5").strip()
        if age_band not in {"3-5", "6-8", "9-12", "13-15", "16-18"}:
            age_band = "3-5"
        is_edu_topic = (
            topic_type == "explanation"
            or any(k in sp for k in ["plural", "plurals", "grammar", "english", "learning", "teach", "lesson"])
        )
        if is_edu_topic:
            extra_requirements = """
        4. Do not invent named characters unless explicitly asked. Use generic roles like "a teacher" and "a child".
        5. Use concrete object examples when teaching (e.g., cats, apples, books). Include multiple plural examples across scenes.
        6. Each scene_prompt must explicitly mention the objects to draw and show plural counts (e.g., "two cats", "three apples").
        7. Avoid repetitive "standing child" scenes; if people appear, show them interacting with the objects.
        8. Provide an `objects` array listing the plural objects shown in the scene (e.g., ["two cats", "three apples"]).
        9. Use a different object set in each scene; do not repeat the same object across scenes.
            """
        elif topic_type == "dialogue":
            extra_requirements = """
        4. Create a short two-person dialogue, but write `script` as a single-speaker voice-over narration.
        5. Add speaker attribution in narration style (e.g., The child says, "...". Daddy says, "...".).
        6. Do not output raw alternating quotes like "..." , "..." and do not use screenplay format like Kid: ... Dad: ....
        7. The scene_prompt should show both speakers interacting naturally.
        8. Avoid forced teaching objects unless the topic explicitly needs them.
            """
        elif topic_type == "scene":
            extra_requirements = """
        4. Focus on describing a setting or moment. Keep characters minimal or optional.
        5. The scene_prompt should emphasize environment, objects, and atmosphere.
        6. Do not force a teacher or classroom unless the topic explicitly requires it.
            """
        extra_requirements += f"\n        10. Keep vocabulary and sentence complexity suitable for learner age band {age_band}."

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": await self._get_story_prompt(
                request.story_prompt,
                request.language,
                request.segments,
                base_start,
                text_lang_note,
                extra_requirements,
            )}
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
        Normalize model outputs to expected keys:
        - script (legacy: text)
        - scene_prompt (legacy: image_prompt)
        - objects (optional)
        """
        if isinstance(data, dict):
            if "text" in data and "script" not in data:
                data["script"] = data.pop("text")
            if "image_prompt" in data and "scene_prompt" not in data:
                data["scene_prompt"] = data.pop("image_prompt")
            # If only script + one other key, assume that other key is scene_prompt
            if "script" in data and "scene_prompt" not in data:
                other_keys = [key for key in data.keys() if key not in ("script", "objects", "url")]
                if len(other_keys) == 1:
                    data["scene_prompt"] = data.pop(other_keys[0])
            if "objects" not in data or data["objects"] is None:
                data["objects"] = []
            return data
        elif isinstance(data, list):
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
        prompt = self._collapse_prompt_whitespace(self._strip_invisible_chars(prompt or "").strip())

        logger.info(f"generate_image called | provider: {image_llm_provider} | model: {image_llm_model} | resolution: {resolution}")
        
        try:
            style_prefix = (
                "Bright, simple, friendly children's illustration, clean shapes, pastel colors, "
                "clear objects for teaching, no realism, no horror. "
            )
            prompt_lc = prompt.lower()
            role_constraints = ""
            role_neg_terms = []
            has_father = bool(re.search(r"\b(father|dad|daddy)\b", prompt_lc))
            has_mother = bool(re.search(r"\b(mother|mom|mommy)\b", prompt_lc))
            if has_father and not has_mother:
                role_constraints = "Important: include father only as the adult (adult man), and do not include any mother/adult woman."
                role_neg_terms.extend(["mother", "mom", "mommy", "adult woman", "female parent", "woman"])
            elif has_mother and not has_father:
                role_constraints = "Important: include mother only as the adult (adult woman), and do not include any father/adult man."
                role_neg_terms.extend(["father", "dad", "daddy", "adult man", "male parent", "man"])

            neg_style_base = (
                "photorealistic, horror, creepy, scary, gore, deformed, mutated, "
                "animal-human hybrid, extra limbs, distorted anatomy, uncanny, low quality"
            )
            neg_style = neg_style_base + (", " + ", ".join(role_neg_terms) if role_neg_terms else "")
            # 添加安全提示词
            safe_prompt = (
                "Create a safe, family-friendly illustration. "
                f"{prompt} "
                f"{role_constraints} "
                "The image should be appropriate for all ages, non-violent, and non-controversial."
            )
            safe_prompt = self._collapse_prompt_whitespace(safe_prompt)
            
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
                model_name_lc = (image_llm_model or "").lower()
                supports_image_input = any(
                    name in model_name_lc
                    for name in [
                        "inpainting",
                        "img2img",
                        "dreamshaper-8-lcm",
                    ]
                )
                wants_inpaint = bool(img2img_kwargs.get("init_image_base64")) and supports_image_input
                if wants_inpaint:
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

                        # Ensure init image matches requested resolution to avoid orientation drift
                        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                        target_w, target_h = width, height
                        logger.info(f"Init image size: {img.size} | target: {target_w}x{target_h}")
                        if img.size != (target_w, target_h):
                            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

                        # Re-encode to base64 to guarantee target orientation/size
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        init_b64 = base64.b64encode(buf.getvalue()).decode()

                        # Create a character-focused mask (center + slightly lower region)
                        mask = Image.new("L", (target_w, target_h), 0)  # black mask
                        draw = ImageDraw.Draw(mask)
                        # Ellipse around center (upper body)
                        ell_w = int(target_w * 0.6)
                        ell_h = int(target_h * 0.55)
                        ell_x0 = (target_w - ell_w) // 2
                        ell_y0 = int(target_h * 0.1)
                        draw.ellipse((ell_x0, ell_y0, ell_x0 + ell_w, ell_y0 + ell_h), fill=255)
                        # Lower rectangle (full body/legs)
                        rect_w = int(target_w * 0.7)
                        rect_h = int(target_h * 0.5)
                        rect_x0 = (target_w - rect_w) // 2
                        rect_y0 = int(target_h * 0.45)
                        draw.rectangle((rect_x0, rect_y0, rect_x0 + rect_w, rect_y0 + rect_h), fill=255)
                        
                        mask_bytes = io.BytesIO()
                        mask.save(mask_bytes, format="PNG")
                        mask_bytes = mask_bytes.getvalue()
                        
                        is_img2img = (supports_image_input and "inpainting" not in model_name_lc)
                        # Allow more change for img2img to avoid "all same" images
                        strength_value = 0.55 if is_img2img else 0.45
                        preserve_hint = ""
                        if is_img2img:
                            preserve_hint = ""
                        image_edit_prompt = (
                            f"{style_prefix} "
                            "Using the attached image as a strict visual reference. "
                            "Keep the same characters, faces, body proportions, clothing, and skin tones. "
                            "Keep the same background, environment, lighting, camera angle, and art style. "
                            "Do NOT change character identity or scene composition. "
                            f"Only make the following changes: {prompt}. "
                            f"{preserve_hint} "
                            "Focus changes on the characters (pose/action/expression), keep the background unchanged. "
                            "The result should look like the same moment in the same scene, with subtle action changes or added details, not a new illustration. "
                            "Style: consistent, cohesive, high visual continuity. "
                            "Content: safe, family-friendly, non-violent, appropriate for all ages."
                        )
                        image_edit_negative = (
                            "blurry, low quality, deformed, "
                            "different character, different face, different body, "
                            "changed background, new environment, "
                            "distorted anatomy, wide body, short body, "
                            "stretched proportions, deformed limbs, "
                            "style change, camera change, perspective change, "
                            f"{neg_style}"
                        )
                        payload = {
                            "prompt": self._collapse_prompt_whitespace(image_edit_prompt),
                            "negative_prompt": self._collapse_prompt_whitespace(image_edit_negative),
                            "image_b64": init_b64,
                            "mask": list(mask_bytes),
                            "num_steps": 20,
                            "strength": strength_value,            # how much to change
                            "guidance": 7.5,
                            "width": width,
                            "height": height,
                            "seed": random.randint(1, 2147483647)
                        }
                        if is_img2img:
                            # For img2img, try swapped width/height to counter model orientation flip
                            if "width" in payload and "height" in payload:
                                payload["width"], payload["height"] = payload["height"], payload["width"]
                                logger.info(f"img2img: swapped width/height to {payload['width']}x{payload['height']}")
                            logger.info("img2img: omitting mask; using init image size")
                        if "dreamshaper-8-lcm" in model_name_lc:
                            logger.info("dreamshaper: keeping width/height, no mask")
                        logger.info(
                            f"image-input mode | supports_image_input={supports_image_input} | "
                            f"is_img2img={is_img2img} | model={image_llm_model}"
                        )
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
                        "prompt": self._collapse_prompt_whitespace(f"{style_prefix}{safe_prompt}"),
                        "negative_prompt": self._collapse_prompt_whitespace(f"blurry, low quality, {neg_style}"),
                        "width": width,
                        "height": height,
                        "num_steps": 20,
                        "seed": random.randint(1, 2147483647)
                    }
                    # If user selected an img2img/inpainting model, switch to a text-to-image model
                    model_name_lc = (image_llm_model or "").lower()
                    if ("inpainting" in model_name_lc or "img2img" in model_name_lc) or not image_llm_model:
                        image_llm_model = "@cf/stabilityai/stable-diffusion-xl-base-1.0"  # text-to-image model
                
                safe_payload = dict(payload)
                if "image_b64" in safe_payload:
                    safe_payload["image_b64"] = f"<base64:{len(str(safe_payload['image_b64']))} chars>"
                if "mask" in safe_payload:
                    safe_payload["mask"] = f"<mask:{len(safe_payload['mask'])} bytes>"
                logger.info(f"Cloudflare payload summary: {safe_payload}")
                
                def is_wrong_orientation(image_path: Path) -> bool:
                    try:
                        with Image.open(image_path) as im:
                            w, h = im.size
                        return (width < height and w > h) or (width > height and w < h)
                    except Exception:
                        return False

                def is_black_image(image_path: Path) -> bool:
                    try:
                        with Image.open(image_path) as im:
                            extrema = im.convert("RGB").getextrema()
                        # extrema is ((minR,maxR),(minG,maxG),(minB,maxB))
                        return all(ch[1] <= 5 for ch in extrema)
                    except Exception:
                        return False

                for attempt in range(1):
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
                                
                                if is_wrong_orientation(image_path):
                                    with Image.open(image_path) as im:
                                        w, h = im.size
                                    logger.warning(f"Cloudflare wrong orientation attempt {attempt+1}: {w}x{h}")

                                if is_black_image(image_path) and is_img2img and "mask" in payload:
                                    logger.warning("Cloudflare returned black image for img2img, retrying without mask once")
                                    payload_no_mask = dict(payload)
                                    payload_no_mask.pop("mask", None)
                                    payload_no_mask["seed"] = random.randint(1, 2147483647)
                                    response = requests.post(f"{api_base_url}{image_llm_model}", headers=headers, json=payload_no_mask, timeout=180)
                                    if response.status_code == 200 and "image/" in response.headers.get("content-type", "").lower():
                                        image_path.write_bytes(response.content)
                                        logger.info("Replaced black image with retry (no mask)")

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
                                        
                                        if is_wrong_orientation(image_path):
                                            with Image.open(image_path) as im:
                                                w, h = im.size
                                            logger.warning(f"Cloudflare wrong orientation attempt {attempt+1}: {w}x{h}")

                                        if is_black_image(image_path) and is_img2img and "mask" in payload:
                                            logger.warning("Cloudflare returned black image for img2img, retrying without mask once")
                                            payload_no_mask = dict(payload)
                                            payload_no_mask.pop("mask", None)
                                            payload_no_mask["seed"] = random.randint(1, 2147483647)
                                            response = requests.post(f"{api_base_url}{image_llm_model}", headers=headers, json=payload_no_mask, timeout=180)
                                            if response.status_code == 200:
                                                content_type2 = response.headers.get("content-type", "").lower()
                                                if "image/" in content_type2:
                                                    image_path.write_bytes(response.content)
                                                    logger.info("Replaced black image with retry (no mask)")
                                                elif "application/json" in content_type2:
                                                    try:
                                                        result2 = response.json()
                                                        if "result" in result2 and isinstance(result2["result"], str):
                                                            img_bytes2 = base64.b64decode(result2["result"])
                                                            image_path.write_bytes(img_bytes2)
                                                            logger.info("Replaced black image with retry (no mask, base64)")
                                                    except Exception:
                                                        pass

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
        req_topic_type = getattr(request, "topic_type", None)
        subject = (getattr(request, "subject", None) or "").strip()
        subject = self._strip_invisible_chars(subject)
        if subject:
            topic_text = self._strip_invisible_chars((request.story_prompt or "")).strip()
            if len(topic_text) > 120:
                topic_text = topic_text[:117].rstrip() + "..."
            if topic_text:
                topic_text = re.sub(r"^(subject|topic)\s*[:\-]\s*", "", topic_text, flags=re.I).strip()
            cover_script = f"Today we're going to learn {subject}."
            cover_scene_prompt = "Create a simple, friendly title card background for kids. "
            cover_scene_prompt += f"Theme: {subject}. "
            if topic_text:
                cover_scene_prompt += f"Context: {topic_text}. "
            cover_scene_prompt += "Use large, clear lettering and clean shapes. "
            cover_scene_prompt += "Bright, warm colors, no realism, no clutter."
            cover_segment = {
                "script": cover_script,
                "scene_prompt": cover_scene_prompt,
                "objects": [],
                "keywords": [],
                "url": None,
                "is_cover": True,
                "subject": subject,
                "topic_type": req_topic_type,
            }
            story_segments = [cover_segment] + story_segments

        def soften_object_count(obj: str) -> str:
            if not obj:
                return obj
            # Replace explicit counts with "some" to avoid exact-number rendering
            return re.sub(
                r"^\\s*(\\d+|one|two|three|four|five|six|seven|eight|nine|ten)\\b\\s+",
                "some ",
                obj.strip(),
                flags=re.I,
            )

        def soften_counts_in_text(text: str) -> str:
            if not text:
                return text
            # Replace explicit counts like "two cats" with "some cats"
            return re.sub(
                r"\\b(\\d+|one|two|three|four|five|six|seven|eight|nine|ten)\\b\\s+([a-zA-Z]+)",
                r"some \\2",
                text,
                flags=re.I,
            )

        def enhance_scene_prompt_for_education(scene_text: str, scene_prompt: str, objects: list, use_exact_counts: bool, is_edu: bool) -> str:
            if not is_edu:
                return scene_prompt
            base = (scene_prompt or "").strip()
            obj_hint = ""
            objects_for_prompt = objects
            if objects and not use_exact_counts:
                objects_for_prompt = [soften_object_count(o) for o in objects]
            if objects_for_prompt:
                obj_hint = f"Objects to show: {', '.join(objects_for_prompt)}."
            count_hints = []
            if use_exact_counts:
                for obj in objects or []:
                    m = re.match(r"^\\s*(\\d+|one|two|three|four|five|six|seven|eight|nine|ten)\\b\\s*(.*)$", obj.strip(), flags=re.I)
                    if m:
                        count = m.group(1)
                        name = m.group(2).strip()
                        if name:
                            count_hints.append(f"exactly {count} {name}, no more, no fewer")
            guidance = (
                "Include clear visual examples of plural objects mentioned in the scene text "
                "or objects list (e.g., two cats, three apples, several books). "
                "Avoid a single child standing alone; if people appear, show a teacher/adult explaining with the objects visible. "
                "Keep the background relevant to a learning setting."
            )
            count_hint_text = ""
            if count_hints:
                count_hint_text = "Quantity emphasis: " + "; ".join(count_hints) + "."
            return f"{base}\n\n{obj_hint}\n{count_hint_text}\n{guidance}".strip()

        def add_role_hints(scene: dict):
            script = (scene.get("script", "") or "").lower()
            hints = []
            if "father" in script or "dad" in script:
                hints.append("Include exactly one parent: the father (adult man).")
                hints.append("Do not include mother, mom, mommy, or any adult woman.")
            if "mother" in script or "mom" in script:
                hints.append("Include exactly one parent: the mother (adult woman).")
                hints.append("Do not include father, dad, daddy, or any adult man.")
            if "teacher" in script:
                hints.append("Include a teacher (adult).")
            if "boy" in script:
                hints.append("Include a boy.")
            if "girl" in script:
                hints.append("Include a girl.")
            if hints:
                scene["scene_prompt"] = self._collapse_prompt_whitespace(
                    f"{scene.get('scene_prompt', '')}. {' '.join(hints)}"
                )

        def to_voiceover_dialogue(script: str) -> str:
            if not script:
                return script
            text = script.strip()
            if re.search(r"\b(said|says|asked|asks|replied|replies|told|tells)\b", text, flags=re.I):
                return text

            def with_end_punct(s: str) -> str:
                s = s.strip().strip('"').strip("'")
                if not s:
                    return s
                if s[-1] not in ".!?":
                    s += "."
                return s

            # Fallback for raw quote-only dialogue
            quotes = re.findall(r"[\"“”']([^\"“”']{2,180})[\"“”']", text)
            quotes = [with_end_punct(q) for q in quotes if q and q.strip()]
            if len(quotes) >= 2:
                return f'The child says, "{quotes[0]}" Daddy says, "{quotes[1]}"'
            if len(quotes) == 1:
                return f'The child says, "{quotes[0]}"'

            # Fallback for screenplay-style dialogue (Kid: ... Dad: ...)
            parts = re.findall(r"\b([A-Za-z ]{2,20})\s*:\s*([^:]+?)(?=(?:\b[A-Za-z ]{2,20}\s*:)|$)", text)
            lines = []
            for speaker, utter in parts[:2]:
                role = "the child"
                sp = speaker.strip().lower()
                if any(k in sp for k in ["dad", "daddy", "father"]):
                    role = "Daddy"
                elif any(k in sp for k in ["mom", "mommy", "mother", "mum"]):
                    role = "Mummy"
                elif "teacher" in sp:
                    role = "the teacher"
                u = with_end_punct(utter)
                if u:
                    lines.append(f'{role} says, "{u}"')
            if lines:
                return " ".join(lines)
            return text

        def ensure_unique_objects(scene: dict, idx: int, used_objects: set) -> list:
            fallback_objects = [
                "two cats",
                "three apples",
                "four books",
                "five toys",
                "six pencils",
                "seven balls",
                "eight cars",
                "nine dogs",
                "ten stars",
            ]
            objs = scene.get("objects") or []
            # Normalize to list of strings
            objs = [str(o).strip() for o in objs if str(o).strip()]
            # If empty or repeats, assign a new one
            if not objs:
                for item in fallback_objects:
                    if item not in used_objects:
                        objs = [item]
                        break
            else:
                # If any object already used, replace with a new one
                if any(o in used_objects for o in objs):
                    for item in fallback_objects:
                        if item not in used_objects:
                            objs = [item]
                            break
            for o in objs:
                used_objects.add(o)
            scene["objects"] = objs
            return objs

        def ensure_objects_in_script(scene: dict, objects: list, use_exact_counts: bool):
            if not objects:
                return
            script = scene.get("script", "")
            script_lower = script.lower()
            if any(o.lower() in script_lower for o in objects):
                return
            # Append a short kid-friendly counting line
            if use_exact_counts:
                objects_phrase = ", ".join(objects)
                scene["script"] = (script + f" Let's count {objects_phrase} together!").strip()
            else:
                scene["script"] = script.strip()

        # Create task dir early (or receive from upper caller)
        if not task_dir:
            task_id = str(int(time.time() * 1000))
            task_dir = str(Path("/app/tasks") / task_id)
            Path(task_dir).mkdir(parents=True, exist_ok=True)

        async def build_variation_hint(scene_text: str, idx: int, request: StoryGenerationRequest) -> str:
            # Heuristic: extract action + emotion from the scene text
            if not scene_text:
                scene_text = ""
            text = scene_text.lower()
            action_terms = [
                "walk", "run", "look", "smile", "laugh", "point", "hold",
                "hug", "wave", "sit", "stand", "lean", "turn", "jump",
                "reach", "whisper", "nod", "shake", "kneel", "crouch",
                "step", "gesture", "talk", "speak", "listen", "giggle"
            ]
            emotion_terms = [
                "surprised", "curious", "thoughtful", "excited", "calm",
                "content", "playful", "happy", "sad", "angry", "worried",
                "confident", "shy", "proud", "nervous", "relieved"
            ]

            def find_first_term(terms):
                for term in terms:
                    if re.search(rf"\\b{re.escape(term)}\\b", text):
                        return term
                return ""

            action = find_first_term(action_terms)
            emotion = find_first_term(emotion_terms)

            if action or emotion:
                action_phrase = action if action else "subtle motion"
                emotion_phrase = emotion if emotion else "neutral"
                return f"pose/action: {action_phrase}; expression: {emotion_phrase}"

            # Fallback: ask the text LLM for a short variation hint
            try:
                messages = [
                    {
                        "role": "system",
                        "content": "You generate short visual variation hints for inpainting. Return only JSON."
                    },
                    {
                        "role": "user",
                        "content": (
                            "Given the following scene text, extract a concise pose/action and expression.\n"
                            "Return JSON: {\"pose_action\": \"...\", \"expression\": \"...\"}\n\n"
                            f"Scene text: {scene_text}"
                        ),
                    },
                ]
                result = await self._generate_response(
                    text_llm_provider=request.text_llm_provider,
                    text_llm_model=request.text_llm_model,
                    messages=messages,
                    response_format="json_object",
                )
                pose_action = (result.get("pose_action") or "").strip()
                expression = (result.get("expression") or "").strip()
                if pose_action or expression:
                    pose_action = pose_action or "subtle motion"
                    expression = expression or "neutral"
                    return f"pose/action: {pose_action}; expression: {expression}"
            except Exception as e:
                logger.warning(f"Variation hint fallback failed: {e}")

            return ""

        previous_base64 = None
        raw_age_band = (getattr(request, "learner_age", None) or "3-5").strip()
        allowed_age_bands = {"3-5", "6-8", "9-12", "13-15", "16-18"}
        age_band = raw_age_band if raw_age_band in allowed_age_bands else "3-5"

        async def extract_keywords_from_scripts(scripts: List[str], count: int = 2):
            if not scripts:
                return []
            joined = " ".join([s for s in scripts if s])
            messages = [
                {
                    "role": "system",
                    "content": "You extract 1-2 key learning words from a kids' English script. Return only JSON.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Target learner age band: {age_band}. "
                        "Pick 1-2 important learning words from the script. "
                        "Prefer action or concept words (e.g., wave, turn, count, plural, wheel) over very basic object words (e.g., bird, cat, dog). "
                        "Choose words that are slightly challenging but still age-appropriate for the target age band. "
                        "Avoid proper names and function words. "
                        "For each word, return both US and UK pronunciation (IPA) and a short kid-friendly explanation. "
                        "Use clean IPA without syllable dots (no '.' or '·') and use length mark like 'ː' when needed.\n"
                        "Return JSON: {\"keywords\":[{\"word\":\"...\",\"pronunciation_us\":\"...\",\"pronunciation_uk\":\"...\",\"explanation\":\"...\"}]}\n\n"
                        f"Script: {joined}"
                    ),
                },
            ]
            try:
                result = await self._generate_response(
                    text_llm_provider=request.text_llm_provider,
                    text_llm_model=request.text_llm_model,
                    messages=messages,
                    response_format="json_object",
                )
                keywords = result.get("keywords", [])
                if isinstance(keywords, list):
                    return keywords[:count]
            except Exception as e:
                logger.warning(f"Keyword extraction failed: {e}")
            return []

        def refine_keywords(raw_keywords: List[Dict[str, str]], scripts: List[str], count: int = 2):
            joined_text = " ".join([s for s in scripts if s]).lower()
            if not joined_text:
                return []
            age_vocab_profiles = {
                "3-5": {
                    "preferred": {
                        "wave", "wheel", "turn", "count", "color", "shape", "open", "close",
                        "happy", "sad", "fast", "slow", "up", "down", "stop", "go",
                    }
                },
                "6-8": {
                    "preferred": {
                        "compare", "group", "notice", "pattern", "before", "after", "around",
                        "through", "question", "answer", "plural", "singular", "measure",
                    }
                },
                "9-12": {
                    "preferred": {
                        "describe", "observe", "predict", "explain", "similar", "different",
                        "category", "sequence", "position", "direction", "grammar", "sentence",
                        "prefix", "suffix", "verb", "noun",
                    }
                },
                "13-15": {
                    "preferred": {
                        "analyze", "interpret", "contrast", "evidence", "summary", "inference",
                        "perspective", "context", "structure", "function", "precision",
                    }
                },
                "16-18": {
                    "preferred": {
                        "evaluate", "synthesize", "argument", "hypothesis", "nuance", "cohesion",
                        "coherence", "rhetoric", "semantics", "implication", "framework",
                    }
                },
            }
            basic_words = {
                "bird", "cat", "dog", "apple", "book", "toy", "tree", "boy", "girl",
                "child", "kids", "kid", "teacher", "father", "mother", "dad", "daddy",
                "mom", "mommy", "bus", "car", "house", "school",
            }
            universal_preferred_words = {
                "wave", "wheel", "turn", "count", "plural", "singular", "group",
                "compare", "describe", "notice", "observe", "listen", "speak", "say",
                "talk", "walk", "hold", "pull", "push", "learn", "practice",
            }
            age_preferred_words = age_vocab_profiles.get(age_band, {}).get("preferred", set())
            stop_words = {
                "the", "a", "an", "and", "or", "but", "if", "then", "that", "this",
                "those", "these", "there", "here", "with", "from", "into", "about",
                "your", "their", "our", "my", "his", "her", "its", "are", "is", "was",
                "were", "be", "been", "being", "to", "of", "in", "on", "for", "as",
            }

            def norm_word(word: str) -> str:
                return re.sub(r"[^a-z]", "", (word or "").lower())

            def word_score(w: str) -> int:
                if not w:
                    return -999
                freq = len(re.findall(rf"\b{re.escape(w)}\b", joined_text))
                score = min(4, freq)
                if w in universal_preferred_words:
                    score += 5
                if w in age_preferred_words:
                    score += 6
                if w.endswith("ing") or w.endswith("ed"):
                    score += 2
                if w in basic_words:
                    score -= 5
                if len(w) <= 3:
                    score -= 2
                if age_band in {"13-15", "16-18"} and w in {"wave", "wheel", "walk", "bird", "cat", "dog"}:
                    score -= 2
                return score

            refined = []
            seen = set()

            for kw in raw_keywords or []:
                if not isinstance(kw, dict):
                    continue
                word_raw = (kw.get("word") or "").strip()
                w = norm_word(word_raw)
                if (
                    not w
                    or w in stop_words
                    or w in basic_words
                    or w in seen
                    or len(w) < 3
                ):
                    continue
                item = {
                    "word": w,
                    "pronunciation_us": (kw.get("pronunciation_us") or "").strip(),
                    "pronunciation_uk": (kw.get("pronunciation_uk") or "").strip(),
                    "explanation": (kw.get("explanation") or "").strip(),
                    "_score": word_score(w),
                }
                refined.append(item)
                seen.add(w)

            # Fill missing slots from script tokens with a score-based fallback.
            if len(refined) < count:
                tokens = [norm_word(t) for t in re.findall(r"[A-Za-z']+", joined_text)]
                candidates = []
                first_pos = {}
                for idx, t in enumerate(tokens):
                    if t and t not in first_pos:
                        first_pos[t] = idx
                for t in set(tokens):
                    if (
                        not t
                        or t in seen
                        or t in stop_words
                        or t in basic_words
                        or len(t) < 3
                    ):
                        continue
                    candidates.append((word_score(t), -first_pos.get(t, 9999), t))
                candidates.sort(reverse=True)
                for _, _, cand in candidates:
                    refined.append(
                        {
                            "word": cand,
                            "pronunciation_us": "",
                            "pronunciation_uk": "",
                            "explanation": f"{cand} is an important word in this story.",
                            "_score": word_score(cand),
                        }
                    )
                    seen.add(cand)
                    if len(refined) >= count:
                        break

            refined.sort(key=lambda x: x.get("_score", 0), reverse=True)
            out = []
            for item in refined[:count]:
                clean_item = dict(item)
                clean_item.pop("_score", None)
                out.append(clean_item)
            return out

        # Extract 1-2 keywords for the whole video (skip for sequence mode)
        keywords = []
        script_list = [self._strip_invisible_chars(s.get("script", "")) for s in story_segments if not s.get("is_cover")]
        if req_topic_type != "sequence":
            keywords = await extract_keywords_from_scripts(
                script_list,
                count=2,
            )
            keywords = refine_keywords(keywords, script_list, count=2)
            if not keywords:
                # Simple fallback: pick first verb-like word from script
                fallback_verbs = ["wave", "wheel", "turn", "walk", "run", "hold", "pull", "talk", "say", "learn"]
                joined = " ".join(script_list).lower()
                picked = None
                for v in fallback_verbs:
                    if re.search(rf"\\b{re.escape(v)}\\b", joined):
                        picked = v
                        break
                if picked:
                    keywords = [{
                        "word": picked,
                        "pronunciation_us": "",
                        "pronunciation_uk": "",
                        "explanation": f"to {picked}"
                    }]
        logger.info(f"Extracted keywords: {keywords}")
        for s in story_segments:
            s["keywords"] = [] if s.get("is_cover") else keywords

        # 为每个场景生成图片
        used_objects = set()
        use_inpainting = bool(getattr(request, "use_inpainting", False))
        image_model_name = (request.image_llm_model or settings.image_llm_model or "").lower()
        avoid_counts = settings.avoid_exact_counts
        if getattr(request, "avoid_exact_counts", None) is not None:
            avoid_counts = bool(request.avoid_exact_counts)
        use_exact_counts = (not avoid_counts) and ("lightning" not in image_model_name)
        is_edu_topic = (
            getattr(request, "topic_type", None) == "explanation"
            or any(k in (request.story_prompt or "").lower() for k in ["plural", "plurals", "grammar", "english", "learning", "teach", "lesson"])
        )
        is_dialogue_topic = getattr(request, "topic_type", None) == "dialogue"
        target_w, target_h = None, None
        if request.resolution:
            try:
                w_str, h_str = request.resolution.replace("x", "*").split("*")
                target_w, target_h = int(w_str.strip()), int(h_str.strip())
            except Exception:
                target_w, target_h = None, None
        for idx, segment in enumerate(story_segments, 1):
            if req_topic_type != "sequence":
                logger.info(f"Wait for 2 mins, free api have concurrency limits")
                time.sleep(120)
                logger.info(f"2 mins passed")
            
            try:
                logger.info(
                    f"Scene {idx} start | use_inpainting={use_inpainting} | "
                    f"model={request.image_llm_model or settings.image_llm_model} | "
                    f"resolution={request.resolution}"
                )
                is_cover = bool(segment.get("is_cover"))
                segment["topic_type"] = segment.get("topic_type") or req_topic_type
                segment["script"] = self._strip_invisible_chars(segment.get("script", ""))
                segment["scene_prompt"] = self._strip_invisible_chars(segment.get("scene_prompt", ""))
                if is_dialogue_topic and not is_cover:
                    segment["script"] = to_voiceover_dialogue(segment.get("script", ""))
                if is_edu_topic and not use_exact_counts and not is_cover:
                    segment["script"] = soften_counts_in_text(segment.get("script", ""))
                    segment["scene_prompt"] = soften_counts_in_text(segment.get("scene_prompt", ""))
                objs = []
                if is_edu_topic and not is_cover:
                    objs = ensure_unique_objects(segment, idx, used_objects)
                    ensure_objects_in_script(segment, objs, use_exact_counts)
                else:
                    segment["objects"] = segment.get("objects", [])
                if not is_cover:
                    add_role_hints(segment)
                variation_hint = ""
                if idx > 1 and not is_cover:
                    variation_hint = await build_variation_hint(segment.get("script", ""), idx, request)
                img2img_kwargs = {}
                if use_inpainting and previous_base64 and not is_cover:
                    img2img_kwargs = {"init_image_base64": previous_base64}
                logger.info(
                    f"Scene {idx} image mode | "
                    f"img2img={'yes' if img2img_kwargs else 'no'} | "
                    f"init_image_bytes={'present' if img2img_kwargs else 'none'}"
                )
                if not is_cover:
                    segment["scene_prompt"] = enhance_scene_prompt_for_education(
                        segment.get("script", ""),
                        segment.get("scene_prompt", ""),
                        segment.get("objects", []),
                        use_exact_counts,
                        is_edu_topic,
                    )
                if getattr(request, "topic_type", None) in ("dialogue", "scene") and not is_cover:
                    segment["scene_prompt"] = (
                        segment["scene_prompt"]
                        + "\nKeep the overall look and feel identical; only change motion or add small details. Keep the same characters and background."
                    )
                
                image_url = self.generate_image(
                    prompt=(
                        segment["scene_prompt"]
                        + (f"\n\nVariation hint for this scene: {variation_hint}" if variation_hint else "")
                    ),
                    resolution=request.resolution, 
                    image_llm_provider=request.image_llm_provider, 
                    image_llm_model=request.image_llm_model,
                    task_dir=str(task_dir),           # pass task dir
                    segment_index=idx,                 # pass 1-based index
                    **img2img_kwargs
                )
                if not image_url:
                    logger.warning(
                        f"Image generation failed for scene {idx}; waiting 5 minutes before retry"
                    )
                    time.sleep(300)
                    image_url = self.generate_image(
                        prompt=(
                            segment["scene_prompt"]
                            + (f"\n\nVariation hint for this scene: {variation_hint}" if variation_hint else "")
                        ),
                        resolution=request.resolution, 
                        image_llm_provider=request.image_llm_provider, 
                        image_llm_model=request.image_llm_model,
                        task_dir=str(task_dir),           # pass task dir
                        segment_index=idx,                 # pass 1-based index
                        **img2img_kwargs
                    )
                    if not image_url:
                        logger.error(
                            f"Image generation failed after retry for scene {idx}; aborting video generation"
                        )
                        raise RuntimeError("Image generation failed after retry")
                segment["url"] = image_url
                if image_url and os.path.exists(image_url):
                    try:
                        with Image.open(image_url) as im:
                            w, h = im.size
                        logger.info(f"Scene {idx} output size: {w}x{h}")
                    except Exception as e:
                        logger.warning(f"Scene {idx} output size check failed: {e}")
                # Orientation recheck removed per request (no retry)
                if image_url and not is_cover:
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
                if isinstance(e, RuntimeError) and "after retry" in str(e):
                    raise

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
        
        return {
            "textLLMProviders": textLLMList,
            "imageLLMProviders": imgLLMList,
            "text_llm_model": settings.text_llm_model,
            "image_llm_model": settings.image_llm_model,
            "resolution": settings.image_resolution,
            "text_llm_provider": settings.text_provider,
            "image_llm_provider": settings.image_provider,
        }

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
            
            if "script" not in scene:
                raise LLMResponseValidationError(f"Scene {i} missing 'script' field")
            
            if "scene_prompt" not in scene:
                raise LLMResponseValidationError(f"Scene {i} missing 'scene_prompt' field")
            
            if not isinstance(scene["script"], str):
                raise LLMResponseValidationError(f"Scene {i} 'script' must be a string")
            
            if not isinstance(scene["scene_prompt"], str):
                raise LLMResponseValidationError(f"Scene {i} 'scene_prompt' must be a string")
            
            if "objects" in scene and not isinstance(scene["objects"], list):
                raise LLMResponseValidationError(f"Scene {i} 'objects' must be an array")

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

    async def _get_story_prompt(self, story_prompt: str = None, language: Language = Language.CHINESE_CN, segments: int = 3, base_start: str = "讲一个故事，主题是：", text_lang_note: str = "written in Chinese (简体中文)", extra_requirements: str = "") -> str:
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
        {base_prompt}. {segment_instruction}, and each scene must include a narration script and a visual scene prompt.

        Please return the result in the following JSON format, where the key `list` contains an array of objects:

        **Expected JSON format**:
        {{
            "list": [
                {{
                    "script": "Short, kid-friendly narration for the scene",
                    "scene_prompt": "Detailed visual prompt describing what to draw, in English",
                    "objects": ["two cats", "three apples"]
                }}
                ,// ... {segments-1} more if segments > 1
            ]
        }}

        **Requirements**:
        1. The root object must contain a key named `list`, and its value must be an array of scene objects.
        2. Each object in the `list` array must include:
            - `script`: A short, kid-friendly narration for the scene, written in {languageValue}.
            - `scene_prompt`: A detailed prompt for generating an image, written in English.
            - `objects`: An array of plural objects to show (e.g., ["two cats", "three apples"]). Use [] if none.
        3. Ensure the JSON format matches the above example exactly. Avoid extra fields or incorrect key names.
        {extra_requirements}
        {array_note}

        **Important**:
        - Do not include explanations, comments, markdown code blocks, or any text outside the JSON.
        - Output **only** the valid JSON object.
        """

    

# 创建服务实例
llm_service = LLMService()
