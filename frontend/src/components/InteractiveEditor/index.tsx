import React, { useEffect, useState } from 'react';
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
    Popconfirm,
    Row,
    Select,
    Slider,
    Space,
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
    roundIndex: number;
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
}

const ImageSourceControls: React.FC<ImageSourceControlsProps> = ({
    taskId,
    onChange,
    onClear,
    clearLabel = 'Clear image',
    showClear = false,
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
                </Col>
            </Row>
        </Card>
    );
};

interface BookendListProps {
    label: string;
    addLabel: string;
    hint: string;
    taskId: string;
    items: InteractiveBookend[];
    onChange: (items: InteractiveBookend[]) => void;
}

const BookendList: React.FC<BookendListProps> = ({
    label,
    addLabel,
    hint,
    taskId,
    items,
    onChange,
}) => {
    const add = () => onChange([...items, { image_url: '', script: '', translation: '' }]);
    const update = (index: number, value: InteractiveBookend) => {
        onChange(items.map((item, itemIndex) => (itemIndex === index ? value : item)));
    };
    const remove = (index: number) => {
        onChange(items.filter((_, itemIndex) => itemIndex !== index));
    };
    const move = (index: number, direction: -1 | 1) => {
        const target = index + direction;
        if (target < 0 || target >= items.length) return;
        const next = [...items];
        [next[index], next[target]] = [next[target], next[index]];
        onChange(next);
    };

    if (items.length === 0) {
        return (
            <Card size="small" style={{ marginBottom: 18 }}>
                <Space wrap>
                    <Button type="dashed" onClick={add}>{addLabel}</Button>
                    <Text type="secondary">{hint}</Text>
                </Space>
            </Card>
        );
    }

    return (
        <>
            {items.map((item, index) => (
                <BookendSceneCard
                    key={`${label}-${index}`}
                    title={`${label} ${index + 1}`}
                    taskId={taskId}
                    value={item}
                    onChange={(value) => update(index, value)}
                    onRemove={() => remove(index)}
                    onMoveUp={() => move(index, -1)}
                    onMoveDown={() => move(index, 1)}
                    canMoveUp={index > 0}
                    canMoveDown={index < items.length - 1}
                />
            ))}
            <Card size="small" style={{ marginBottom: 18 }}>
                <Space wrap>
                    <Button type="dashed" icon={<PlusOutlined />} onClick={add}>{addLabel}</Button>
                    <Text type="secondary">{hint}</Text>
                </Space>
            </Card>
        </>
    );
};

