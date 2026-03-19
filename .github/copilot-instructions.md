# StoryFlicks AI Coding Instructions

## Project Overview

**StoryFlicks** is a full-stack AI storytelling platform that generates animated story videos from text prompts.

**Data Flow**: User story theme → LLM generates story segments → Each segment gets AI image → Voice generation → Video composition with subtitles

## Architecture & Key Components

### Backend (FastAPI, Python 3.10+)
- **Entry point**: `backend/main.py` - FastAPI app with CORS for localhost:8081 (dev frontend)
- **Config**: `app/config.py` - Pydantic Settings with `.env` support. Supports multi-provider LLMs (OpenAI, Aliyun, DeepSeek, Ollama, SiliconFlow, NVIDIA, Cloudflare)
- **API Router**: `app/api/router.py` - Exposes `/api/llm`, `/api/voice`, `/api/video` endpoints
- **Services** (business logic):
  - `app/services/llm.py` - Story generation + image generation with provider abstraction; uses retry with exponential backoff via `tenacity`
  - `app/services/video.py` - Video composition using MoviePy (352 lines); handles text wrapping, subtitle rendering, audio sync
  - `app/services/voice.py` - TTS via multiple providers (Edge TTS, Google Cloud TTS, etc.)
  - `app/services/story.py` - In-memory story CRUD (basic skeleton, not actively used)
  - `app/services/health.py` - Health check
- **Schemas**: `app/schemas/` - Pydantic request/response models (StoryGenerationRequest, ImageGenerationRequest, VideoGenerateRequest)
- **Constants**: `app/models/const.py` - StoryType, ImageStyle, Language enums; LANGUAGE_NAMES mapping; PUNCTUATIONS list
- **Static serving**: Task output (images, videos, subtitles) mounted at `/tasks` endpoint

### Frontend (React 18 + TypeScript, Vite)
- Built with Ant Design UI components, i18next for i18n, Zustand for state, Axios for API calls
- Communicates with backend via `axios` to localhost:8000 in dev mode

### Docker Compose
- Two services: backend (port 8000) + frontend (port 8081) on shared `app-network`
- Frontend volumes exclude `node_modules` to prevent override
- Backend uses `.env` from `backend/` for configuration

## Key Patterns & Conventions

### LLM Provider Abstraction
- **Pattern**: Settings specify `text_provider` / `image_provider` (openai, aliyun, deepseek, ollama, siliconflow, nvidia, cloudflare)
- **Implementation**: Initialize clients at module load time (`llm.py` lines 27-51), switch at runtime based on `provider` param
- **Example**: `call_flux()` function uses `@retry` decorator for resilient image generation with 5-min timeout

### Task Management
- **Task Directory**: `backend/tasks/{timestamp}/` stores all artifacts (story.json, N.srt subtitle files, images, video output)
- **Schema**: StoryScene objects contain text, image_prompt, image_url, voice_duration; serialized to JSON

### Video Composition Workflow (MoviePy)
1. Download/load images for each scene
2. Create ImageClip for each scene
3. Generate voice audio with TTS service
4. Load subtitle (.srt) files and render with SubtitlesClip
5. Compose clips (image + audio + subtitles) with fade transitions
6. Concatenate scenes and render final video

### Error Handling
- Custom exception: `LLMResponseValidationError` for LLM response parsing failures
- Services raise `HTTPException` with descriptive messages; logged via `loguru`
- Retry logic: `tenacity` with exponential backoff (min=60s, max=120s) for resilient API calls

## Critical Developer Workflows

### Local Development
```bash
# Backend setup
cd backend
conda create -n story-flicks python=3.10
conda activate story-flicks
pip install -r requirements.txt
# Create .env with API keys (openai_api_key, aliyun_api_key, etc.)
cp .env.example .env
uvicorn main:app --reload
# Runs on http://localhost:8000 with auto-reload
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev  # Runs on http://localhost:8081
```

### Docker
```bash
docker-compose up --build  # Builds and starts both services
```

### Key Config Variables
- **Text/Image Providers**: `text_provider`, `image_provider` (must match provider-specific config below)
- **API Keys**: `{provider}_api_key` (e.g., `openai_api_key`, `aliyun_api_key`)
- **Model Names**: `text_llm_model` (default: meta/llama-3.1-70b-instruct), `image_llm_model` (default: black-forest-labs/flux-1-dev)
- **Image Resolution**: `image_resolution` (default: 1080*1920)

## Data Schemas & Validation

### Request/Response Flow
```
POST /api/llm/story
  ↓ StoryGenerationRequest(story_prompt, segments: 1-10, language, providers/models)
  ↓ Returns: StoryGenerationResponse(segments: List[StorySegment])

POST /api/video/generate
  ↓ VideoGenerateRequest(scene_descriptions, voice settings, language)
  ↓ Returns: {video_url, task_id}
```

### Language Enum Mapping
- `zh-CN`, `zh-TW`, `en-GB`, `en-US`, `ja-JP`, `ko-KR`
- Update `LANGUAGE_NAMES` dict in `app/models/const.py` if adding languages

## Code Organization Rules

- **API endpoints** → minimal logic, delegate to services
- **Services** → implement business logic, may call external APIs or other services
- **Schemas** → Pydantic models with validation rules (e.g., segments 1-10)
- **Config** → all env-dependent values; never hardcode API keys or URLs
- **Utils** → `app/utils/utils.py` for shared helpers (file I/O, path handling)

## Testing & Debugging

- **Logs**: Configured via `loguru`; check console output for errors
- **API Docs**: Auto-generated Swagger UI at `http://localhost:8000/docs`
- **Task Artifacts**: Browse `/tasks/{timestamp}/` to inspect generated story.json, SRT files, images

## Common Pitfalls

1. **Provider Mismatch**: Ensure `text_provider` env var matches an initialized client (e.g., if using `siliconflow`, verify `siliconflow_api_key` is set)
2. **Image Resolution Format**: Use `"WxH"` string format (e.g., `1080*1920`), not integers
3. **Subtitle Timing**: SRT timing must be precise; MoviePy SubtitlesClip is strict with format
4. **CORS Localhost Only**: Default CORS allows only `localhost:8081` and `127.0.0.1:8081`; update `main.py` for production
5. **Task Directory Permissions**: Ensure `backend/tasks/` exists and is writable; auto-created in main.py if missing
