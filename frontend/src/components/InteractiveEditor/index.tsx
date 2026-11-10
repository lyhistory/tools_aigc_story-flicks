import React, { useEffect, useRef, useState } from 'react';
import {
    Button,
    Card,
    Col,
    ColorPicker,
    Divider,
    Empty,
    Input,
    InputNumber,
    message,
    Modal,
    Popconfirm,
    Row,
    Select,
    Slider,
    Space,
    Switch,
    Tag,
    Typography,
    Upload,
} from 'antd';
import {
    ArrowDownOutlined,
    ArrowUpOutlined,
    DeleteOutlined,
    PlusOutlined,
    TranslationOutlined,
    UploadOutlined,
} from '@ant-design/icons';
import { assembleInteractive, checkTranscripts, getSubtitleFonts, translateScenes, uploadImage } from '../../services/index';
import { useVideoStore } from '../../stores';
import GrammarCheckModal from '../GrammarCheckModal';
import { applyGrammarFixesToText, grammarIssueKey, groupGrammarIssues } from '../../utils/grammar';

const { Text } = Typography;

interface InteractiveEditorProps {
    taskId: string;
    resolution: string;
    chineseSubtitleEnabled: boolean;
    karaoke: boolean;
    voiceProvider?: string;
    voiceName?: string;
    voiceRate?: number;
    language?: string;
    onClose: () => void;
}

interface FontOption {
    id: string;
    label: string;
    default: boolean;
}

type HighlightShape = 'rect' | 'ellipse';

interface DraftRect {
    roundIndex?: number;
    blockId?: string;
    target?: 'answer' | 'spotlight';
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
}

const DEFAULT_FONT = 'UbuntuCondensed';
const DEFAULT_HIGHLIGHT_COLOR = '#FFD54F';

const VOICE_RATE_OPTIONS = [
    { value: 1.0, label: '1.00x (Normal)' },
    { value: 0.95, label: '0.95x (Slightly slower)' },
    { value: 0.9, label: '0.90x (Recommended for children)' },
    { value: 0.85, label: '0.85x (Slow)' },
];

const COUNTDOWN_POSITION_OPTIONS = [
    { value: 'center', label: 'Center' },
    { value: 'top_left', label: 'Top left' },
    { value: 'top_center', label: 'Top center' },
    { value: 'top_right', label: 'Top right' },
    { value: 'center_left', label: 'Middle left' },
    { value: 'center_right', label: 'Middle right' },
    { value: 'bottom_left', label: 'Bottom left' },
    { value: 'bottom', label: 'Bottom center' },
    { value: 'bottom_right', label: 'Bottom right' },
];

const COUNTDOWN_SOUND_OPTIONS = [
    { value: 'tick_ding', label: 'Tick every second + final ding (recommended)' },
    { value: 'tick', label: 'Tick every second' },
    { value: 'beep', label: 'Soft beep every second' },
    { value: 'ding', label: 'Final ding only' },
    { value: 'none', label: 'Silent' },
];

const ANSWER_SUBTITLE_POSITION_OPTIONS = [
    { value: 'bottom', label: 'Bottom (default)' },
    { value: 'center', label: 'Middle' },
    { value: 'top', label: 'Top' },
    { value: 'bottom_left', label: 'Bottom left' },
    { value: 'bottom_right', label: 'Bottom right' },
    { value: 'top_left', label: 'Top left' },
    { value: 'top_right', label: 'Top right' },
    { value: 'center_left', label: 'Middle left' },
    { value: 'center_right', label: 'Middle right' },
];

const HIGHLIGHT_COLORS = ['#FFD54F', '#FF7043', '#4DD0E1', '#81C784', '#F06292', '#FFFFFF'];

const makeRound = (): InteractiveRound => ({
    question_image_url: '',
    question_script: '',
    question_translation: '',
    answer_script: '',
    answer_translation: '',
    answer_image_url: null,
    countdown_seconds: 5,
    countdown_position: 'center',
    countdown_sound: 'tick_ding',
    countdown_label: '',
    highlights: [],
    answer_subtitle_font_size: null,
    answer_subtitle_position: 'bottom',
    question_spotlight: {
        enabled: false,
        regions: [],
        mask_style: 'dim',
        mask_opacity: 0.92,
        blur_radius: 18,
        feather: 12,
        outline: true,
        answer_reveal: 'expand',
        reveal_seconds: 0.5,
    },
});