const InteractiveEditor: React.FC<InteractiveEditorProps> = ({
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
    const [rounds, setRounds] = useState<InteractiveRound[]>(() => [makeRound()]);
    const [fontOptions, setFontOptions] = useState<FontOption[]>([]);
    const [subtitleFont, setSubtitleFont] = useState(DEFAULT_FONT);
    const [subtitleFontSize, setSubtitleFontSize] = useState(32);
    const [subtitleColor, setSubtitleColor] = useState('#F472B6');
    const [framing, setFraming] = useState<'fit' | 'fill'>('fit');
    const [highlightShape, setHighlightShape] = useState<HighlightShape>('rect');
    const [highlightColor, setHighlightColor] = useState(DEFAULT_HIGHLIGHT_COLOR);
    const [draftRect, setDraftRect] = useState<DraftRect | null>(null);
    const [translatingAll, setTranslatingAll] = useState(false);
    const [translatingRound, setTranslatingRound] = useState<number | null>(null);
    const [intros, setIntros] = useState<InteractiveBookend[]>([]);
    const [outros, setOutros] = useState<InteractiveBookend[]>([]);
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

    const updateRound = (index: number, patch: Partial<InteractiveRound>) => {
        setRounds((previous) => previous.map((round, roundIndex) => (
            roundIndex === index ? { ...round, ...patch } : round
        )));
    };

    const addRound = () => setRounds((previous) => [...previous, makeRound()]);

    const deleteRound = (index: number) => {
        setRounds((previous) => previous.filter((_, roundIndex) => roundIndex !== index));
    };

    const moveRound = (index: number, direction: -1 | 1) => {
        const target = index + direction;
        setRounds((previous) => {
            if (target < 0 || target >= previous.length) return previous;
            const next = [...previous];
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });
    };

    const translateRound = async (index: number) => {
        const round = rounds[index];
        const scripts = [round.question_script, round.answer_script];
        if (!scripts.some((script) => script.trim())) {
            message.warning('Write the English question or answer first.');
            return;
        }
        setTranslatingRound(index);
        try {
            const res = await translateScenes(scripts);
            if (!res.success || !res.data?.translations) {
                throw new Error(res.message || 'Translation failed');
            }
            updateRound(index, {
                question_translation: res.data.translations[0] || '',
                answer_translation: res.data.translations[1] || '',
            });
            message.success(`Round ${index + 1} translation ready`);
        } catch (error: unknown) {
            message.error('Translation failed: ' + getRequestError(error));
        } finally {
            setTranslatingRound(null);
        }
    };

    const translateAll = async () => {
        const scripts: string[] = [];
        intros.forEach((item) => scripts.push(item.script));
        rounds.forEach((round) => scripts.push(round.question_script, round.answer_script));
        outros.forEach((item) => scripts.push(item.script));
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
            let offset = 0;
            if (intros.length > 0) {
                setIntros((previous) => previous.map((item, index) => ({
                    ...item,
                    translation: translations[index] || '',
                })));
                offset = intros.length;
            }
            setRounds((previous) => previous.map((round, index) => ({
                ...round,
                question_translation: translations[offset + index * 2] || '',
                answer_translation: translations[offset + index * 2 + 1] || '',
            })));
            if (outros.length > 0) {
                const outroOffset = offset + rounds.length * 2;
                setOutros((previous) => previous.map((item, index) => ({
                    ...item,
                    translation: translations[outroOffset + index] || '',
                })));
            }
            message.success('All English narration translated line by line');
        } catch (error: unknown) {
            message.error('Translation failed: ' + getRequestError(error));
        } finally {
            setTranslatingAll(false);
        }
    };

    const startHighlight = (event: React.PointerEvent<HTMLDivElement>, roundIndex: number) => {
        if (event.button !== 0) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        const x = clamp01((event.clientX - rect.left) / rect.width);
        const y = clamp01((event.clientY - rect.top) / rect.height);
        setDraftRect({ roundIndex, startX: x, startY: y, currentX: x, currentY: y });
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

    const finishHighlight = (event: React.PointerEvent<HTMLDivElement>, roundIndex: number) => {
        if (!draftRect) return;
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
        if (draft && draft.width >= 0.02 && draft.height >= 0.02) {
            setRounds((previous) => previous.map((round, index) => (
                index === roundIndex
                    ? {
                        ...round,
                        highlights: [
                            ...round.highlights,
                            { ...draft, shape: highlightShape, color: highlightColor },
                        ],
                    }
                    : round
            )));
        }
        setDraftRect(null);
    };

    const removeHighlight = (roundIndex: number, highlightIndex: number) => {
        setRounds((previous) => previous.map((round, index) => (
            index === roundIndex
                ? { ...round, highlights: round.highlights.filter((_, itemIndex) => itemIndex !== highlightIndex) }
                : round
        )));
    };

    const collectTranscripts = () => {
        const texts: string[] = [];
        const labels: string[] = [];
        intros.forEach((item, index) => {
            texts.push(item.script);
            labels.push(`Intro ${index + 1}`);
        });
        rounds.forEach((round, index) => {
            texts.push(round.question_script);
            labels.push(`Round ${index + 1} question`);
            texts.push(round.answer_script);
            labels.push(`Round ${index + 1} answer`);
        });
        outros.forEach((item, index) => {
            texts.push(item.script);
            labels.push(`Outro ${index + 1}`);
        });
        return { texts, labels };
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

    const handleApplyGrammarFixes = (choices: Record<string, string>) => {
        const grouped = groupGrammarIssues(grammarIssues);
        const introOffset = intros.length;
        const roundIssues = rounds.map((_, index) => {
            const offset = introOffset + index * 2;
            return {
                question: grouped.get(offset) || [],
                answer: grouped.get(offset + 1) || [],
            };
        });
        if (intros.length > 0) {
            setIntros((previous) => previous.map((item, index) => ({
                ...item,
                script: applyGrammarFixesToText(item.script, grouped.get(index) || [], choices),
            })));
        }
        setRounds((previous) => previous.map((round, index) => ({
            ...round,
            question_script: applyGrammarFixesToText(round.question_script, roundIssues[index]?.question || [], choices),
            answer_script: applyGrammarFixesToText(round.answer_script, roundIssues[index]?.answer || [], choices),
        })));
        if (outros.length > 0) {
            const outroOffset = introOffset + rounds.length * 2;
            setOutros((previous) => previous.map((item, index) => ({
                ...item,
                script: applyGrammarFixesToText(item.script, grouped.get(outroOffset + index) || [], choices),
            })));
        }
        const fixedCount = grammarIssues.filter((issue) => (
            issue.replacements.length === 1 || Boolean(choices[grammarIssueKey(issue)])
        )).length;
        setGrammarIssues([]);
        message.success(`Applied ${fixedCount} suggestion(s). Review the text, then generate again.`);
    };

    const handleGenerate = async () => {
        const missingRound = rounds.findIndex((round) => (
            !round.question_image_url.trim()
            || !round.question_script.trim()
            || !round.answer_script.trim()
        ));
        if (missingRound >= 0) {
            const round = rounds[missingRound];
            const missing = !round.question_image_url.trim()
                ? 'question image'
                : (!round.question_script.trim() ? 'question narration' : 'answer narration');
            message.warning(`Round ${missingRound + 1} is missing its ${missing}.`);
            return;
        }
        const invalidIntro = intros.findIndex(
            (item) => !item.image_url.trim() || !item.script.trim()
        );
        if (invalidIntro >= 0) {
            message.warning(
                `Intro scene ${invalidIntro + 1} needs both an image and English narration.`
            );
            return;
        }
        const invalidOutro = outros.findIndex(
            (item) => !item.image_url.trim() || !item.script.trim()
        );
        if (invalidOutro >= 0) {
            message.warning(
                `Outro scene ${invalidOutro + 1} needs both an image and English narration.`
            );
            return;
        }
        const { texts, labels } = collectTranscripts();
        const passed = await runGrammarCheck(texts, labels);
        if (!passed) return;
        setAssembling(true);
        message.loading({ content: 'Rendering interactive video...', key: 'interactive-assemble' });
        try {
            const res = await assembleInteractive({
                task_id: taskId,
                rounds,
                intros,
                outros,
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

    const renderAnswerHighlights = (round: InteractiveRound, roundIndex: number) => {
        const imageUrl = round.answer_image_url || round.question_image_url;
        if (!imageUrl) {
            return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Upload the question image first" />;
        }
        const draft = draftRect?.roundIndex === roundIndex ? normalizedDraft(draftRect) : null;
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
                    onPointerDown={(event) => startHighlight(event, roundIndex)}
                    onPointerMove={updateHighlight}
                    onPointerUp={(event) => finishHighlight(event, roundIndex)}
                    onPointerCancel={() => setDraftRect(null)}
                >
                    <img
                        src={imageUrl}
                        alt={`Answer preview for round ${roundIndex + 1}`}
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
                                    removeHighlight(roundIndex, highlightIndex);
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

    return (
        <div style={{ maxWidth: 1280, margin: '0 auto' }}>
            <Card
                title="Interactive Video Builder"
                extra={<Button onClick={onClose}>Back to setup</Button>}
                style={{ marginBottom: 16 }}
            >
                <Text type="secondary">
                    Each round plays Question → Countdown → Answer. Question and answer use narration and subtitles;
                    the countdown reuses the question image. Drag on the answer preview to add a highlight.
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
                                : 'Applies to every question, answer, intro, and outro scene.'}
                        </Text>
                    </Col>
                </Row>
                <Space wrap style={{ marginTop: 16 }}>
                    <Button
                        icon={<TranslationOutlined />}
                        loading={translatingAll}
                        onClick={translateAll}
                    >
                        Translate All to Chinese
                    </Button>
                    <Button type="primary" loading={assembling} onClick={handleGenerate}>
                        Generate Interactive Video
                    </Button>
                </Space>
                <div style={{ marginTop: 8 }}>
                    <Text type="secondary">
                        Chinese translations are aligned one line per English subtitle line. Use “Translate All” after
                        writing the English narration, then edit any line manually if needed. Re-run it after changing line breaks.
                    </Text>
                </div>
            </Card>

            <BookendList
                label="Intro"
                addLabel="Add intro scene"
                hint="Optional: shown before the first round. Add as many as you need."
                taskId={taskId}
                items={intros}
                onChange={setIntros}
            />

            {rounds.map((round, roundIndex) => {
                const answerImage = round.answer_image_url || round.question_image_url;
                return (
                    <Card
                        key={roundIndex}
                        title={`Round ${roundIndex + 1}`}
                        style={{ marginBottom: 18 }}
                        extra={(
                            <Space>
                                <Button
                                    icon={<ArrowUpOutlined />}
                                    size="small"
                                    disabled={roundIndex === 0}
                                    onClick={() => moveRound(roundIndex, -1)}
                                />
                                <Button
                                    icon={<ArrowDownOutlined />}
                                    size="small"
                                    disabled={roundIndex === rounds.length - 1}
                                    onClick={() => moveRound(roundIndex, 1)}
                                />
                                <Button
                                    icon={<TranslationOutlined />}
                                    size="small"
                                    loading={translatingRound === roundIndex}
                                    onClick={() => translateRound(roundIndex)}
                                >
                                    Translate this round
                                </Button>
                                <Popconfirm
                                    title="Delete this round?"
                                    onConfirm={() => deleteRound(roundIndex)}
                                    disabled={rounds.length === 1}
                                >
                                    <Button
                                        danger
                                        icon={<DeleteOutlined />}
                                        size="small"
                                        disabled={rounds.length === 1}
                                    />
                                </Popconfirm>
                            </Space>
                        )}
                    >
                        <Row gutter={20}>
                            <Col xs={24} lg={12}>
                                <Card size="small" title="1. Question" style={{ height: '100%' }}>
                                    {round.question_image_url ? (
                                        <div style={{ background: '#0F172A', borderRadius: 10, padding: 8, textAlign: 'center' }}>
                                            <img
                                                src={round.question_image_url}
                                                alt={`Question for round ${roundIndex + 1}`}
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
                                            onChange={(url) => updateRound(roundIndex, { question_image_url: url })}
                                            showClear={!!round.question_image_url}
                                            clearLabel="Clear image"
                                            onClear={() => updateRound(roundIndex, { question_image_url: '' })}
                                        />
                                    </div>
                                    <div style={{ marginTop: 14, marginBottom: 6 }}>English question narration</div>
                                    <Input.TextArea
                                        value={round.question_script}
                                        onChange={(event) => updateRound(roundIndex, { question_script: event.target.value })}
                                        placeholder="What can you see in this picture?"
                                        autoSize={{ minRows: 3, maxRows: 7 }}
                                    />
                                    <SubtitleLinePreview script={round.question_script} />
                                    <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                                    <Input.TextArea
                                        value={round.question_translation}
                                        onChange={(event) => updateRound(roundIndex, { question_translation: event.target.value })}
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
                                    {renderAnswerHighlights(round, roundIndex)}
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
                                            onClick={() => updateRound(roundIndex, { highlights: [] })}
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
                                            onChange={(url) => updateRound(roundIndex, { answer_image_url: url, highlights: [] })}
                                            showClear={!!round.answer_image_url}
                                            clearLabel="Use question image"
                                            onClear={() => updateRound(roundIndex, { answer_image_url: null, highlights: [] })}
                                        />
                                    </div>
                                    <div style={{ marginTop: 14, marginBottom: 6 }}>English answer narration</div>
                                    <Input.TextArea
                                        value={round.answer_script}
                                        onChange={(event) => updateRound(roundIndex, { answer_script: event.target.value })}
                                        placeholder="I can see a red apple. The apple is round."
                                        autoSize={{ minRows: 3, maxRows: 7 }}
                                    />
                                    <SubtitleLinePreview script={round.answer_script} />
                                    <div style={{ marginTop: 10, marginBottom: 6 }}>Chinese translation</div>
                                    <Input.TextArea
                                        value={round.answer_translation}
                                        onChange={(event) => updateRound(roundIndex, { answer_translation: event.target.value })}
                                        placeholder="Use Translate All, or type one line per English subtitle line"
                                        autoSize={{ minRows: 2, maxRows: 7 }}
                                    />
                                    <Divider orientation="left" style={{ margin: '16px 0 10px' }}>Answer subtitle</Divider>
                                    <Row gutter={16}>
                                        <Col xs={24} md={12}>
                                            <div style={{ marginBottom: 6 }}>
                                                Font size
                                                {round.answer_subtitle_font_size
                                                    ? ' (this answer only)'
                                                    : ` (global ${subtitleFontSize}px)`}
                                            </div>
                                            <Space.Compact style={{ width: '100%' }}>
                                                <InputNumber
                                                    min={16}
                                                    max={160}
                                                    value={round.answer_subtitle_font_size ?? subtitleFontSize}
                                                    onChange={(value) => updateRound(roundIndex, {
                                                        answer_subtitle_font_size: typeof value === 'number' ? value : null,
                                                    })}
                                                    style={{ width: '100%' }}
                                                />
                                                <Button
                                                    disabled={!round.answer_subtitle_font_size}
                                                    onClick={() => updateRound(roundIndex, { answer_subtitle_font_size: null })}
                                                >
                                                    Global
                                                </Button>
                                            </Space.Compact>
                                        </Col>
                                        <Col xs={24} md={12}>
                                            <div style={{ marginBottom: 6 }}>Position</div>
                                            <Select
                                                value={round.answer_subtitle_position || 'bottom'}
                                                onChange={(value) => updateRound(roundIndex, { answer_subtitle_position: value })}
                                                style={{ width: '100%' }}
                                                options={ANSWER_SUBTITLE_POSITION_OPTIONS}
                                            />
                                        </Col>
                                    </Row>
                                    {answerImage && !round.answer_image_url && (
                                        <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
                                            The answer will use the question image until you upload a separate one.
                                        </Text>
                                    )}
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
                                    onChange={(value) => updateRound(roundIndex, { countdown_seconds: Number(value) || 5 })}
                                    addonAfter="seconds"
                                    style={{ width: '100%' }}
                                />
                            </Col>
                            <Col xs={24} md={8}>
                                <div style={{ marginBottom: 6 }}>Screen position</div>
                                <Select
                                    value={round.countdown_position}
                                    onChange={(value) => updateRound(roundIndex, { countdown_position: value })}
                                    style={{ width: '100%' }}
                                    options={COUNTDOWN_POSITION_OPTIONS}
                                />
                            </Col>
                            <Col xs={24} md={10}>
                                <div style={{ marginBottom: 6 }}>Sound effect</div>
                                <Select
                                    value={round.countdown_sound}
                                    onChange={(value) => updateRound(roundIndex, { countdown_sound: value })}
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
                                    onChange={(event) => updateRound(roundIndex, { countdown_label: event.target.value })}
                                    placeholder="e.g. Think!"
                                    maxLength={32}
                                />
                            </Col>
                            <Col xs={24} md={12}>
                                <Text type="secondary">
                                    The countdown automatically reuses this round's question image. Question and answer
                                    images stay still for a calm, focused learning pace.
                                </Text>
                            </Col>
                        </Row>
                    </Card>
                );
            })}

            <BookendList
                label="Outro"
                addLabel="Add outro scene"
                hint="Optional: shown after the last round. Add as many as you need."
                taskId={taskId}
                items={outros}
                onChange={setOutros}
            />

            <Card style={{ marginBottom: 24 }}>
                <Space wrap>
                    <Button icon={<PlusOutlined />} onClick={addRound}>Add Round</Button>
                    <Button
                        icon={<TranslationOutlined />}
                        loading={translatingAll}
                        onClick={translateAll}
                    >
                        Translate All to Chinese
                    </Button>
                    <Button type="primary" loading={assembling} onClick={handleGenerate}>
                        Generate Interactive Video
                    </Button>
                </Space>
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

export default InteractiveEditor;
