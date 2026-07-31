from fastapi import APIRouter, HTTPException, BackgroundTasks, Form, UploadFile, File
from fastapi.responses import FileResponse
from loguru import logger
from typing import List, Dict, Any
from app.services.social import DouyinPublisher, XiaohongshuPublisher, WechatChannelsPublisher, ACTIVE_LOGIN_SESSIONS

import uuid
import asyncio
from datetime import datetime
import os
from contextlib import suppress

router = APIRouter()

PUBLISH_TASKS: Dict[str, Any] = {}

class PlatformSkipped(Exception):
    pass

SCREENSHOT_BY_PLATFORM = {
    "douyin": "douyin_error.png",
    "xiaohongshu": "xhs_error.png",
    "channels": "channels_error.png",
}

SUCCESS_SCREENSHOT_BY_PLATFORM = {
    "douyin": "douyin_after_publish.png",
    "xiaohongshu": "xhs_after_publish.png",
    "channels": "channels_after_publish.png",
}

DEBUG_SCREENSHOT_BY_PLATFORM = {
    "douyin": "douyin_debug_upload.png",
    "xiaohongshu": "xhs_debug_upload.png",
    "channels": "channels_debug_upload.png",
}

ALLOWED_SCREENSHOTS = {
    "douyin_error.png",
    "douyin_after_publish.png",
    "douyin_debug_upload.png",
    "xhs_error.png",
    "xhs_after_publish.png",
    "xhs_debug_upload.png",
    "channels_error.png",
    "channels_after_publish.png",
    "channels_debug_upload.png",
}

def _screenshot_url(filename: str | None) -> str | None:
    if not filename:
        return None
    path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(path):
        return None
    return f"/api/social/publish/screenshot/{filename}?t={int(os.path.getmtime(path))}"

def _parse_scheduled_at(scheduled_at: str | None) -> datetime | None:
    if not scheduled_at:
        return None
    try:
        normalized = scheduled_at.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid scheduled_at value: {e}")

async def _publish_with_task_controls(task_id: str, platform: str, pub, actual_path: str, title: str, description: str, scheduled_for: datetime):
    publish_task = asyncio.create_task(pub.publish(actual_path, title, description, scheduled_for))
    try:
        while not publish_task.done():
            task_state = PUBLISH_TASKS[task_id]
            if task_state.get("stop_requested"):
                publish_task.cancel()
                with suppress(asyncio.CancelledError):
                    await publish_task
                raise asyncio.CancelledError()
            if task_state.get("skip_requested_platform") == platform:
                publish_task.cancel()
                with suppress(asyncio.CancelledError):
                    await publish_task
                raise PlatformSkipped("Skipped by user")
            await asyncio.sleep(1)
        if PUBLISH_TASKS[task_id].get("skip_requested_platform") == platform:
            raise PlatformSkipped("Skipped by user")
        return await publish_task
    finally:
        if PUBLISH_TASKS.get(task_id, {}).get("skip_requested_platform") == platform:
            PUBLISH_TASKS[task_id]["skip_requested_platform"] = None

async def _debug_upload_with_task_controls(task_id: str, pub, actual_path: str, title: str, description: str, scheduled_for: datetime):
    debug_task = asyncio.create_task(pub.debug_upload(actual_path, title, description, scheduled_for))
    while not debug_task.done():
        if PUBLISH_TASKS[task_id].get("stop_requested"):
            debug_task.cancel()
            with suppress(asyncio.CancelledError):
                await debug_task
            raise asyncio.CancelledError()
        await asyncio.sleep(1)
    return await debug_task