const makeBlockId = () => `block-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const makeBookend = (): InteractiveBookend => ({ image_url: '', script: '', translation: '', hold_after: 0 });

const makeSpotlight = (): InteractiveQuestionSpotlight => ({
    enabled: false,
    regions: [],
    mask_style: 'dim',
    mask_opacity: 0.92,
    blur_radius: 18,
    feather: 12,
    outline: true,
    answer_reveal: 'expand',
    reveal_seconds: 0.5,
});

const makeCameraStep = (x = 0.5, y = 0.5, zoom = 1.4): InteractiveCameraStep => ({
    id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    label: '',
    x,
    y,
    zoom,
    script: '',
    translation: '',
    image_url: null,
    swap_style: 'crossfade',
    hold_seconds: null,
    pause_after: 0.2,
});

const makeCameraSequence = (preset: 'journey' | 'zoom' = 'journey'): InteractiveCameraSequence => ({
    preset,
    base_image_url: '',
    steps: preset === 'zoom'
        ? [makeCameraStep(0.5, 0.5, 1.0), makeCameraStep(0.5, 0.5, 3.0)]
        : [makeCameraStep(0.28, 0.5, 1.5), makeCameraStep(0.72, 0.5, 1.5)],
    travel_seconds: preset === 'zoom' ? 1.2 : 1.5,
    arrow_style: preset === 'zoom' ? 'none' : 'both',
    arrow_color: '#F59E0B',
    travel_sound: preset === 'zoom' ? 'none' : 'whoosh',
    fit_mode: 'cover',
    camera_mode: preset === 'zoom' ? 'follow' : 'follow',
});

const getRequestError = (error: unknown): string => {
    const maybe = error as { response?: { data?: { message?: string } }; message?: string };
    return maybe?.response?.data?.message || maybe?.message || String(error);
};

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const normalizedDraft = (draft: DraftRect | null) => {
    if (!draft) return null;
    const x = Math.min(draft.startX, draft.currentX);
    const y = Math.min(draft.startY, draft.currentY);
    return {
        x,
        y,
        width: Math.abs(draft.currentX - draft.startX),
        height: Math.abs(draft.currentY - draft.startY),
    };
};

interface ImageSourceControlsProps {
    taskId: string;
    onChange: (url: string) => void;
    onClear?: () => void;
    clearLabel?: string;
    showClear?: boolean;
    currentUrl?: string;
}

const ImageSourceControls: React.FC<ImageSourceControlsProps> = ({
    taskId,
    onChange,
    onClear,
    clearLabel = 'Clear image',
    showClear = false,
    currentUrl = '',
}) => {
    const [externalUrl, setExternalUrl] = useState('');
    const [uploading, setUploading] = useState(false);

    const handleUpload = async (file: File) => {
        setUploading(true);
        try {
            const res = await uploadImage(file, taskId);
            if (res.success && res.data?.image_url) {
                onChange(res.data.image_url);
                message.success('Image uploaded');
            } else {
                message.error(res.message || 'Image upload failed');
            }
        } catch (error: unknown) {
            message.error('Image upload failed: ' + getRequestError(error));
        } finally {
            setUploading(false);
        }
        return false;
    };

    const useExternalUrl = (value: string) => {
        const trimmed = value.trim();
        if (!/^https?:\/\//i.test(trimmed)) {
            message.error('Enter a valid http:// or https:// image URL');
            return;
        }
        onChange(trimmed);
        setExternalUrl('');
    };

    return (
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
            <Space wrap>
                <Upload beforeUpload={handleUpload} showUploadList={false} accept="image/*">
                    <Button icon={<UploadOutlined />} loading={uploading}>Upload image</Button>
                </Upload>
                {showClear && onClear && (
                    <Button onClick={onClear}>{clearLabel}</Button>
                )}
            </Space>
            <Input.Search
                value={externalUrl}
                onChange={(event) => setExternalUrl(event.target.value)}
                onSearch={useExternalUrl}
                placeholder="Or paste an image URL"
                enterButton="Use URL"
                allowClear
            />
            {currentUrl && (
                <div
                    style={{
                        display: 'flex',
                        gap: 10,
                        alignItems: 'center',
                        padding: 8,
                        background: '#F8FAFC',
                        border: '1px solid #E2E8F0',
                        borderRadius: 8,
                    }}
                >
                    <img
                        src={currentUrl}
                        alt="Selected image"
                        style={{
                            width: 112,
                            height: 84,
                            objectFit: 'contain',
                            background: '#0F172A',
                            borderRadius: 6,
                            flex: '0 0 auto',
                        }}
                    />
                    <Text type="secondary" style={{ fontSize: 11, wordBreak: 'break-all' }}>
                        {currentUrl}
                    </Text>
                </div>
            )}
        </Space>
    );
};

// Mirrors the backend subtitle splitter. Newlines are hard breaks; sentence
// punctuation still splits within each typed line.
const splitSubtitleLines = (script: string): string[] => {
    if (!script) return [];
    const normalized = script.replace(/\r\n?/g, '\n');
    const activePunctuations = ['?', '.', '!', '…', '？', '。', '！', '...'];
    const result: string[] = [];
    let current = '';

    for (let i = 0; i < normalized.length; i += 1) {
        const char = normalized[i];
        if (char === '\n') {
            if (current.trim()) result.push(current.trim());
            current = '';
            continue;
        }
        if (/\s/.test(char)) {
            current += ' ';
            continue;
        }
        const previous = i > 0 ? normalized[i - 1] : '';
        const next = i < normalized.length - 1 ? normalized[i + 1] : '';
        if (char === '.' && /\d/.test(previous) && /\d/.test(next)) {
            current += char;
            continue;
        }
        current += char;
        if (activePunctuations.includes(char)) {
            while (i + 1 < normalized.length && ['\'', '"', '”', '’', ')', ']'].includes(normalized[i + 1])) {
                current += normalized[i + 1];
                i += 1;
            }
            if (current.trim()) result.push(current.trim());
            current = '';
        }
    }
    if (current.trim()) result.push(current.trim());
    return result.filter((line) => /[\w\u4e00-\u9fa5]/.test(line));
};

const SubtitleLinePreview: React.FC<{ script: string }> = ({ script }) => {
    const lines = splitSubtitleLines(script);
    if (!lines.length) return null;
    return (
        <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
                {lines.length} subtitle line{lines.length === 1 ? '' : 's'}
            </Text>
            <div style={{ marginTop: 4, padding: '8px 10px', background: '#FAFAFA', borderRadius: 8, border: '1px solid #F0F0F0' }}>
                {lines.map((line, index) => (
                    <div key={`${index}-${line}`} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 2 }}>
                        <Text type="secondary" style={{ fontSize: 12, minWidth: 18, textAlign: 'right' }}>{index + 1}.</Text>
                        <Text style={{ fontSize: 12, lineHeight: 1.5 }}>{line}</Text>
                    </div>
                ))}
            </div>
            <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                Press Enter in the narration to force a subtitle line break.
            </Text>
        </div>
    );
};

interface BookendSceneCardProps {
    title: string;
    taskId: string;
    value: InteractiveBookend;
    onChange: (value: InteractiveBookend) => void;
    onRemove: () => void;
    onMoveUp: () => void;
    onMoveDown: () => void;
    canMoveUp: boolean;
    canMoveDown: boolean;
}

const BookendSceneCard: React.FC<BookendSceneCardProps> = ({
    title,
    taskId,
    value,
    onChange,
    onRemove,
    onMoveUp,
    onMoveDown,
    canMoveUp,
    canMoveDown,
}) => {
    const [translating, setTranslating] = useState(false);

    const translate = async () => {
        if (!value.script.trim()) {
            message.warning('Write the English narration first.');
            return;
        }
        setTranslating(true);
        try {
            const res = await translateScenes([value.script]);
            if (!res.success || !res.data?.translations) {
                throw new Error(res.message || 'Translation failed');
            }
            onChange({ ...value, translation: res.data.translations[0] || '' });
            message.success(`${title} translation ready`);
        } catch (error: unknown) {
            message.error('Translation failed: ' + getRequestError(error));
        } finally {
            setTranslating(false);
        }
    };

    return (
        <Card
            size="small"
            title={title}
            style={{ marginBottom: 18 }}
            extra={(
                <Space>
                    <Button size="small" icon={<ArrowUpOutlined />} disabled={!canMoveUp} onClick={onMoveUp} />
                    <Button size="small" icon={<ArrowDownOutlined />} disabled={!canMoveDown} onClick={onMoveDown} />
                    <Button size="small" icon={<TranslationOutlined />} loading={translating} onClick={translate}>
                        Translate
                    </Button>
                    <Button size="small" danger icon={<DeleteOutlined />} onClick={onRemove}>
                        Remove
                    </Button>
                </Space>
            )}
        >
            <Row gutter={20}>
                <Col xs={24} lg={10}>
                    {value.image_url ? (
                        <div style={{ background: '#0F172A', borderRadius: 10, padding: 8, textAlign: 'center' }}>
                            <img
                                src={value.image_url}
                                alt={`${title} preview`}
                                draggable={false}
                                style={{ display: 'inline-block', maxWidth: '100%', maxHeight: 300, width: 'auto', height: 'auto' }}
                            />
                        </div>
                    ) : (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload an image" />
                    )}
                    <div style={{ marginTop: 10 }}>
                        <ImageSourceControls
                            taskId={taskId}
                            onChange={(url) => onChange({ ...value, image_url: url })}
                            showClear={!!value.image_url}
                            clearLabel="Clear image"
                            onClear={() => onChange({ ...value, image_url: '' })}
                        />
                    </div>
                </Col>
                <Col xs={24} lg={14}>
                    <div style={{ marginBottom: 6 }}>English narration</div>
                    <Input.TextArea
                        value={value.script}
                        onChange={(event) => onChange({ ...value, script: event.target.value })}
                        placeholder={title.startsWith('Intro')
                            ? 'Welcome! Today we will play a guessing game.'
                            : 'Great job! See you next time.'}
                        autoSize={{ minRows: 3, maxRows: 7 }}
                    />
                    <SubtitleLinePreview script={value.script} />
                    <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                    <Input.TextArea
                        value={value.translation}
                        onChange={(event) => onChange({ ...value, translation: event.target.value })}
                        placeholder="Use Translate, or type one line per English subtitle line"
                        autoSize={{ minRows: 2, maxRows: 7 }}
                    />
                    <Row gutter={12} style={{ marginTop: 12 }} align="bottom">
                        <Col span={10}>
                            <div style={{ marginBottom: 4 }}>Hold after voice</div>
                            <InputNumber
                                min={0}
                                max={10}
                                step={0.1}
                                value={value.hold_after ?? 0}
                                onChange={(hold_after) => onChange({ ...value, hold_after: Number(hold_after) || 0 })}
                                addonAfter="s"
                                style={{ width: '100%' }}
                            />
                        </Col>
                        <Col span={14}>
                            <Text type="secondary">
                                Keeps the picture on screen after the narration before the next block starts.
                            </Text>
                        </Col>
                    </Row>
                </Col>
            </Row>
        </Card>
    );
};

interface BlockActionsProps {
    canMoveUp: boolean;
    canMoveDown: boolean;
    onMoveUp: () => void;
    onMoveDown: () => void;
    onRemove: () => void;
    children?: React.ReactNode;
}

const BlockActions: React.FC<BlockActionsProps> = ({
    canMoveUp,
    canMoveDown,
    onMoveUp,
    onMoveDown,
    onRemove,
    children,
}) => (
    <Space wrap>
        <Button size="small" icon={<ArrowUpOutlined />} disabled={!canMoveUp} onClick={onMoveUp} />
        <Button size="small" icon={<ArrowDownOutlined />} disabled={!canMoveDown} onClick={onMoveDown} />
        {children}
        <Popconfirm title="Delete this block?" onConfirm={onRemove}>
            <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
    </Space>
);

interface SpotlightPreviewProps {
    imageUrl: string;
    regions: InteractiveHighlight[];
    draft: { x: number; y: number; width: number; height: number } | null;
    shape: HighlightShape;
    color: string;
    maskStyle: 'dim' | 'blur' | 'solid';
    opacity: number;
    maskId: string;
    onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
    onPointerMove: (event: React.PointerEvent<HTMLDivElement>) => void;
    onPointerUp: (event: React.PointerEvent<HTMLDivElement>) => void;
    onPointerCancel: () => void;
    onRemove: (index: number) => void;
}

const SpotlightPreview: React.FC<SpotlightPreviewProps> = ({
    imageUrl,
    regions,
    draft,
    shape,
    color,
    maskStyle,
    opacity,
    maskId,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    onRemove,
}) => {
    if (!imageUrl) {
        return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload a question image first" />;
    }
    const overlayOpacity = maskStyle === 'solid' ? 1 : (maskStyle === 'blur' ? Math.min(0.75, opacity) : opacity);
    return (
        <div style={{ background: '#0F172A', borderRadius: 10, padding: 8, display: 'flex', justifyContent: 'center' }}>
            <div
                style={{
                    position: 'relative',
                    display: 'inline-block',
                    maxWidth: '100%',
                    cursor: 'crosshair',
                    touchAction: 'none',
                    userSelect: 'none',
                }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerCancel}
            >
                <img
                    src={imageUrl}
                    alt="Question spotlight preview"
                    draggable={false}
                    style={{ display: 'block', maxWidth: '100%', maxHeight: 430, width: 'auto', height: 'auto' }}
                />
                <svg
                    viewBox="0 0 100 100"
                    preserveAspectRatio="none"
                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                >
                    <defs>
                        <mask id={maskId}>
                            <rect x="0" y="0" width="100" height="100" fill="white" />
                            {regions.map((region, index) => region.shape === 'ellipse' ? (
                                <ellipse
                                    key={`hole-${index}`}
                                    cx={(region.x + region.width / 2) * 100}
                                    cy={(region.y + region.height / 2) * 100}
                                    rx={(region.width / 2) * 100}
                                    ry={(region.height / 2) * 100}
                                    fill="black"
                                />
                            ) : (
                                <rect
                                    key={`hole-${index}`}
                                    x={region.x * 100}
                                    y={region.y * 100}
                                    width={region.width * 100}
                                    height={region.height * 100}
                                    fill="black"
                                />
                            ))}
                        </mask>
                    </defs>
                    <rect x="0" y="0" width="100" height="100" fill="rgba(8,15,23,0.96)" opacity={overlayOpacity} mask={`url(#${maskId})`} />
                </svg>
                {regions.map((region, index) => (
                    <div
                        key={`spot-${index}-${region.x}-${region.y}`}
                        style={{
                            position: 'absolute',
                            left: `${region.x * 100}%`,
                            top: `${region.y * 100}%`,
                            width: `${region.width * 100}%`,
                            height: `${region.height * 100}%`,
                            border: `3px solid ${color}`,
                            borderRadius: region.shape === 'ellipse' ? '50%' : 8,
                            boxShadow: '0 0 0 2px rgba(15, 23, 42, 0.65)',
                            pointerEvents: 'none',
                            boxSizing: 'border-box',
                        }}
                    >
                        <button
                            type="button"
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) => {
                                event.stopPropagation();
                                onRemove(index);
                            }}
                            style={{
                                position: 'absolute',
                                right: -12,
                                top: -12,
                                width: 24,
                                height: 24,
                                borderRadius: '50%',
                                border: 'none',
                                background: '#DC2626',
                                color: 'white',
                                cursor: 'pointer',
                                lineHeight: '24px',
                                padding: 0,
                                pointerEvents: 'auto',
                            }}
                            aria-label="Remove spotlight"
                        >
                            ×
                        </button>
                    </div>
                ))}
                {draft && (
                    <div
                        style={{
                            position: 'absolute',
                            left: `${draft.x * 100}%`,
                            top: `${draft.y * 100}%`,
                            width: `${draft.width * 100}%`,
                            height: `${draft.height * 100}%`,
                            border: `2px dashed ${color}`,
                            borderRadius: shape === 'ellipse' ? '50%' : 8,
                            background: `${color}22`,
                            pointerEvents: 'none',
                            boxSizing: 'border-box',
                        }}
                    />
                )}
            </div>
        </div>
    );
};

interface CameraSequencePreviewProps {
    open: boolean;
    sequence: InteractiveCameraSequence;
    resolution: string;
    onClose: () => void;
}

interface CameraPreviewSegment {
    kind: 'hold' | 'travel';
    start: number;
    end: number;
    index: number;
}

const easeCameraPreview = (value: number) => (
    value < 0.5 ? 2 * value * value : 1 - Math.pow(-2 * value + 2, 2) / 2
);

const estimateCameraHold = (step: InteractiveCameraStep) => {
    if (typeof step.hold_seconds === 'number' && step.hold_seconds > 0) return step.hold_seconds;
    const words = step.script.trim() ? step.script.trim().split(/\s+/).length : 0;
    return Math.max(1.1, Math.min(6, words * 0.36));
};

const buildCameraPreviewTimeline = (sequence: InteractiveCameraSequence) => {
    const segments: CameraPreviewSegment[] = [];
    let cursor = 0;
    sequence.steps.forEach((step, index) => {
        const hold = Math.max(0.4, estimateCameraHold(step));
        const pause = Math.max(0, Number(step.pause_after) || 0);
        const holdEnd = cursor + hold + pause;
        segments.push({ kind: 'hold', start: cursor, end: holdEnd, index });
        cursor = holdEnd;
        if (index < sequence.steps.length - 1) {
            const travel = Math.max(0.2, Number(sequence.travel_seconds) || 1.5);
            segments.push({ kind: 'travel', start: cursor, end: cursor + travel, index });
            cursor += travel;
        }
    });
    return { segments, duration: Math.max(0.1, cursor) };
};

const parsePreviewAspect = (resolution: string) => {
    const match = /^(\d+)\s*[*xX]\s*(\d+)$/.exec((resolution || '').trim());
    if (!match) return 1;
    const width = Number(match[1]);
    const height = Number(match[2]);
    if (!width || !height) return 1;
    return Math.max(0.4, Math.min(2.5, width / height));
};

