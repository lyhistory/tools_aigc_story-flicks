import { request } from "../utils/request";

export async function getVoiceList(data: { provider?: string; language?: string; area?: string[] }): Promise<VoiceListRes> {
    return request<VoiceListRes>({
        url: "/api/voice/voices",
        method: "post",
        data,
    });
}

export async function getVoiceOptions(): Promise<VoiceOptionsRes> {
    return request<VoiceOptionsRes>({
        url: "/api/voice/options",
        method: "get",
    });
}

export async function getLLMProviders(): Promise<LLMProvidersRes> {
    return request<LLMProvidersRes>({
        url: "/api/llm/providers",
        method: "get",
    });
}

export async function generateVideo(data: VideoGenerateReq): Promise<VideoGenerateRes> {
    return request<VideoGenerateRes>({
        url: "/api/video/generate",
        method: "post",
        data,
    });
}

export async function generateStoryboard(data: VideoGenerateReq): Promise<StoryboardRes> {
    return request<StoryboardRes>({
        url: "/api/video/generate_storyboard",
        method: "post",
        data,
    });
}

export async function assembleVideo(data: StoryboardAssembleReq): Promise<VideoGenerateRes> {
    return request<VideoGenerateRes>({
        url: "/api/video/assemble_video",
        method: "post",
        data,
    });
}

export async function regenerateImage(data: RegenerateImageReq): Promise<VideoGenerateRes> {
    return request<VideoGenerateRes>({
        url: "/api/video/regenerate_image",
        method: "post",
        data,
    });
}

export async function uploadImage(file: File, taskId: string): Promise<{ success: boolean; data?: { image_url: string }; message?: string }> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('task_id', taskId);
    return request({
        url: "/api/video/upload_image",
        method: "post",
        headers: {
            'Content-Type': 'multipart/form-data',
        },
        data: formData,
    });
}

export async function getSubtitleFonts(): Promise<{ success: boolean; data: { fonts: { id: string; label: string; default: boolean }[]; default: string } }> {
    return request({
        url: "/api/video/fonts",
        method: "get",
    });
}

export async function retranslateScript(data: RetranslateScriptReq): Promise<{ success: boolean; data?: { translation: string }; message?: string | null }> {
    return request({
        url: "/api/video/retranslate_script",
        method: "post",
        data,
    });
}

// ========================
// Social Publisher
// ========================

export async function getSocialStatus(): Promise<{ success: boolean; data?: { douyin: boolean; xiaohongshu: boolean; channels: boolean }; message?: string }> {
    return request({
        url: "/api/social/status",
        method: "get",
    });
}

export async function startSocialLogin(platform: string): Promise<{ success: boolean; data?: { qr_base64: string | null; already_logged_in?: boolean }; message?: string }> {
    return request({
        url: `/api/social/login/${platform}/start`,
        method: "post",
    });
}

export async function pollSocialLogin(platform: string): Promise<{ success: boolean; data?: { status: string }; message?: string }> {
    return request({
        url: `/api/social/login/${platform}/poll`,
        method: "get",
    });
}

export async function publishSocialVideo(platforms: string, title: string, description: string, scheduledAt?: string | null, file?: File | null, videoPath?: string): Promise<{ success: boolean; data?: { task_id: string }; message?: string }> {
    const formData = new FormData();
    formData.append('platforms', platforms);
    formData.append('title', title);
    formData.append('description', description);
    if (scheduledAt) {
        formData.append('scheduled_at', scheduledAt);
    }
    if (file) {
        formData.append('file', file);
    }
    if (videoPath) {
        formData.append('video_path', videoPath);
    }

    return request({
        url: "/api/social/publish",
        method: "post",
        headers: {
            'Content-Type': 'multipart/form-data',
        },
        data: formData,
    });
}

export async function debugUploadSocialVideo(platforms: string, title: string, description: string, scheduledAt?: string | null, file?: File | null, videoPath?: string): Promise<{ success: boolean; data?: { task_id: string }; message?: string }> {
    const formData = new FormData();
    formData.append('platforms', platforms);
    formData.append('title', title);
    formData.append('description', description);
    if (scheduledAt) {
        formData.append('scheduled_at', scheduledAt);
    }
    if (file) {
        formData.append('file', file);
    }
    if (videoPath) {
        formData.append('video_path', videoPath);
    }

    return request({
        url: "/api/social/publish/debug",
        method: "post",
        headers: {
            'Content-Type': 'multipart/form-data',
        },
        data: formData,
    });
}

export interface PublishPlatformResult {
    status: string;
    retries: number;
    message: string;
    screenshot_url?: string | null;
}

export async function pollPublishStatus(taskId: string): Promise<{ success: boolean; data?: { status: string; current_platform?: string | null; results?: Record<string, PublishPlatformResult>; scheduled_for?: string | null }; message?: string }> {
    return request({
        url: `/api/social/publish/status/${taskId}`,
        method: "get"
    });
}

export async function stopPublishTask(taskId: string) {
    return request({
        url: `/api/social/publish/stop/${taskId}`,
        method: "post"
    });
}

export async function skipCurrentPublishPlatform(taskId: string): Promise<{ success: boolean; message?: string }> {
    return request({
        url: `/api/social/publish/skip/${taskId}`,
        method: "post"
    });
}