async def _run_publish_task(task_id: str, platforms: List[str], actual_path: str, title: str, description: str, scheduled_at: str | None, cleanup_file: bool):
    try:
        scheduled_for = _parse_scheduled_at(scheduled_at)
        if not scheduled_for:
            raise ValueError("Schedule time is required for publishing.")

        for p in platforms:
            p_clean = p.strip()
            if not p_clean:
                continue
            PUBLISH_TASKS[task_id]["current_platform"] = p_clean
            
            PUBLISH_TASKS[task_id]["results"][p_clean] = {"status": "publishing", "retries": 0, "message": "", "screenshot_url": None}
            
            pub = get_publisher(p_clean)
            max_retries = 9999  # Practically infinite, until user stops
            
            for attempt in range(max_retries):
                if PUBLISH_TASKS[task_id].get("stop_requested"):
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "stopped"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Stopped by user"
                    break
                
                try:
                    await _publish_with_task_controls(task_id, p_clean, pub, actual_path, title, description, scheduled_for)
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "success"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = ""
                    PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = _screenshot_url(
                        SUCCESS_SCREENSHOT_BY_PLATFORM.get(p_clean)
                    )
                    break
                except PlatformSkipped:
                    logger.info(f"Publish skipped for {p_clean} by user request")
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "skipped"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Skipped by user"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = _screenshot_url(SCREENSHOT_BY_PLATFORM.get(p_clean))
                    break
                except asyncio.CancelledError:
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "stopped"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Stopped by user"
                    break
                except Exception as e:
                    logger.error(f"Publish failed for {p_clean} (Attempt {attempt + 1}): {e}")
                    screenshot_url = _screenshot_url(SCREENSHOT_BY_PLATFORM.get(p_clean))
                    
                    if PUBLISH_TASKS[task_id].get("stop_requested"):
                        PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "stopped"
                        PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = f"Stopped. Last error: {str(e)}"
                        PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = screenshot_url
                        break
                        
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "waiting_retry"
                    PUBLISH_TASKS[task_id]["results"][p_clean]["retries"] = attempt + 1
                    PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = str(e)
                    PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = screenshot_url
                    
                    # Wait 60s, but check stop flag frequently
                    for _ in range(60):
                        if PUBLISH_TASKS[task_id].get("stop_requested"):
                            break
                        if PUBLISH_TASKS[task_id].get("skip_requested_platform") == p_clean:
                            break
                        await asyncio.sleep(1)
                    
                    if PUBLISH_TASKS[task_id].get("stop_requested"):
                        PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "stopped"
                        break
                    if PUBLISH_TASKS[task_id].get("skip_requested_platform") == p_clean:
                        PUBLISH_TASKS[task_id]["skip_requested_platform"] = None
                        PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "skipped"
                        PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Skipped by user"
                        break
                    
                    PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "publishing"
            PUBLISH_TASKS[task_id]["current_platform"] = None
                    
    finally:
        if PUBLISH_TASKS[task_id].get("stop_requested"):
            PUBLISH_TASKS[task_id]["status"] = "stopped"
        elif PUBLISH_TASKS[task_id].get("status") != "stopped":
            PUBLISH_TASKS[task_id]["status"] = "completed"
        import os
        if cleanup_file and actual_path and os.path.exists(actual_path):
            try:
                os.remove(actual_path)
            except:
                pass

async def _run_debug_upload_task(task_id: str, platforms: List[str], actual_path: str, title: str, description: str, scheduled_at: str | None, cleanup_file: bool):
    try:
        scheduled_for = _parse_scheduled_at(scheduled_at)
        if not scheduled_for:
            raise ValueError("Schedule time is required for debug upload.")

        for p in platforms:
            p_clean = p.strip()
            if not p_clean:
                continue
            PUBLISH_TASKS[task_id]["current_platform"] = p_clean
            PUBLISH_TASKS[task_id]["results"][p_clean] = {"status": "debugging", "retries": 0, "message": "", "screenshot_url": None}
            pub = get_publisher(p_clean)

            try:
                screenshot_path = await _debug_upload_with_task_controls(task_id, pub, actual_path, title, description, scheduled_for)
                PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "ready"
                PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Upload prepared. Publish was not clicked."
                PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = _screenshot_url(screenshot_path)
            except asyncio.CancelledError:
                PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "stopped"
                PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = "Stopped by user"
                break
            except Exception as e:
                logger.error(f"Debug upload failed for {p_clean}: {e}")
                PUBLISH_TASKS[task_id]["results"][p_clean]["status"] = "failed"
                PUBLISH_TASKS[task_id]["results"][p_clean]["message"] = str(e)
                PUBLISH_TASKS[task_id]["results"][p_clean]["screenshot_url"] = _screenshot_url(
                    DEBUG_SCREENSHOT_BY_PLATFORM.get(p_clean)
                )

            PUBLISH_TASKS[task_id]["current_platform"] = None
            if PUBLISH_TASKS[task_id].get("stop_requested"):
                break
    finally:
        if PUBLISH_TASKS[task_id].get("stop_requested"):
            PUBLISH_TASKS[task_id]["status"] = "stopped"
        else:
            PUBLISH_TASKS[task_id]["status"] = "completed"
        if cleanup_file and actual_path and os.path.exists(actual_path):
            try:
                os.remove(actual_path)
            except:
                pass

def get_publisher(platform: str):
    if platform == "douyin":
        return DouyinPublisher()
    elif platform == "xiaohongshu":
        return XiaohongshuPublisher()
    elif platform == "channels":
        return WechatChannelsPublisher()
    raise HTTPException(status_code=400, detail="Unknown platform")

@router.get("/status")
async def get_social_status():
    """Returns the login status for all platforms."""
    douyin = DouyinPublisher()
    xhs = XiaohongshuPublisher()
    channels = WechatChannelsPublisher()
    
    return {
        "success": True,
        "data": {
            "douyin": await douyin.is_authenticated(),
            "xiaohongshu": await xhs.is_authenticated(),
            "channels": await channels.is_authenticated()
        }
    }

@router.post("/login/{platform}/start")
async def start_login(platform: str):
    """Starts a login session and returns the QR code."""
    publisher = get_publisher(platform)
    try:
        qr_b64 = await publisher.start_login_session()
        if qr_b64 == "ALREADY_LOGGED_IN":
            return {"success": True, "data": {"qr_base64": None, "already_logged_in": True}}
        return {"success": True, "data": {"qr_base64": qr_b64}}
    except Exception as e:
        logger.error(f"Failed to start login for {platform}: {e}")
        return {"success": False, "message": str(e)}