const CameraSequencePreview: React.FC<CameraSequencePreviewProps> = ({
    open,
    sequence,
    resolution,
    onClose,
}) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const imagesRef = useRef(new Map<string, HTMLImageElement>());
    const preparedCacheRef = useRef(new Map<string, HTMLCanvasElement>());
    const offsetRef = useRef(0);
    const playingRef = useRef(true);
    const timelineRef = useRef(buildCameraPreviewTimeline(sequence));
    timelineRef.current = buildCameraPreviewTimeline(sequence);

    const [imagesReady, setImagesReady] = useState(false);
    const [playing, setPlaying] = useState(true);
    const [elapsed, setElapsed] = useState(0);

    const canvasWidth = 720;
    const canvasHeight = Math.max(320, Math.min(720, Math.round(canvasWidth / parsePreviewAspect(resolution))));
    const imageSignature = [sequence.base_image_url, ...sequence.steps.map((step) => step.image_url || '')].join('|');
    const sequenceSignature = JSON.stringify(sequence);

    useEffect(() => {
        playingRef.current = playing;
    }, [playing]);

    useEffect(() => {
        if (!open) return;
        offsetRef.current = 0;
        setElapsed(0);
        setPlaying(true);
        playingRef.current = true;
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const urls = Array.from(new Set(
            [sequence.base_image_url, ...sequence.steps.map((step) => step.image_url || '')].filter(Boolean),
        ));
        let cancelled = false;
        setImagesReady(false);
        preparedCacheRef.current.clear();
        Promise.all(urls.map((url) => new Promise<void>((resolve) => {
            const image = new Image();
            image.onload = () => {
                imagesRef.current.set(url, image);
                resolve();
            };
            image.onerror = () => resolve();
            image.src = url;
        }))).then(() => {
            if (!cancelled) setImagesReady(true);
        });
        return () => {
            cancelled = true;
        };
    }, [open, imageSignature, canvasHeight]);

    const getPrepared = (url: string) => {
        if (!url) return null;
        const cacheKey = `${sequence.fit_mode}|${canvasWidth}x${canvasHeight}|${url}`;
        const cached = preparedCacheRef.current.get(cacheKey);
        if (cached) return cached;
        const image = imagesRef.current.get(url);
        if (!image) return null;
        const canvas = document.createElement('canvas');
        canvas.width = canvasWidth;
        canvas.height = canvasHeight;
        const context = canvas.getContext('2d');
        if (!context) return null;
        const coverScale = Math.max(canvasWidth / image.width, canvasHeight / image.height);
        if (sequence.fit_mode === 'contain') {
            context.save();
            context.filter = 'blur(18px) brightness(0.55)';
            context.drawImage(
                image,
                (canvasWidth - image.width * coverScale) / 2,
                (canvasHeight - image.height * coverScale) / 2,
                image.width * coverScale,
                image.height * coverScale,
            );
            context.restore();
            const containScale = Math.min(canvasWidth / image.width, canvasHeight / image.height);
            context.drawImage(
                image,
                (canvasWidth - image.width * containScale) / 2,
                (canvasHeight - image.height * containScale) / 2,
                image.width * containScale,
                image.height * containScale,
            );
        } else {
            context.drawImage(
                image,
                (canvasWidth - image.width * coverScale) / 2,
                (canvasHeight - image.height * coverScale) / 2,
                image.width * coverScale,
                image.height * coverScale,
            );
        }
        preparedCacheRef.current.set(cacheKey, canvas);
        return canvas;
    };

    const drawCameraImage = (
        context: CanvasRenderingContext2D,
        prepared: HTMLCanvasElement,
        zoomValue: number,
        centerX: number,
        centerY: number,
    ) => {
        const zoom = Math.max(1, zoomValue);
        const viewWidth = canvasWidth / zoom;
        const viewHeight = canvasHeight / zoom;
        const left = Math.max(0, Math.min(canvasWidth - viewWidth, centerX * canvasWidth - viewWidth / 2));
        const top = Math.max(0, Math.min(canvasHeight - viewHeight, centerY * canvasHeight - viewHeight / 2));
        context.drawImage(prepared, left, top, viewWidth, viewHeight, 0, 0, canvasWidth, canvasHeight);
    };

    const drawArrows = (
        context: CanvasRenderingContext2D,
        index: number,
        progress: number,
        zoomValue: number,
        centerX: number,
        centerY: number,
        moving: boolean,
    ) => {
        const style = (sequence.arrow_style || 'none').toLowerCase();
        if (style === 'none' || sequence.steps.length < 2) return;
        const zoom = Math.max(1, zoomValue);
        const project = (x: number, y: number): [number, number] => [
            (x * canvasWidth - centerX * canvasWidth) * zoom + canvasWidth / 2,
            (y * canvasHeight - centerY * canvasHeight) * zoom + canvasHeight / 2,
        ];
        const color = sequence.arrow_color || '#F59E0B';
        context.save();
        if (style === 'route' || style === 'both') {
            context.beginPath();
            context.setLineDash([10, 7]);
            context.lineWidth = 3;
            context.strokeStyle = color;
            sequence.steps.forEach((step, stepIndex) => {
                const [px, py] = project(step.x, step.y);
                if (stepIndex === 0) context.moveTo(px, py);
                else context.lineTo(px, py);
            });
            context.stroke();
            context.setLineDash([]);
        }
        if ((style === 'chevron' || style === 'both') && moving && index < sequence.steps.length - 1) {
            const from = sequence.steps[index];
            const to = sequence.steps[index + 1];
            const [x1, y1] = project(from.x, from.y);
            const [x2, y2] = project(to.x, to.y);
            const midX = x1 + (x2 - x1) * progress;
            const midY = y1 + (y2 - y1) * progress;
            const angle = Math.atan2(y2 - y1, x2 - x1);
            context.translate(midX, midY);
            context.rotate(angle);
            context.strokeStyle = color;
            context.lineWidth = 4;
            context.lineCap = 'round';
            [-9, 5].forEach((shift) => {
                context.beginPath();
                context.moveTo(shift - 7, -10);
                context.lineTo(shift + 5, 0);
                context.lineTo(shift - 7, 10);
                context.stroke();
            });
        }
        context.restore();
    };

    const drawPreview = (time: number) => {
        const canvas = canvasRef.current;
        const context = canvas?.getContext('2d');
        if (!canvas || !context) return;
        context.clearRect(0, 0, canvasWidth, canvasHeight);
        context.fillStyle = '#0F172A';
        context.fillRect(0, 0, canvasWidth, canvasHeight);
        const steps = sequence.steps;
        if (!steps.length) return;
        const timeline = timelineRef.current;
        const segment = timeline.segments.find((item, index) => (
            time < item.end || index === timeline.segments.length - 1
        )) || timeline.segments[0];
        if (!segment) return;
        const current = steps[segment.index];
        let zoom = Math.max(1, Number(current.zoom) || 1);
        let centerX = current.x;
        let centerY = current.y;
        let progress = 0;
        if (segment.kind === 'travel') {
            const next = steps[segment.index + 1];
            progress = easeCameraPreview(Math.max(0, Math.min(1, (time - segment.start) / Math.max(1e-6, segment.end - segment.start))));
            zoom = (Number(current.zoom) || 1) + ((Number(next.zoom) || 1) - (Number(current.zoom) || 1)) * progress;
            centerX = current.x + (next.x - current.x) * progress;
            centerY = current.y + (next.y - current.y) * progress;
        }
        const currentUrl = current.image_url || sequence.base_image_url;
        const preparedCurrent = getPrepared(currentUrl);
        if (!preparedCurrent) return;
        if (segment.kind === 'travel' && steps[segment.index + 1]) {
            const nextUrl = steps[segment.index + 1].image_url || sequence.base_image_url;
            const preparedNext = getPrepared(nextUrl);
            if (preparedNext && nextUrl !== currentUrl) {
                const swap = (steps[segment.index + 1].swap_style || 'crossfade').toLowerCase();
                if (swap === 'cut') {
                    drawCameraImage(context, progress < 0.5 ? preparedCurrent : preparedNext, zoom, centerX, centerY);
                } else if (swap === 'circle_reveal') {
                    drawCameraImage(context, preparedCurrent, zoom, centerX, centerY);
                    context.save();
                    context.beginPath();
                    const radius = progress * Math.hypot(canvasWidth, canvasHeight) * 0.75;
                    context.arc(centerX * canvasWidth, centerY * canvasHeight, radius, 0, Math.PI * 2);
                    context.clip();
                    drawCameraImage(context, preparedNext, zoom, centerX, centerY);
                    context.restore();
                } else {
                    drawCameraImage(context, preparedCurrent, zoom, centerX, centerY);
                    context.save();
                    context.globalAlpha = progress;
                    drawCameraImage(context, preparedNext, zoom, centerX, centerY);
                    context.restore();
                }
            } else {
                drawCameraImage(context, preparedCurrent, zoom, centerX, centerY);
            }
        } else {
            drawCameraImage(context, preparedCurrent, zoom, centerX, centerY);
        }
        drawArrows(context, segment.index, progress, zoom, centerX, centerY, segment.kind === 'travel');
    };

    useEffect(() => {
        if (!open || !imagesReady) return;
        let frame = 0;
        let last = performance.now();
        const tick = (now: number) => {
            const delta = Math.min(0.1, Math.max(0, (now - last) / 1000));
            last = now;
            if (playingRef.current) {
                const next = Math.min(timelineRef.current.duration, offsetRef.current + delta);
                offsetRef.current = next;
                setElapsed(next);
                if (next >= timelineRef.current.duration) {
                    setPlaying(false);
                }
            }
            drawPreview(offsetRef.current);
            frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(frame);
    }, [open, imagesReady, sequenceSignature]);

    const seek = (value: number) => {
        offsetRef.current = value;
        setElapsed(value);
        drawPreview(value);
    };
    const restart = () => {
        offsetRef.current = 0;
        setElapsed(0);
        setPlaying(true);
    };
    const activeSegment = timelineRef.current.segments.find((item, index) => (
        elapsed < item.end || index === timelineRef.current.segments.length - 1
    ));
    const activeStep = activeSegment ? activeSegment.index + 1 : 1;

    return (
        <Modal
            open={open}
            onCancel={onClose}
            footer={null}
            width={860}
            title="Camera sequence preview"
        >
            <div style={{ background: '#0F172A', borderRadius: 10, padding: 10, display: 'flex', justifyContent: 'center' }}>
                <canvas
                    ref={canvasRef}
                    width={canvasWidth}
                    height={canvasHeight}
                    style={{ width: '100%', maxWidth: 720, borderRadius: 8 }}
                />
            </div>
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <Button onClick={() => setPlaying((previous) => !previous)}>{playing ? 'Pause' : 'Play'}</Button>
                <Button onClick={restart}>Restart</Button>
                <Text type="secondary">Step {activeStep} / {sequence.steps.length}</Text>
                <Text type="secondary">{elapsed.toFixed(2)}s / {timelineRef.current.duration.toFixed(2)}s</Text>
            </div>
            <Slider
                min={0}
                max={timelineRef.current.duration}
                step={0.01}
                value={elapsed}
                onChange={seek}
                tooltip={{ formatter: (value) => `${Number(value).toFixed(2)}s` }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
                Preview timing is estimated from each step&apos;s hold time and narration length.
                The final video uses the generated voice duration.
            </Text>
        </Modal>
    );
};

interface CameraSequenceCardProps {
    title: string;
    taskId: string;
    resolution: string;
    value: InteractiveCameraSequence;
    onChange: (value: InteractiveCameraSequence) => void;
    onRemove: () => void;
    onMoveUp: () => void;
    onMoveDown: () => void;
    canMoveUp: boolean;
    canMoveDown: boolean;
    onTranslate: () => void;
    translating: boolean;
}

const CameraSequenceCard: React.FC<CameraSequenceCardProps> = ({
    title,
    taskId,
    resolution,
    value,
    onChange,
    onRemove,
    onMoveUp,
    onMoveDown,
    canMoveUp,
    canMoveDown,
    onTranslate,
    translating,
}) => {
    const [previewOpen, setPreviewOpen] = useState(false);
    const imageRef = useRef<HTMLDivElement>(null);
    const draggingIndexRef = useRef<number | null>(null);
    const [selectedStep, setSelectedStep] = useState<number | null>(null);
    const [draggingStep, setDraggingStep] = useState<number | null>(null);

    useEffect(() => {
        setSelectedStep((previous) => (
            previous !== null && previous < value.steps.length ? previous : null
        ));
    }, [value.steps.length]);

    const updateStep = (index: number, patch: Partial<InteractiveCameraStep>) => {
        onChange({
            ...value,
            steps: value.steps.map((step, stepIndex) => stepIndex === index ? { ...step, ...patch } : step),
        });
    };
    const addStep = (x = 0.5, y = 0.5) => {
        const lastZoom = value.steps[value.steps.length - 1]?.zoom || 1.4;
        onChange({ ...value, steps: [...value.steps, makeCameraStep(x, y, lastZoom)] });
        return value.steps.length;
    };
    const removeStep = (index: number) => {
        onChange({
            ...value,
            steps: value.steps.filter((_, stepIndex) => stepIndex !== index),
        });
        setSelectedStep((previous) => {
            if (previous === null) return null;
            if (previous === index) return null;
            return previous > index ? previous - 1 : previous;
        });
    };
    const moveStep = (index: number, direction: -1 | 1) => {
        const target = index + direction;
        if (target < 0 || target >= value.steps.length) return;
        const next = [...value.steps];
        [next[index], next[target]] = [next[target], next[index]];
        onChange({ ...value, steps: next });
    };
    const pointFromEvent = (event: React.MouseEvent<HTMLDivElement> | React.PointerEvent<HTMLDivElement>) => {
        const rect = imageRef.current?.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        return {
            x: clamp01((event.clientX - rect.left) / rect.width),
            y: clamp01((event.clientY - rect.top) / rect.height),
        };
    };
    const handleImageClick = (event: React.MouseEvent<HTMLDivElement>) => {
        if (draggingIndexRef.current !== null) return;
        const point = pointFromEvent(event);
        if (!point) return;
        event.stopPropagation();
        if (selectedStep !== null && selectedStep < value.steps.length) {
            updateStep(selectedStep, point);
            return;
        }
        if (value.preset === 'zoom' && value.steps.length >= 2) {
            updateStep(value.steps.length - 1, point);
            return;
        }
        addStep(point.x, point.y);
    };
    const handleMarkerPointerDown = (event: React.PointerEvent<HTMLDivElement>, index: number) => {
        event.stopPropagation();
        event.preventDefault();
        draggingIndexRef.current = index;
        setDraggingStep(index);
        setSelectedStep(index);
        event.currentTarget.setPointerCapture(event.pointerId);
    };
    const handleMarkerPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
        const index = draggingIndexRef.current;
        if (index === null) return;
        const point = pointFromEvent(event);
        if (!point) return;
        event.stopPropagation();
        updateStep(index, point);
    };
    const handleMarkerPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
        if (draggingIndexRef.current === null) return;
        draggingIndexRef.current = null;
        setDraggingStep(null);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
        }
    };
    const markerOffset = (index: number) => {
        const current = value.steps[index];
        if (!current) return { x: 0, y: 0 };
        const group = value.steps
            .map((step, stepIndex) => ({ step, stepIndex }))
            .filter(({ step }) => Math.abs(step.x - current.x) < 0.004 && Math.abs(step.y - current.y) < 0.004)
            .map(({ stepIndex }) => stepIndex);
        if (group.length < 2) return { x: 0, y: 0 };
        const position = group.indexOf(index);
        const radius = 18;
        const angle = -Math.PI / 2 + (2 * Math.PI * position) / group.length;
        return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
    };
    const routeSegments = value.steps.slice(1)
        .map((step, index) => ({ from: value.steps[index], to: step, key: `${index}-${index + 1}` }))
        .filter(({ from, to }) => Math.hypot(to.x - from.x, to.y - from.y) > 0.01);

    return (
        <Card
            size="small"
            title={title}
            style={{ marginBottom: 18 }}
            extra={(
                <BlockActions
                    canMoveUp={canMoveUp}
                    canMoveDown={canMoveDown}
                    onMoveUp={onMoveUp}
                    onMoveDown={onMoveDown}
                    onRemove={onRemove}
                >
                    <Button size="small" onClick={() => setPreviewOpen(true)} disabled={!value.base_image_url || !value.steps.length}>
                        Preview
                    </Button>
                    <Button size="small" icon={<TranslationOutlined />} loading={translating} onClick={onTranslate}>
                        Translate
                    </Button>
                </BlockActions>
            )}
        >
            <Row gutter={20}>
                <Col xs={24} lg={11}>
                    {value.base_image_url ? (
                        <div
                            style={{ background: '#0F172A', borderRadius: 10, padding: 8, textAlign: 'center' }}
                            onClick={() => setSelectedStep(null)}
                            title="Click outside the picture to clear the selected step"
                        >
                            <div
                                ref={imageRef}
                                onClick={handleImageClick}
                                style={{
                                    position: 'relative',
                                    display: 'inline-block',
                                    maxWidth: '100%',
                                    cursor: draggingStep !== null ? 'grabbing' : 'crosshair',
                                }}
                            >
                                <img
                                    src={value.base_image_url}
                                    alt={`${title} path preview`}
                                    draggable={false}
                                    style={{ display: 'block', maxWidth: '100%', maxHeight: 420, width: 'auto', height: 'auto' }}
                                />
                                <svg
                                    viewBox="0 0 100 100"
                                    preserveAspectRatio="none"
                                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                                >
                                    {routeSegments.map(({ from, to, key }) => (
                                        <line
                                            key={key}
                                            x1={from.x * 100}
                                            y1={from.y * 100}
                                            x2={to.x * 100}
                                            y2={to.y * 100}
                                            stroke={value.arrow_color}
                                            strokeWidth="0.8"
                                            strokeDasharray="2.2 1.6"
                                            strokeLinecap="round"
                                        />
                                    ))}
                                </svg>
                                {value.steps.map((step, index) => {
                                    const offset = markerOffset(index);
                                    const selected = selectedStep === index;
                                    return (
                                        <div
                                            key={step.id || index}
                                            onClick={(event) => event.stopPropagation()}
                                            onPointerDown={(event) => handleMarkerPointerDown(event, index)}
                                            onPointerMove={handleMarkerPointerMove}
                                            onPointerUp={handleMarkerPointerUp}
                                            onPointerCancel={handleMarkerPointerUp}
                                            style={{
                                                position: 'absolute',
                                                left: `${step.x * 100}%`,
                                                top: `${step.y * 100}%`,
                                                transform: `translate(-50%, -50%) translate(${offset.x}px, ${offset.y}px)`,
                                                width: 30,
                                                height: 30,
                                                borderRadius: '50%',
                                                background: value.arrow_color,
                                                color: '#111827',
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                fontWeight: 700,
                                                boxShadow: selected
                                                    ? '0 0 0 4px rgba(255,255,255,0.4), 0 2px 8px rgba(0,0,0,0.45)'
                                                    : '0 2px 8px rgba(0,0,0,0.45)',
                                                border: selected ? '3px solid #FFFFFF' : '2px solid #FFF7E6',
                                                pointerEvents: 'auto',
                                                cursor: draggingStep === index ? 'grabbing' : 'grab',
                                                touchAction: 'none',
                                                userSelect: 'none',
                                            }}
                                            title={step.label || `Step ${index + 1} — drag to fine-tune`}
                                        >
                                            {index + 1}
                                            <button
                                                type="button"
                                                aria-label={`Delete step ${index + 1}`}
                                                onPointerDown={(event) => event.stopPropagation()}
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    if (value.steps.length > 1) removeStep(index);
                                                }}
                                                style={{
                                                    position: 'absolute',
                                                    top: -7,
                                                    right: -7,
                                                    width: 17,
                                                    height: 17,
                                                    borderRadius: '50%',
                                                    border: '1px solid #FEE2E2',
                                                    background: value.steps.length > 1 ? '#DC2626' : '#9CA3AF',
                                                    color: '#FFFFFF',
                                                    fontSize: 11,
                                                    lineHeight: '13px',
                                                    padding: 0,
                                                    cursor: value.steps.length > 1 ? 'pointer' : 'not-allowed',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    justifyContent: 'center',
                                                }}
                                            >
                                                ×
                                            </button>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    ) : (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload a base image" />
                    )}
                    <div style={{ marginTop: 10 }}>
                        <ImageSourceControls
                            taskId={taskId}
                            onChange={(url) => onChange({ ...value, base_image_url: url })}
                            showClear={!!value.base_image_url}
                            clearLabel="Clear image"
                            onClear={() => onChange({ ...value, base_image_url: '' })}
                        />
                    </div>
                    {selectedStep !== null ? (
                        <Space wrap style={{ marginTop: 8 }}>
                            <Tag color="blue">Step {selectedStep + 1} selected</Tag>
                            <Text type="secondary">Click the picture to move this step&apos;s center, or drag the marker.</Text>
                            <Button size="small" onClick={() => setSelectedStep(null)}>Deselect</Button>
                        </Space>
                    ) : (
                        <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
                            Click the picture to set focus. Journey clicks add a stop; Zoom clicks move the last step.
                            Drag markers to fine-tune, or click × to delete one.
                        </Text>
                    )}
                </Col>
                <Col xs={24} lg={13}>
                    <Row gutter={12}>
                        <Col span={12}>
                            <div style={{ marginBottom: 6 }}>Preset</div>
                            <Select
                                value={value.preset}
                                onChange={(preset) => onChange({ ...value, preset })}
                                style={{ width: '100%' }}
                                options={[
                                    { value: 'journey', label: 'Journey — travel across the picture' },
                                    { value: 'zoom', label: 'Zoom — move closer / enter details' },
                                    { value: 'custom', label: 'Custom camera sequence' },
                                ]}
                            />
                        </Col>
                        <Col span={12}>
                            <div style={{ marginBottom: 6 }}>Camera mode</div>
                            <Select
                                value={value.camera_mode}
                                onChange={(camera_mode) => onChange({ ...value, camera_mode })}
                                style={{ width: '100%' }}
                                options={[
                                    { value: 'follow', label: 'Follow the marker' },
                                    { value: 'wide', label: 'Wide view during travel' },
                                ]}
                            />
                        </Col>
                    </Row>
                    <Row gutter={12} style={{ marginTop: 10 }}>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Travel time</div>
                            <InputNumber
                                min={0.2}
                                max={10}
                                step={0.1}
                                value={value.travel_seconds}
                                onChange={(travel_seconds) => onChange({ ...value, travel_seconds: Number(travel_seconds) || 1.5 })}
                                addonAfter="s"
                                style={{ width: '100%' }}
                            />
                        </Col>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Arrow</div>
                            <Select
                                value={value.arrow_style}
                                onChange={(arrow_style) => onChange({ ...value, arrow_style })}
                                style={{ width: '100%' }}
                                options={[
                                    { value: 'both', label: 'Route + double chevron' },
                                    { value: 'chevron', label: 'Moving double chevron' },
                                    { value: 'route', label: 'Route line' },
                                    { value: 'none', label: 'No arrow' },
                                ]}
                            />
                        </Col>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Arrow color</div>
                            <ColorPicker
                                value={value.arrow_color}
                                onChange={(color) => onChange({ ...value, arrow_color: color.toHexString() })}
                                showText
                            />
                        </Col>
                    </Row>
                    <Row gutter={12} style={{ marginTop: 10 }}>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Travel sound</div>
                            <Select
                                value={value.travel_sound}
                                onChange={(travel_sound) => onChange({ ...value, travel_sound })}
                                style={{ width: '100%' }}
                                options={[
                                    { value: 'none', label: 'Silent' },
                                    { value: 'whoosh', label: 'Whoosh' },
                                    { value: 'footsteps', label: 'Footsteps' },
                                ]}
                            />
                        </Col>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Image fit</div>
                            <Select
                                value={value.fit_mode}
                                onChange={(fit_mode) => onChange({ ...value, fit_mode })}
                                style={{ width: '100%' }}
                                options={[
                                    { value: 'cover', label: 'Cover frame' },
                                    { value: 'contain', label: 'Show whole image' },
                                ]}
                            />
                        </Col>
                        <Col span={8}>
                            <div style={{ marginBottom: 6 }}>Steps</div>
                            <Button icon={<PlusOutlined />} onClick={() => setSelectedStep(addStep())} style={{ width: '100%' }}>
                                Add step
                            </Button>
                        </Col>
                    </Row>
                </Col>
            </Row>

            <Divider orientation="left">Camera steps</Divider>
            {value.steps.map((step, index) => {
                const hasDetailImage = step.image_url !== null && step.image_url !== undefined;
                return (
                    <Card
                        key={step.id || index}
                        size="small"
                        title={`Step ${index + 1}${step.label ? ` — ${step.label}` : ''}`}
                        style={{ marginBottom: 12 }}
                        extra={(
                            <Space>
                                <Button
                                    size="small"
                                    type={selectedStep === index ? 'primary' : 'default'}
                                    onClick={() => setSelectedStep(index)}
                                >
                                    Locate
                                </Button>
                                <Button size="small" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => moveStep(index, -1)} />
                                <Button size="small" icon={<ArrowDownOutlined />} disabled={index === value.steps.length - 1} onClick={() => moveStep(index, 1)} />
                                <Button size="small" danger icon={<DeleteOutlined />} disabled={value.steps.length <= 1} onClick={() => removeStep(index)} />
                            </Space>
                        )}
                    >
                        <Row gutter={16}>
                            <Col xs={24} md={10}>
                                <div style={{ marginBottom: 6 }}>Label (optional)</div>
                                <Input
                                    value={step.label}
                                    onChange={(event) => updateStep(index, { label: event.target.value })}
                                    placeholder={value.preset === 'zoom' ? 'e.g. Root detail' : 'e.g. Kitchen'}
                                />
                                <Row gutter={8} style={{ marginTop: 10 }}>
                                    <Col span={12}>
                                        <div style={{ marginBottom: 4 }}>Center X (fine tune): {Math.round(step.x * 100)}%</div>
                                        <Slider min={0} max={1} step={0.01} value={step.x} onChange={(x) => updateStep(index, { x })} />
                                    </Col>
                                    <Col span={12}>
                                        <div style={{ marginBottom: 4 }}>Center Y (fine tune): {Math.round(step.y * 100)}%</div>
                                        <Slider min={0} max={1} step={0.01} value={step.y} onChange={(y) => updateStep(index, { y })} />
                                    </Col>
                                </Row>
                                <div style={{ marginBottom: 4 }}>Zoom: {step.zoom.toFixed(2)}x</div>
                                <Slider min={1} max={8} step={0.05} value={step.zoom} onChange={(zoom) => updateStep(index, { zoom })} />
                                <Row gutter={8} style={{ marginTop: 8 }}>
                                    <Col span={12}>
                                        <div style={{ marginBottom: 4 }}>Hold after voice</div>
                                        <InputNumber
                                            min={0}
                                            max={10}
                                            step={0.1}
                                            value={step.pause_after}
                                            onChange={(pause_after) => updateStep(index, { pause_after: Number(pause_after) || 0 })}
                                            addonAfter="s"
                                            style={{ width: '100%' }}
                                        />
                                    </Col>
                                    <Col span={12}>
                                        <div style={{ marginBottom: 4 }}>Swap style</div>
                                        <Select
                                            value={step.swap_style}
                                            onChange={(swap_style) => updateStep(index, { swap_style })}
                                            style={{ width: '100%' }}
                                            disabled={!hasDetailImage}
                                            options={[
                                                { value: 'crossfade', label: 'Crossfade into detail' },
                                                { value: 'circle_reveal', label: 'Circle reveal' },
                                                { value: 'cut', label: 'Hard cut' },
                                            ]}
                                        />
                                    </Col>
                                </Row>
                                <div style={{ marginTop: 10 }}>
                                    <Space>
                                        <Switch
                                            checked={hasDetailImage}
                                            onChange={(checked) => updateStep(index, { image_url: checked ? '' : null })}
                                        />
                                        <Text>Use another image from this step</Text>
                                    </Space>
                                </div>
                                {hasDetailImage && (
                                    <div style={{ marginTop: 8 }}>
                                        <ImageSourceControls
                                            taskId={taskId}
                                            onChange={(url) => updateStep(index, { image_url: url })}
                                            showClear={!!step.image_url}
                                            clearLabel="Clear detail image"
                                            onClear={() => updateStep(index, { image_url: '' })}
                                            currentUrl={step.image_url || ''}
                                        />
                                        {!step.image_url && (
                                            <Text type="danger" style={{ fontSize: 12 }}>
                                                Upload the detail image for the “enter the object” effect.
                                            </Text>
                                        )}
                                    </div>
                                )}
                            </Col>
                            <Col xs={24} md={14}>
                                <div style={{ marginBottom: 6 }}>English narration</div>
                                <Input.TextArea
                                    value={step.script}
                                    onChange={(event) => updateStep(index, { script: event.target.value })}
                                    placeholder="Optional narration for this camera stop."
                                    autoSize={{ minRows: 3, maxRows: 7 }}
                                />
                                <SubtitleLinePreview script={step.script} />
                                <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                                <Input.TextArea
                                    value={step.translation}
                                    onChange={(event) => updateStep(index, { translation: event.target.value })}
                                    placeholder="Use Translate, or type one line per English subtitle line"
                                    autoSize={{ minRows: 2, maxRows: 7 }}
                                />
                            </Col>
                        </Row>
                    </Card>
                );
            })}
            <CameraSequencePreview
                open={previewOpen}
                sequence={value}
                resolution={resolution}
                onClose={() => setPreviewOpen(false)}
            />
        </Card>
    );
};

