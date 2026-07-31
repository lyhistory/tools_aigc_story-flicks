from fastapi import APIRouter
from app.api import voice, video, llm, social

router = APIRouter(prefix="/api")
router.include_router(voice.router, prefix="/voice", tags=["voice"])
router.include_router(video.router, prefix="/video", tags=["video"])
router.include_router(llm.router, prefix="/llm", tags=["llm"])
router.include_router(social.router, prefix="/social", tags=["social"])
