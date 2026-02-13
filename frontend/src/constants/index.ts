export const VOICE_LANGUAGES: Language[] = [
    "zh-CN",
    "zh-TW",
    "fixed-en-GB",
    "en-GB",
    "en-US",
    "ja-JP",
    "ko-KR"
];

export const VOICE_LANGUAGES_LABELS = [
    {
        label: '中文（简体）',
        value: 'zh-CN'
    },
    {
        label: '中文（繁体）',
        value: 'zh-TW'
    },
    {
        label: 'Fixed UK Accent',
        value: 'fixed-en-GB' 
    },
    {
        label: 'British English (UK)',
        value: 'en-GB' 
    },
    {
        label: 'English',
        value: 'en-US'
    },
    {
        label: '日本語',
        value: 'ja-JP'
    },
    {
        label: '한국어',
        value: 'ko-KR'
    }
]

export const VOICE_PROVIDERS = [
    { label: "gTTS (Fixed UK)", value: "gtts" },
    { label: "Edge TTS", value: "edge-tts" },
    { label: "Google Cloud TTS", value: "google-tts" },
];