const InteractiveTimelineEditor: React.FC<InteractiveEditorProps> = ({
    taskId,
    resolution,
    chineseSubtitleEnabled,
    karaoke,
    voiceProvider,
    voiceName,
    voiceRate,
    language,
    onClose,
}) => {
    const [blocks, setBlocks] = useState<InteractiveBlock[]>([]);
    const [fontOptions, setFontOptions] = useState<FontOption[]>([]);
    const [subtitleFont, setSubtitleFont] = useState(DEFAULT_FONT);
    const [subtitleFontSize, setSubtitleFontSize] = useState(32);
    const [subtitleColor, setSubtitleColor] = useState('#F472B6');
    const [framing, setFraming] = useState<'fit' | 'fill'>('fit');
    const [highlightShape, setHighlightShape] = useState<HighlightShape>('rect');
    const [highlightColor, setHighlightColor] = useState(DEFAULT_HIGHLIGHT_COLOR);
    const [draftRect, setDraftRect] = useState<DraftRect | null>(null);
    const [translatingAll, setTranslatingAll] = useState(false);
    const [translatingBlockId, setTranslatingBlockId] = useState<string | null>(null);
    const [narrationRate, setNarrationRate] = useState(voiceRate ?? 0.9);
    const [grammarIssues, setGrammarIssues] = useState<GrammarIssue[]>([]);
    const [grammarLabels, setGrammarLabels] = useState<string[]>([]);
    const { setVideoUrl, assembling, setAssembling } = useVideoStore();

    useEffect(() => {
        getSubtitleFonts().then((res) => {
            if (res?.data?.fonts?.length) {
                setFontOptions(res.data.fonts);
                setSubtitleFont(res.data.default || DEFAULT_FONT);
            }
        }).catch(() => {
            setFontOptions([{ id: DEFAULT_FONT, label: 'Ubuntu Condensed', default: true }]);
        });
    }, []);

    const addBlock = (kind: InteractiveBlock['kind'], preset: 'journey' | 'zoom' = 'journey') => {
        const id = makeBlockId();
        if (kind === 'intro' || kind === 'outro') {
            setBlocks((previous) => [...previous, { id, kind, [kind]: makeBookend() } as InteractiveBlock]);
            return;
        }
        if (kind === 'round') {
            setBlocks((previous) => [...previous, { id, kind: 'round', round: makeRound() }]);
            return;
        }
        setBlocks((previous) => [...previous, { id, kind: 'camera', camera: makeCameraSequence(preset) }]);
    };

    const updateRound = (blockId: string, patch: Partial<InteractiveRound>) => {
        setBlocks((previous) => previous.map((block) => (
            block.id === blockId && block.kind === 'round'
                ? { ...block, round: { ...block.round, ...patch } }
                : block
        )));
    };

    const updateCamera = (blockId: string, camera: InteractiveCameraSequence) => {
        setBlocks((previous) => previous.map((block) => (
            block.id === blockId && block.kind === 'camera' ? { ...block, camera } : block
        )));
    };

    const updateBookend = (blockId: string, patch: Partial<InteractiveBookend>) => {
        setBlocks((previous) => previous.map((block) => {
            if (block.id !== blockId) return block;
            if (block.kind === 'intro') return { ...block, intro: { ...block.intro, ...patch } };
            if (block.kind === 'outro') return { ...block, outro: { ...block.outro, ...patch } };
            return block;
        }));
    };

    const removeBlock = (blockId: string) => {
        setBlocks((previous) => previous.filter((block) => block.id !== blockId));
    };

    const moveBlock = (blockId: string, direction: -1 | 1) => {
        setBlocks((previous) => {
            const index = previous.findIndex((block) => block.id === blockId);
            const target = index + direction;
            if (index < 0 || target < 0 || target >= previous.length) return previous;
            const next = [...previous];
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });
    };

    const blockLabel = (block: InteractiveBlock, index: number) => {
        const count = blocks.slice(0, index + 1).filter((item) => item.kind === block.kind).length;
        if (block.kind === 'round') return `Round ${count}`;
        if (block.kind === 'intro') return `Intro ${count}`;
        if (block.kind === 'outro') return `Outro ${count}`;
        return `${block.camera.preset === 'zoom' ? 'Zoom' : 'Journey'} ${count}`;
    };

    interface TextEntry {
        blockId: string;
        field: 'intro' | 'round-question' | 'round-answer' | 'camera-step' | 'outro';
        stepIndex?: number;
        label: string;
        text: string;
    }

    const getTextEntries = (): TextEntry[] => {
        const entries: TextEntry[] = [];
        let introCount = 0;
        let roundCount = 0;
        let cameraCount = 0;
        let outroCount = 0;
        blocks.forEach((block) => {
            if (block.kind === 'intro') {
                introCount += 1;
                entries.push({ blockId: block.id, field: 'intro', label: `Intro ${introCount}`, text: block.intro.script });
                return;
            }
            if (block.kind === 'outro') {
                outroCount += 1;
                entries.push({ blockId: block.id, field: 'outro', label: `Outro ${outroCount}`, text: block.outro.script });
                return;
            }
            if (block.kind === 'round') {
                roundCount += 1;
                entries.push({
                    blockId: block.id,
                    field: 'round-question',
                    label: `Round ${roundCount} question`,
                    text: block.round.question_script,
                });
                entries.push({
                    blockId: block.id,
                    field: 'round-answer',
                    label: `Round ${roundCount} answer`,
                    text: block.round.answer_script,
                });
                return;
            }
            cameraCount += 1;
            const cameraName = block.camera.preset === 'zoom' ? 'Zoom' : 'Journey';
            block.camera.steps.forEach((step, stepIndex) => {
                entries.push({
                    blockId: block.id,
                    field: 'camera-step',
                    stepIndex,
                    label: `${cameraName} ${cameraCount} step ${stepIndex + 1}`,
                    text: step.script,
                });
            });
        });
        return entries;
    };

    const collectTranscripts = () => {
        const entries = getTextEntries();
        return { texts: entries.map((entry) => entry.text), labels: entries.map((entry) => entry.label) };
    };

    const runGrammarCheck = async (texts: string[], labels: string[]): Promise<boolean> => {
        message.loading({ content: 'Checking grammar and spelling...', key: 'grammar-check' });
        try {
            const res = await checkTranscripts(texts);
            if (!res.success || !res.data) {
                throw new Error(res.message || 'Grammar check failed');
            }
            if (!res.data.checked) {
                message.warning({
                    content: 'Grammar check is unavailable right now, so it was skipped.',
                    key: 'grammar-check',
                });
                return true;
            }
            if (res.data.issues.length > 0) {
                setGrammarIssues(res.data.issues);
                setGrammarLabels(labels);
                message.warning({
                    content: `Found ${res.data.issues.length} grammar/spelling issue(s). Fix them before generating.`,
                    key: 'grammar-check',
                });
                return false;
            }
            message.success({ content: 'Transcript check passed.', key: 'grammar-check' });
            return true;
        } catch (error: unknown) {
            message.warning({
                content: `Grammar check skipped: ${getRequestError(error)}`,
                key: 'grammar-check',
            });
            return true;
        }
    };

    const translateBlock = async (blockId: string) => {
        const block = blocks.find((item) => item.id === blockId);
        if (!block) return;
        let scripts: string[] = [];
        if (block.kind === 'round') scripts = [block.round.question_script, block.round.answer_script];
        else if (block.kind === 'intro') scripts = [block.intro.script];
        else if (block.kind === 'outro') scripts = [block.outro.script];
        else scripts = block.camera.steps.map((step) => step.script);
        if (!scripts.some((script) => script.trim())) {
            message.warning('Write the English narration first.');
            return;
        }
        setTranslatingBlockId(blockId);
        try {
            const res = await translateScenes(scripts);
            if (!res.success || !res.data?.translations) {
                throw new Error(res.message || 'Translation failed');
            }
            const translations = res.data.translations;
            if (block.kind === 'round') {
                updateRound(blockId, {
                    question_translation: translations[0] || '',
                    answer_translation: translations[1] || '',
                });
            } else if (block.kind === 'intro') {
                updateBookend(blockId, { translation: translations[0] || '' });
            } else if (block.kind === 'outro') {
                updateBookend(blockId, { translation: translations[0] || '' });
            } else {
                updateCamera(blockId, {
                    ...block.camera,
                    steps: block.camera.steps.map((step, index) => ({
                        ...step,
                        translation: translations[index] || '',
                    })),
                });
            }
            message.success('Translation ready');
        } catch (error: unknown) {
            message.error('Translation failed: ' + getRequestError(error));
        } finally {
            setTranslatingBlockId(null);
        }
    };

    const translateAll = async () => {
        const entries = getTextEntries();
        const scripts = entries.map((entry) => entry.text);
        if (!scripts.some((script) => script.trim())) {
            message.warning('Write some English narration first.');
            return;
        }
        setTranslatingAll(true);
        try {
            const res = await translateScenes(scripts);
            if (!res.success || !res.data?.translations) {
                throw new Error(res.message || 'Translation failed');
            }
            const translations = res.data.translations;
            const payloads = new Map<string, { intro?: string; outro?: string; question?: string; answer?: string; steps?: string[] }>();
            entries.forEach((entry, index) => {
                const value = translations[index] || '';
                const payload = payloads.get(entry.blockId) || {};
                if (entry.field === 'intro') payload.intro = value;
                if (entry.field === 'outro') payload.outro = value;
                if (entry.field === 'round-question') payload.question = value;
                if (entry.field === 'round-answer') payload.answer = value;
                if (entry.field === 'camera-step' && entry.stepIndex !== undefined) {
                    payload.steps = payload.steps || [];
                    payload.steps[entry.stepIndex] = value;
                }
                payloads.set(entry.blockId, payload);
            });
            setBlocks((previous) => previous.map((block) => {
                const payload = payloads.get(block.id);
                if (!payload) return block;
                if (block.kind === 'intro') return { ...block, intro: { ...block.intro, translation: payload.intro || '' } };
                if (block.kind === 'outro') return { ...block, outro: { ...block.outro, translation: payload.outro || '' } };
                if (block.kind === 'round') {
                    return {
                        ...block,
                        round: {
                            ...block.round,
                            question_translation: payload.question || '',
                            answer_translation: payload.answer || '',
                        },
                    };
                }
                return {
                    ...block,
                    camera: {
                        ...block.camera,
                        steps: block.camera.steps.map((step, index) => ({
                            ...step,
                            translation: payload.steps?.[index] || '',
                        })),
                    },
                };
            }));
            message.success('All English narration translated line by line');
        } catch (error: unknown) {
            message.error('Translation failed: ' + getRequestError(error));
        } finally {
            setTranslatingAll(false);
        }
    };

    const startHighlight = (event: React.PointerEvent<HTMLDivElement>, blockId: string, target: 'answer' | 'spotlight') => {
        if (event.button !== 0) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        const x = clamp01((event.clientX - rect.left) / rect.width);
        const y = clamp01((event.clientY - rect.top) / rect.height);
        setDraftRect({ blockId, target, startX: x, startY: y, currentX: x, currentY: y });
    };

    const updateHighlight = (event: React.PointerEvent<HTMLDivElement>) => {
        if (!draftRect) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        setDraftRect((previous) => previous ? ({
            ...previous,
            currentX: clamp01((event.clientX - rect.left) / rect.width),
            currentY: clamp01((event.clientY - rect.top) / rect.height),
        }) : previous);
    };

    const finishHighlight = (event: React.PointerEvent<HTMLDivElement>, blockId: string, target: 'answer' | 'spotlight') => {
        if (!draftRect || draftRect.blockId !== blockId || draftRect.target !== target) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const draft = normalizedDraft(draftRect);
        if (!draft) {
            setDraftRect(null);
            return;
        }
        if (rect.width > 0 && rect.height > 0) {
            const finalX = clamp01((event.clientX - rect.left) / rect.width);
            const finalY = clamp01((event.clientY - rect.top) / rect.height);
            draft.x = Math.min(draftRect.startX, finalX);
            draft.y = Math.min(draftRect.startY, finalY);
            draft.width = Math.abs(finalX - draftRect.startX);
            draft.height = Math.abs(finalY - draftRect.startY);
        }
        if (draft.width >= 0.02 && draft.height >= 0.02) {
            setBlocks((previous) => previous.map((block) => {
                if (block.id !== blockId || block.kind !== 'round') return block;
                if (target === 'answer') {
                    return {
                        ...block,
                        round: {
                            ...block.round,
                            highlights: [...block.round.highlights, { ...draft, shape: highlightShape, color: highlightColor }],
                        },
                    };
                }
                const spotlight = block.round.question_spotlight || makeSpotlight();
                return {
                    ...block,
                    round: {
                        ...block.round,
                        question_spotlight: {
                            ...spotlight,
                            regions: [...spotlight.regions, { ...draft, shape: highlightShape, color: highlightColor }],
                        },
                    },
                };
            }));
        }
        setDraftRect(null);
    };

    const removeHighlight = (blockId: string, highlightIndex: number) => {
        setBlocks((previous) => previous.map((block) => (
            block.id === blockId && block.kind === 'round'
                ? { ...block, round: { ...block.round, highlights: block.round.highlights.filter((_, index) => index !== highlightIndex) } }
                : block
        )));
    };

    const removeSpotlight = (blockId: string, spotlightIndex: number) => {
        setBlocks((previous) => previous.map((block) => {
            if (block.id !== blockId || block.kind !== 'round') return block;
            const spotlight = block.round.question_spotlight || makeSpotlight();
            return {
                ...block,
                round: {
                    ...block.round,
                    question_spotlight: {
                        ...spotlight,
                        regions: spotlight.regions.filter((_, index) => index !== spotlightIndex),
                    },
                },
            };
        }));
    };

    const handleApplyGrammarFixes = (choices: Record<string, string>) => {
        const grouped = groupGrammarIssues(grammarIssues);
        setBlocks((previous) => {
            let cursor = 0;
            const fixedAt = () => {
                const entry = getTextEntries()[cursor];
                const fixed = entry
                    ? applyGrammarFixesToText(entry.text, grouped.get(cursor) || [], choices)
                    : '';
                cursor += 1;
                return fixed;
            };
            return previous.map((block) => {
                if (block.kind === 'intro') return { ...block, intro: { ...block.intro, script: fixedAt() } };
                if (block.kind === 'outro') return { ...block, outro: { ...block.outro, script: fixedAt() } };
                if (block.kind === 'round') {
                    const question = fixedAt();
                    const answer = fixedAt();
                    return { ...block, round: { ...block.round, question_script: question, answer_script: answer } };
                }
                return {
                    ...block,
                    camera: {
                        ...block.camera,
                        steps: block.camera.steps.map((step) => ({ ...step, script: fixedAt() })),
                    },
                };
            });
        });
        const fixedCount = grammarIssues.filter((issue) => (
            issue.replacements.length === 1 || Boolean(choices[grammarIssueKey(issue)])
        )).length;
        setGrammarIssues([]);
        message.success(`Applied ${fixedCount} suggestion(s). Review the text, then generate again.`);
    };

    const handleGenerate = async () => {
        if (blocks.length === 0) {
            message.warning('Add at least one block before generating the video.');
            return;
        }
        for (let index = 0; index < blocks.length; index += 1) {
            const block = blocks[index];
            const label = blockLabel(block, index);
            if (block.kind === 'intro' || block.kind === 'outro') {
                const value = block.kind === 'intro' ? block.intro : block.outro;
                if (!value.image_url.trim() || !value.script.trim()) {
                    message.warning(`${label} needs both an image and English narration.`);
                    return;
                }
                continue;
            }
            if (block.kind === 'round') {
                const round = block.round;
                if (!round.question_image_url.trim() || !round.question_script.trim() || !round.answer_script.trim()) {
                    message.warning(`${label} needs a question image, question narration and answer narration.`);
                    return;
                }
                if (round.question_spotlight?.enabled && round.question_spotlight.regions.length === 0) {
                    message.warning(`${label} has question spotlight enabled but no visible region.`);
                    return;
                }
                continue;
            }
            if (!block.camera.base_image_url.trim()) {
                message.warning(`${label} needs a base image.`);
                return;
            }
            if (block.camera.steps.length < 2) {
                message.warning(`${label} needs at least two camera steps.`);
                return;
            }
            const missingStepImage = block.camera.steps.findIndex((step) => (
                step.image_url !== null && step.image_url !== undefined && !step.image_url.trim()
            ));
            if (missingStepImage >= 0) {
                message.warning(`${label} step ${missingStepImage + 1} needs its detail image.`);
                return;
            }
        }
        const { texts, labels } = collectTranscripts();
        const passed = await runGrammarCheck(texts, labels);
        if (!passed) return;
        setAssembling(true);
        message.loading({ content: 'Rendering interactive video...', key: 'interactive-assemble' });
        try {
            const res = await assembleInteractive({
                task_id: taskId,
                blocks,
                resolution,
                chinese_subtitle_enabled: chineseSubtitleEnabled,
                karaoke,
                subtitle_font: subtitleFont,
                subtitle_font_size: subtitleFontSize,
                subtitle_color: subtitleColor,
                karaoke_highlight_color: '#FFFF00',
                framing,
                voice_provider: voiceProvider || 'gtts',
                voice_name: voiceName || 'default',
                voice_rate: narrationRate,
                language: language || 'fixed-en-GB',
            });
            if (res.success && res.data?.video_url) {
                setVideoUrl(res.data.video_url + '?t=' + Date.now());
                message.success({ content: 'Interactive video is ready', key: 'interactive-assemble' });
            } else {
                throw new Error(res.message || 'Unknown assembly error');
            }
        } catch (error: unknown) {
            message.error({ content: 'Assembly failed: ' + getRequestError(error), key: 'interactive-assemble' });
        } finally {
            setAssembling(false);
        }
    };

    const renderAnswerHighlights = (round: InteractiveRound, blockId: string) => {
        const imageUrl = round.answer_image_url || round.question_image_url;
        if (!imageUrl) {
            return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload the question image first" />;
        }
        const draft = draftRect?.blockId === blockId && draftRect.target === 'answer' ? normalizedDraft(draftRect) : null;
        return (
            <div style={{ background: '#0F172A', borderRadius: 10, padding: 8, display: 'flex', justifyContent: 'center' }}>
                <div
                    style={{
                        position: 'relative',
                        display: 'inline-block',
                        maxWidth: '100%',
                        cursor: 'crosshair',
                        touchAction: 'none',
                        userSelect: 'none',
                    }}
                    onPointerDown={(event) => startHighlight(event, blockId, 'answer')}
                    onPointerMove={updateHighlight}
                    onPointerUp={(event) => finishHighlight(event, blockId, 'answer')}
                    onPointerCancel={() => setDraftRect(null)}
                >
                    <img
                        src={imageUrl}
                        alt="Answer preview"
                        draggable={false}
                        style={{ display: 'block', maxWidth: '100%', maxHeight: 430, width: 'auto', height: 'auto' }}
                    />
                    {round.highlights.map((highlight, highlightIndex) => (
                        <div
                            key={`${highlightIndex}-${highlight.x}-${highlight.y}`}
                            style={{
                                position: 'absolute',
                                left: `${highlight.x * 100}%`,
                                top: `${highlight.y * 100}%`,
                                width: `${highlight.width * 100}%`,
                                height: `${highlight.height * 100}%`,
                                border: `3px solid ${highlight.color}`,
                                borderRadius: highlight.shape === 'ellipse' ? '50%' : 8,
                                boxShadow: '0 0 0 2px rgba(15, 23, 42, 0.65)',
                                pointerEvents: 'none',
                                boxSizing: 'border-box',
                            }}
                        >
                            <button
                                type="button"
                                onPointerDown={(event) => event.stopPropagation()}
                                onClick={(event) => {
                                    event.stopPropagation();
                                    removeHighlight(blockId, highlightIndex);
                                }}
                                style={{
                                    position: 'absolute',
                                    right: -12,
                                    top: -12,
                                    width: 24,
                                    height: 24,
                                    borderRadius: '50%',
                                    border: 'none',
                                    background: '#DC2626',
                                    color: 'white',
                                    cursor: 'pointer',
                                    lineHeight: '24px',
                                    padding: 0,
                                    pointerEvents: 'auto',
                                }}
                                aria-label="Remove highlight"
                            >
                                ×
                            </button>
                        </div>
                    ))}
                    {draft && (
                        <div
                            style={{
                                position: 'absolute',
                                left: `${draft.x * 100}%`,
                                top: `${draft.y * 100}%`,
                                width: `${draft.width * 100}%`,
                                height: `${draft.height * 100}%`,
                                border: `2px dashed ${highlightColor}`,
                                borderRadius: highlightShape === 'ellipse' ? '50%' : 8,
                                background: `${highlightColor}22`,
                                pointerEvents: 'none',
                                boxSizing: 'border-box',
                            }}
                        />
                    )}
                </div>
            </div>
        );
    };

    const renderSpotlightEditor = (round: InteractiveRound, blockId: string) => {
        const spotlight = round.question_spotlight || makeSpotlight();
        const draft = draftRect?.blockId === blockId && draftRect.target === 'spotlight' ? normalizedDraft(draftRect) : null;
        return (
            <>
                <SpotlightPreview
                    imageUrl={round.question_image_url}
                    regions={spotlight.regions}
                    draft={draft}
                    shape={highlightShape}
                    color={highlightColor}
                    maskStyle={spotlight.mask_style}
                    opacity={spotlight.mask_opacity}
                    maskId={`spotlight-${blockId}`}
                    onPointerDown={(event) => startHighlight(event, blockId, 'spotlight')}
                    onPointerMove={updateHighlight}
                    onPointerUp={(event) => finishHighlight(event, blockId, 'spotlight')}
                    onPointerCancel={() => setDraftRect(null)}
                    onRemove={(index) => removeSpotlight(blockId, index)}
                />
                <Space wrap style={{ marginTop: 10 }}>
                    <Select
                        value={highlightShape}
                        onChange={setHighlightShape}
                        style={{ width: 140 }}
                        options={[
                            { value: 'rect', label: 'Rectangle' },
                            { value: 'ellipse', label: 'Ellipse / circle' },
                        ]}
                    />
                    <ColorPicker
                        value={highlightColor}
                        onChange={(color) => setHighlightColor(color.toHexString())}
                        presets={[{ label: 'Spotlight', colors: HIGHLIGHT_COLORS }]}
                    />
                    <Button
                        disabled={round.highlights.length === 0}
                        onClick={() => updateRound(blockId, {
                            question_spotlight: {
                                ...spotlight,
                                regions: round.highlights.map((region) => ({ ...region })),
                            },
                        })}
                    >
                        Copy answer highlights
                    </Button>
                    <Button
                        disabled={spotlight.regions.length === 0}
                        onClick={() => updateRound(blockId, { question_spotlight: { ...spotlight, regions: [] } })}
                    >
                        Clear regions
                    </Button>
                </Space>
                <Text type="secondary" style={{ display: 'block', marginTop: 6 }}>
                    Drag on the question image. Only these regions stay visible; the answer reveals the whole picture.
                </Text>
                <Row gutter={12} style={{ marginTop: 10 }}>
                    <Col span={8}>
                        <div style={{ marginBottom: 4 }}>Mask style</div>
                        <Select
                            value={spotlight.mask_style}
                            onChange={(mask_style) => updateRound(blockId, { question_spotlight: { ...spotlight, mask_style } })}
                            style={{ width: '100%' }}
                            options={[
                                { value: 'dim', label: 'Dim outside' },
                                { value: 'blur', label: 'Blur outside' },
                                { value: 'solid', label: 'Hide outside' },
                            ]}
                        />
                    </Col>
                    <Col span={8}>
                        <div style={{ marginBottom: 4 }}>Mask opacity: {Math.round(spotlight.mask_opacity * 100)}%</div>
                        <Slider
                            min={0}
                            max={1}
                            step={0.01}
                            value={spotlight.mask_opacity}
                            onChange={(mask_opacity) => updateRound(blockId, { question_spotlight: { ...spotlight, mask_opacity } })}
                        />
                    </Col>
                    <Col span={8}>
                        <div style={{ marginBottom: 4 }}>Edge feather</div>
                        <InputNumber
                            min={0}
                            max={80}
                            value={spotlight.feather}
                            onChange={(feather) => updateRound(blockId, { question_spotlight: { ...spotlight, feather: Number(feather) || 0 } })}
                            style={{ width: '100%' }}
                        />
                    </Col>
                </Row>
                <Row gutter={12} style={{ marginTop: 8 }} align="bottom">
                    <Col span={8}>
                        <div style={{ marginBottom: 4 }}>Answer reveal</div>
                        <Select
                            value={spotlight.answer_reveal}
                            onChange={(answer_reveal) => updateRound(blockId, { question_spotlight: { ...spotlight, answer_reveal } })}
                            style={{ width: '100%' }}
                            options={[
                                { value: 'expand', label: 'Expand to full picture' },
                                { value: 'cut', label: 'Cut to full picture' },
                            ]}
                        />
                    </Col>
                    <Col span={8}>
                        <div style={{ marginBottom: 4 }}>Reveal time</div>
                        <InputNumber
                            min={0.1}
                            max={2}
                            step={0.1}
                            value={spotlight.reveal_seconds}
                            onChange={(reveal_seconds) => updateRound(blockId, { question_spotlight: { ...spotlight, reveal_seconds: Number(reveal_seconds) || 0.5 } })}
                            addonAfter="s"
                            style={{ width: '100%' }}
                            disabled={spotlight.answer_reveal !== 'expand'}
                        />
                    </Col>
                    <Col span={8}>
                        <Space>
                            <Switch
                                checked={spotlight.outline}
                                onChange={(outline) => updateRound(blockId, { question_spotlight: { ...spotlight, outline } })}
                            />
                            <Text>Outline visible area</Text>
                        </Space>
                    </Col>
                </Row>
            </>
        );
    };

    const renderRoundBlock = (block: Extract<InteractiveBlock, { kind: 'round' }>, index: number) => {
        const round = block.round;
        const spotlight = round.question_spotlight || makeSpotlight();
        return (
            <Card
                key={block.id}
                title={blockLabel(block, index)}
                style={{ marginBottom: 18 }}
                extra={(
                    <BlockActions
                        canMoveUp={index > 0}
                        canMoveDown={index < blocks.length - 1}
                        onMoveUp={() => moveBlock(block.id, -1)}
                        onMoveDown={() => moveBlock(block.id, 1)}
                        onRemove={() => removeBlock(block.id)}
                    >
                        <Button
                            icon={<TranslationOutlined />}
                            size="small"
                            loading={translatingBlockId === block.id}
                            onClick={() => translateBlock(block.id)}
                        >
                            Translate
                        </Button>
                    </BlockActions>
                )}
            >
                <Row gutter={20}>
                    <Col xs={24} lg={12}>
                        <Card size="small" title="1. Question" style={{ height: '100%' }}>
                            {round.question_image_url ? (
                                <div style={{ background: '#0F172A', borderRadius: 10, padding: 8, textAlign: 'center' }}>
                                    <img
                                        src={round.question_image_url}
                                        alt="Question"
                                        draggable={false}
                                        style={{ display: 'inline-block', maxWidth: '100%', maxHeight: 360, width: 'auto', height: 'auto' }}
                                    />
                                </div>
                            ) : (
                                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload a question image" />
                            )}
                            <div style={{ marginTop: 10 }}>
                                <ImageSourceControls
                                    taskId={taskId}
                                    onChange={(url) => updateRound(block.id, { question_image_url: url })}
                                    showClear={!!round.question_image_url}
                                    clearLabel="Clear image"
                                    onClear={() => updateRound(block.id, { question_image_url: '' })}
                                />
                            </div>
                            <Divider orientation="left" style={{ margin: '14px 0 10px' }}>Question visibility</Divider>
                            <Space>
                                <Switch
                                    checked={spotlight.enabled}
                                    onChange={(enabled) => updateRound(block.id, {
                                        question_spotlight: { ...spotlight, enabled },
                                    })}
                                />
                                <Text>Peek / spotlight question</Text>
                            </Space>
                            {spotlight.enabled && renderSpotlightEditor(round, block.id)}
                            <div style={{ marginTop: 14, marginBottom: 6 }}>English question narration</div>
                            <Input.TextArea
                                value={round.question_script}
                                onChange={(event) => updateRound(block.id, { question_script: event.target.value })}
                                placeholder="What can you see in this picture?"
                                autoSize={{ minRows: 3, maxRows: 7 }}
                            />
                            <SubtitleLinePreview script={round.question_script} />
                            <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                            <Input.TextArea
                                value={round.question_translation}
                                onChange={(event) => updateRound(block.id, { question_translation: event.target.value })}
                                placeholder="Use Translate All, or type one line per English subtitle line"
                                autoSize={{ minRows: 2, maxRows: 7 }}
                            />
                        </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                        <Card
                            size="small"
                            title="2. Answer + highlight"
                            extra={round.answer_image_url ? <Tag color="blue">Own image</Tag> : <Tag>Uses question image</Tag>}
                        >
                            {renderAnswerHighlights(round, block.id)}
                            <Space wrap style={{ marginTop: 10 }}>
                                <Select
                                    value={highlightShape}
                                    onChange={setHighlightShape}
                                    style={{ width: 150 }}
                                    options={[
                                        { value: 'rect', label: 'Rectangle' },
                                        { value: 'ellipse', label: 'Ellipse / circle' },
                                    ]}
                                />
                                <ColorPicker
                                    value={highlightColor}
                                    onChange={(color) => setHighlightColor(color.toHexString())}
                                    presets={[{ label: 'Highlights', colors: HIGHLIGHT_COLORS }]}
                                />
                                <Button
                                    disabled={round.highlights.length === 0}
                                    onClick={() => updateRound(block.id, { highlights: [] })}
                                >
                                    Clear highlights
                                </Button>
                            </Space>
                            <div style={{ marginTop: 6 }}>
                                <Text type="secondary">
                                    Choose a shape, then drag over the answer image. {round.highlights.length} highlight(s) added.
                                </Text>
                            </div>
                            <div style={{ marginTop: 10 }}>
                                <ImageSourceControls
                                    taskId={taskId}
                                    onChange={(url) => updateRound(block.id, { answer_image_url: url, highlights: [] })}
                                    showClear={!!round.answer_image_url}
                                    clearLabel="Use question image"
                                    onClear={() => updateRound(block.id, { answer_image_url: null, highlights: [] })}
                                />
                            </div>
                            <div style={{ marginTop: 14, marginBottom: 6 }}>English answer narration</div>
                            <Input.TextArea
                                value={round.answer_script}
                                onChange={(event) => updateRound(block.id, { answer_script: event.target.value })}
                                placeholder="I can see a red apple. The apple is round."
                                autoSize={{ minRows: 3, maxRows: 7 }}
                            />
                            <SubtitleLinePreview script={round.answer_script} />
                            <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                            <Input.TextArea
                                value={round.answer_translation}
                                onChange={(event) => updateRound(block.id, { answer_translation: event.target.value })}
                                placeholder="Use Translate All, or type one line per English subtitle line"
                                autoSize={{ minRows: 2, maxRows: 7 }}
                            />
                            <Divider orientation="left" style={{ margin: '16px 0 10px' }}>Answer subtitle</Divider>
                            <Row gutter={16}>
                                <Col xs={24} md={12}>
                                    <div style={{ marginBottom: 6 }}>Font size</div>
                                    <Space.Compact style={{ width: '100%' }}>
                                        <InputNumber
                                            min={16}
                                            max={160}
                                            value={round.answer_subtitle_font_size ?? subtitleFontSize}
                                            onChange={(value) => updateRound(block.id, {
                                                answer_subtitle_font_size: typeof value === 'number' ? value : null,
                                            })}
                                            style={{ width: '100%' }}
                                        />
                                        <Button
                                            disabled={!round.answer_subtitle_font_size}
                                            onClick={() => updateRound(block.id, { answer_subtitle_font_size: null })}
                                        >
                                            Global
                                        </Button>
                                    </Space.Compact>
                                </Col>
                                <Col xs={24} md={12}>
                                    <div style={{ marginBottom: 6 }}>Position</div>
                                    <Select
                                        value={round.answer_subtitle_position || 'bottom'}
                                        onChange={(value) => updateRound(block.id, { answer_subtitle_position: value })}
                                        style={{ width: '100%' }}
                                        options={ANSWER_SUBTITLE_POSITION_OPTIONS}
                                    />
                                </Col>
                            </Row>
                        </Card>
                    </Col>
                </Row>
                <Divider orientation="left">3. Countdown</Divider>
                <Row gutter={16}>
                    <Col xs={24} md={6}>
                        <div style={{ marginBottom: 6 }}>Duration</div>
                        <InputNumber
                            min={1}
                            max={15}
                            value={round.countdown_seconds}
                            onChange={(value) => updateRound(block.id, { countdown_seconds: Number(value) || 5 })}
                            addonAfter="seconds"
                            style={{ width: '100%' }}
                        />
                    </Col>
                    <Col xs={24} md={8}>
                        <div style={{ marginBottom: 6 }}>Screen position</div>
                        <Select
                            value={round.countdown_position}
                            onChange={(value) => updateRound(block.id, { countdown_position: value })}
                            style={{ width: '100%' }}
                            options={COUNTDOWN_POSITION_OPTIONS}
                        />
                    </Col>
                    <Col xs={24} md={10}>
                        <div style={{ marginBottom: 6 }}>Sound effect</div>
                        <Select
                            value={round.countdown_sound}
                            onChange={(value) => updateRound(block.id, { countdown_sound: value })}
                            style={{ width: '100%' }}
                            options={COUNTDOWN_SOUND_OPTIONS}
                        />
                    </Col>
                </Row>
                <Row gutter={16} style={{ marginTop: 12 }}>
                    <Col xs={24} md={12}>
                        <div style={{ marginBottom: 6 }}>Optional label above the number</div>
                        <Input
                            value={round.countdown_label}
                            onChange={(event) => updateRound(block.id, { countdown_label: event.target.value })}
                            placeholder="e.g. Think!"
                            maxLength={32}
                        />
                    </Col>
                    <Col xs={24} md={12}>
                        <Text type="secondary">
                            The countdown reuses the question image. If spotlight is on, it stays masked until the answer.
                        </Text>
                    </Col>
                </Row>
            </Card>
        );
    };

    const renderBlock = (block: InteractiveBlock, index: number) => {
        if (block.kind === 'intro' || block.kind === 'outro') {
            const value = block.kind === 'intro' ? block.intro : block.outro;
            return (
                <BookendSceneCard
                    key={block.id}
                    title={blockLabel(block, index)}
                    taskId={taskId}
                    value={value}
                    onChange={(next) => updateBookend(block.id, next)}
                    onRemove={() => removeBlock(block.id)}
                    onMoveUp={() => moveBlock(block.id, -1)}
                    onMoveDown={() => moveBlock(block.id, 1)}
                    canMoveUp={index > 0}
                    canMoveDown={index < blocks.length - 1}
                />
            );
        }
        if (block.kind === 'camera') {
            return (
                <CameraSequenceCard
                    key={block.id}
                    title={blockLabel(block, index)}
                    taskId={taskId}
                    resolution={resolution}
                    value={block.camera}
                    onChange={(camera) => updateCamera(block.id, camera)}
                    onRemove={() => removeBlock(block.id)}
                    onMoveUp={() => moveBlock(block.id, -1)}
                    onMoveDown={() => moveBlock(block.id, 1)}
                    canMoveUp={index > 0}
                    canMoveDown={index < blocks.length - 1}
                    onTranslate={() => translateBlock(block.id)}
                    translating={translatingBlockId === block.id}
                />
            );
        }
        return renderRoundBlock(block, index);
    };

    return (
        <div style={{ maxWidth: 1280, margin: '0 auto' }}>
            <Card
                title="Interactive Video Builder"
                extra={<Button onClick={onClose}>Back to setup</Button>}
                style={{ marginBottom: 16 }}
            >
                <Text type="secondary">
                    Build a timeline of quiz rounds, journeys, zoom sequences, intros and outros. Each block can be
                    reordered independently; a spotlight question reveals the full picture on the answer.
                </Text>
                <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col xs={24} md={7}>
                        <div style={{ marginBottom: 6 }}>Subtitle font</div>
                        <Select
                            value={subtitleFont}
                            onChange={setSubtitleFont}
                            style={{ width: '100%' }}
                            options={fontOptions.map((font) => ({ value: font.id, label: font.label }))}
                        />
                    </Col>
                    <Col xs={24} md={7}>
                        <div style={{ marginBottom: 6 }}>Subtitle size: {subtitleFontSize}px</div>
                        <Slider min={32} max={88} value={subtitleFontSize} onChange={setSubtitleFontSize} />
                    </Col>
                    <Col xs={12} md={5}>
                        <div style={{ marginBottom: 6 }}>Subtitle color</div>
                        <ColorPicker
                            value={subtitleColor}
                            onChange={(color) => setSubtitleColor(color.toHexString())}
                            showText
                        />
                    </Col>
                    <Col xs={12} md={5}>
                        <div style={{ marginBottom: 6 }}>Image framing</div>
                        <Select
                            value={framing}
                            onChange={setFraming}
                            style={{ width: '100%' }}
                            options={[
                                { value: 'fit', label: 'Fit — show the whole picture' },
                                { value: 'fill', label: 'Fill — cover the frame' },
                            ]}
                        />
                    </Col>
                </Row>
                <Row gutter={16} style={{ marginTop: 12 }} align="bottom">
                    <Col xs={24} md={8}>
                        <div style={{ marginBottom: 6 }}>Narration speed</div>
                        <Select
                            value={narrationRate}
                            onChange={setNarrationRate}
                            style={{ width: '100%' }}
                            options={VOICE_RATE_OPTIONS}
                            disabled={(voiceProvider || 'gtts') === 'gtts'}
                        />
                    </Col>
                    <Col xs={24} md={16}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                            {(voiceProvider || 'gtts') === 'gtts'
                                ? 'gTTS uses a fixed pace. Choose Edge TTS or Google TTS in the setup form to control narration speed.'
                                : 'Applies to every question, answer, intro, outro and camera-step narration.'}
                        </Text>
                    </Col>
                </Row>
                <Space wrap style={{ marginTop: 16 }}>
                    <Button icon={<TranslationOutlined />} loading={translatingAll} onClick={translateAll}>
                        Translate All to Chinese
                    </Button>
                    <Button type="primary" loading={assembling} onClick={handleGenerate}>
                        Generate Interactive Video
                    </Button>
                </Space>
                <div style={{ marginTop: 8 }}>
                    <Text type="secondary">
                        Chinese translations are aligned one line per English subtitle line. Use “Translate All” after
                        writing the English narration, then edit any line manually if needed.
                    </Text>
                </div>
            </Card>

            {blocks.length === 0 ? (
                <Card style={{ marginBottom: 18 }}>
                    <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description="Start your interactive timeline with any block type."
                    >
                        <Space wrap>
                            <Button type="primary" icon={<PlusOutlined />} onClick={() => addBlock('round')}>Add Round</Button>
                            <Button icon={<PlusOutlined />} onClick={() => addBlock('camera', 'journey')}>Add Journey</Button>
                            <Button icon={<PlusOutlined />} onClick={() => addBlock('camera', 'zoom')}>Add Zoom</Button>
                            <Button onClick={() => addBlock('intro')}>Add Intro</Button>
                            <Button onClick={() => addBlock('outro')}>Add Outro</Button>
                        </Space>
                    </Empty>
                </Card>
            ) : blocks.map(renderBlock)}

            <Card style={{ marginBottom: 24 }}>
                <Space wrap>
                    <Button icon={<PlusOutlined />} onClick={() => addBlock('round')}>Add Round</Button>
                    <Button icon={<PlusOutlined />} onClick={() => addBlock('camera', 'journey')}>Add Journey</Button>
                    <Button icon={<PlusOutlined />} onClick={() => addBlock('camera', 'zoom')}>Add Zoom</Button>
                    <Button onClick={() => addBlock('intro')}>Add Intro</Button>
                    <Button onClick={() => addBlock('outro')}>Add Outro</Button>
                </Space>
                <div style={{ marginTop: 10 }}>
                    <Space wrap>
                        <Button icon={<TranslationOutlined />} loading={translatingAll} onClick={translateAll}>
                            Translate All to Chinese
                        </Button>
                        <Button type="primary" loading={assembling} onClick={handleGenerate}>
                            Generate Interactive Video
                        </Button>
                    </Space>
                </div>
            </Card>

            <GrammarCheckModal
                open={grammarIssues.length > 0}
                issues={grammarIssues}
                labels={grammarLabels}
                onClose={() => setGrammarIssues([])}
                onApplySuggestions={handleApplyGrammarFixes}
            />
        </div>
    );
};

export default InteractiveTimelineEditor;