@router.get("/login/{platform}/poll")
async def poll_login_status(platform: str):
    """Polls the status of an ongoing login session."""
    session = ACTIVE_LOGIN_SESSIONS.get(platform)
    if not session:
        return {"success": True, "data": {"status": "none"}}
    
    return {"success": True, "data": {"status": session["status"]}}

@router.post("/publish")
async def publish_video(
    background_tasks: BackgroundTasks,
    platforms: str = Form(...), # comma separated e.g. "douyin,xiaohongshu"
    title: str = Form(...),
    description: str = Form(...),
    scheduled_at: str = Form(None),
    video_path: str = Form(None), # if using local generated video
    file: UploadFile = File(None) # if uploading a new video
):
    import os
    from app.utils.utils import get_root_dir
    target_platforms = platforms.split(",")
    
    # Resolve video
    actual_path = None
    cleanup_file = False
    if file:
        temp_dir = os.path.join(get_root_dir(), "temp_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        actual_path = os.path.join(temp_dir, file.filename)
        with open(actual_path, "wb") as f:
            content = await file.read()
            f.write(content)
        cleanup_file = True
    elif video_path:
        actual_path = video_path
        
    if not actual_path or not os.path.exists(actual_path):
        return {"success": False, "message": "Video file not found."}

    scheduled_for = _parse_scheduled_at(scheduled_at)
    if not scheduled_for:
        return {"success": False, "message": "Schedule time is required."}
    task_id = str(uuid.uuid4())
    PUBLISH_TASKS[task_id] = {
        "status": "running",
        "stop_requested": False,
        "skip_requested_platform": None,
        "current_platform": None,
        "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
        "results": {
            p.strip(): {"status": "pending", "retries": 0, "message": "", "screenshot_url": None} 
            for p in target_platforms if p.strip()
        }
    }
    
    background_tasks.add_task(
        _run_publish_task, 
        task_id, 
        target_platforms, 
        actual_path, 
        title, 
        description, 
        scheduled_at, 
        cleanup_file
    )
    
    return {"success": True, "data": {"task_id": task_id}}

@router.post("/publish/debug")
async def debug_upload_video(
    background_tasks: BackgroundTasks,
    platforms: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    scheduled_at: str = Form(None),
    video_path: str = Form(None),
    file: UploadFile = File(None)
):
    from app.utils.utils import get_root_dir
    target_platforms = platforms.split(",")

    actual_path = None
    cleanup_file = False
    if file:
        temp_dir = os.path.join(get_root_dir(), "temp_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        actual_path = os.path.join(temp_dir, file.filename)
        with open(actual_path, "wb") as f:
            f.write(await file.read())
        cleanup_file = True
    elif video_path:
        actual_path = video_path

    if not actual_path or not os.path.exists(actual_path):
        return {"success": False, "message": "Video file not found."}

    scheduled_for = _parse_scheduled_at(scheduled_at)
    if not scheduled_for:
        return {"success": False, "message": "Schedule time is required."}

    task_id = str(uuid.uuid4())
    PUBLISH_TASKS[task_id] = {
        "status": "running",
        "stop_requested": False,
        "skip_requested_platform": None,
        "current_platform": None,
        "scheduled_for": scheduled_for.isoformat(),
        "debug_only": True,
        "results": {
            p.strip(): {"status": "pending", "retries": 0, "message": "", "screenshot_url": None}
            for p in target_platforms if p.strip()
        }
    }

    background_tasks.add_task(
        _run_debug_upload_task,
        task_id,
        target_platforms,
        actual_path,
        title,
        description,
        scheduled_at,
        cleanup_file,
    )

    return {"success": True, "data": {"task_id": task_id}}

@router.get("/publish/status/{task_id}")
async def get_publish_status(task_id: str):
    if task_id not in PUBLISH_TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "data": PUBLISH_TASKS[task_id]}

@router.get("/publish/screenshot/{filename}")
async def get_publish_screenshot(filename: str):
    if filename not in ALLOWED_SCREENSHOTS:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")

@router.post("/publish/stop/{task_id}")
async def stop_publish_task(task_id: str):
    if task_id not in PUBLISH_TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    PUBLISH_TASKS[task_id]["stop_requested"] = True
    return {"success": True, "message": "Stop requested"}

@router.post("/publish/skip/{task_id}")
async def skip_current_platform(task_id: str):
    if task_id not in PUBLISH_TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    current_platform = PUBLISH_TASKS[task_id].get("current_platform")
    if not current_platform:
        return {"success": False, "message": "No platform is currently publishing."}
    PUBLISH_TASKS[task_id]["skip_requested_platform"] = current_platform
    return {"success": True, "message": f"Skip requested for {current_platform}"}
