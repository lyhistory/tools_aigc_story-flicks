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
