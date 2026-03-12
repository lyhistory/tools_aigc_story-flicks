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

export async function getSubtitleFonts(): Promise<{ success: boolean; data: { fonts: { id: string; label: string; default: boolean }[]; default: string } }> {
    return request({
        url: "/api/video/fonts",
        method: "get",
    });
}
