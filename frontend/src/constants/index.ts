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

export const LEARNER_AGE_OPTIONS = [
    { label: "3-5 years", value: "3-5" },
    { label: "6-8 years", value: "6-8" },
    { label: "9-12 years", value: "9-12" },
    { label: "13-15 years", value: "13-15" },
    { label: "16-18 years", value: "16-18" },
];
