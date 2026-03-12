import React, { useState, useEffect } from 'react';
import { Button, Card, Typography, Space, Popconfirm, message, Modal, Slider, Image, Select, Badge } from 'antd';
import { DeleteOutlined, ReloadOutlined, SwapOutlined, CheckCircleFilled } from '@ant-design/icons';
import { assembleVideo, regenerateImage, getSubtitleFonts } from '../../services/index';
import { useVideoStore } from '../../stores/index';

const { Text, Paragraph } = Typography;

interface FontOption { id: string; label: string; default: boolean; }

interface StoryboardEditorProps {
    taskId: string;
    scenes: StoryScene[];
    resolution: string;
    chineseSubtitleEnabled: boolean;
    karaoke: boolean;
    onClose: () => void;
}

// CSS weight/style map for visual preview cards
const FONT_STYLE_MAP: Record<string, React.CSSProperties> = {
    'NotoSans-Light': { fontWeight: 300 },
    'NotoSans-Regular': { fontWeight: 400 },
    'NotoSans-Italic': { fontWeight: 400, fontStyle: 'italic' },
    'NotoSans-SemiBold': { fontWeight: 600 },
    'NotoSans-Bold': { fontWeight: 700 },
    'NotoSans-BoldItalic': { fontWeight: 700, fontStyle: 'italic' },
    'NotoSans-ExtraBold': { fontWeight: 800 },
    'NotoSans-Condensed-Regular': { fontWeight: 400, letterSpacing: '-0.04em' },
    'NotoSans-Condensed-SemiBold': { fontWeight: 600, letterSpacing: '-0.04em' },
    'NotoSans-Condensed-Bold': { fontWeight: 700, letterSpacing: '-0.04em' },
    // Decorative – browser won't render them exactly, but we can signal the mood
    'BabyPlums': { fontStyle: 'italic', fontWeight: 700, color: '#f97316' },
    'QuickCat': { fontStyle: 'italic', fontWeight: 500, letterSpacing: '0.03em' },
    'SuperAdorable': { fontWeight: 400, letterSpacing: '0.05em' },
    'SuperJoyful': { fontWeight: 700, letterSpacing: '0.04em', color: '#a855f7' },
    'SuperMaples': { fontStyle: 'italic', fontWeight: 600, letterSpacing: '0.03em' },
    'SuperScribble': { fontWeight: 400, letterSpacing: '0.06em', color: '#10b981' },
    'UbuntuCondensed': { fontWeight: 400, letterSpacing: '-0.02em' },
};

const DEFAULT_FONT = 'NotoSans-Bold';
const DEFAULT_FONT_SIZE = 58;

const StoryboardEditor: React.FC<StoryboardEditorProps> = ({
    taskId, scenes: initialScenes, resolution, chineseSubtitleEnabled, karaoke, onClose
}) => {
    const [scenes, setScenes] = useState<StoryScene[]>(() =>
        initialScenes.map(s => ({ ...s, extra_images: s.extra_images || [] }))
    );
    // Snapshot of the ORIGINAL scene images (taken at mount) — used in reuse modal
    // so swapping scene 1 & 2 still shows scene 1's original image.
    const [originalImages] = useState<{ idx: number; url: string }[]>(() =>
        initialScenes
            .map((s, i) => s.url ? { idx: i, url: s.url } : null)
            .filter(Boolean) as { idx: number; url: string }[]
    );

    const { setVideoUrl, assembling, setAssembling } = useVideoStore();
    const [regeneratingIndexes, setRegeneratingIndexes] = useState<Record<number, boolean>>({});

    // Font picker state
    const [fontOptions, setFontOptions] = useState<FontOption[]>([]);
    const [selectedFont, setSelectedFont] = useState<string>(DEFAULT_FONT);
    const [fontSize, setFontSize] = useState<number>(DEFAULT_FONT_SIZE);
    const [subtitleColor, setSubtitleColor] = useState<string>('#F472B6');

    // Reuse Image Modal State
    const [reuseModalVisible, setReuseModalVisible] = useState(false);
    const [targetSceneIdx, setTargetSceneIdx] = useState<number | null>(null);
    // 'replace-primary' = replace scene's main image; 'add-extra' = add to extras list
    const [reuseMode, setReuseMode] = useState<'replace-primary' | 'add-extra'>('replace-primary');

    useEffect(() => {
        getSubtitleFonts().then(res => {
            if (res?.data?.fonts) {
                setFontOptions(res.data.fonts);
                const def = res.data.default;
                if (def) setSelectedFont(def);
            }
        }).catch(() => {
            setFontOptions(Object.keys(FONT_STYLE_MAP).map(id => ({
                id,
                label: id.replace('NotoSans-', 'Noto Sans ').replace(/-/g, ' '),
                default: id === DEFAULT_FONT,
            })));
        });
    }, []);

    const openReuseModal = (idx: number, mode: 'replace-primary' | 'add-extra' = 'replace-primary') => {
        setTargetSceneIdx(idx);
        setReuseMode(mode);
        setReuseModalVisible(true);
    };

    const handleReuseSelect = (sourceUrl: string) => {
        if (targetSceneIdx !== null && sourceUrl) {
            setScenes(prev => prev.map((s, i) => {
                if (i !== targetSceneIdx) return s;
                let newSlots = [...(s.image_slots || [])];
                
                // Initialize image_slots if empty, including primary
                if (newSlots.length === 0 && s.url) {
                    newSlots = [{ url: s.url, sub_start: null, sub_end: null }];
                }

                if (reuseMode === 'add-extra') {
                    newSlots.push({ url: sourceUrl, sub_start: null, sub_end: null });
                } else {
                    // Replace primary (slot 0)
                    if (newSlots.length > 0) {
                        newSlots[0] = { ...newSlots[0], url: sourceUrl };
                    } else {
                        newSlots = [{ url: sourceUrl, sub_start: null, sub_end: null }];
                    }
                }
                
                return { ...s, url: newSlots[0].url, image_slots: newSlots };
            }));
            
            message.success(
                reuseMode === 'add-extra'
                    ? `Extra image added to Scene ${targetSceneIdx + 1}`
                    : `Primary image replaced for Scene ${targetSceneIdx + 1}`
            );
        }
        setReuseModalVisible(false);
        setTargetSceneIdx(null);
    };

    const updateSlotRange = (sceneIdx: number, slotIdx: number, start: number | null, end: number | null) => {
        setScenes(prev => prev.map((s, i) => {
            if (i !== sceneIdx) return s;
            const newSlots = [...(s.image_slots || [])];
            if (newSlots[slotIdx]) {
                newSlots[slotIdx] = { ...newSlots[slotIdx], sub_start: start, sub_end: end };
            }
            return { ...s, image_slots: newSlots };
        }));
    };

    const removeExtraImage = (sceneIdx: number, slotIdx: number) => {
        setScenes(prev => prev.map((s, i) => {
            if (i !== sceneIdx) return s;
            let newSlots = [...(s.image_slots || [])];
            newSlots.splice(slotIdx, 1);
            return { ...s, url: newSlots[0]?.url || s.url, image_slots: newSlots };
        }));
    };

    // Helper to count lines in script (by sentence delimiters)
    const getScriptLines = (script: string) => {
        if (!script) return [];
        // Match backend's split_minor_punct=False logic (major punct + newline)
        // Symbols: ?, ., !, …, ？, 。, ！, ...
        return script
            .split(/(?<=[.!?…？。！])\s+|\n/)
            .filter(l => l.trim().length > 0 && /[\w\u4e00-\u9fa5]/.test(l));
    };

    const handleDelete = (index: number) => {
        const newScenes = [...scenes];
        newScenes.splice(index, 1);
        setScenes(newScenes);
    };

    const handleAssemble = async () => {
        if (scenes.length === 0) {
            message.warning("Timeline is empty! Cannot assemble empty video.");
            return;
        }
        setAssembling(true);
        message.loading({ content: 'Assembling Video Phase 2, please wait...', key: 'assemble' });
        try {
            const res = await assembleVideo({
                task_id: taskId,
                scenes: scenes,
                resolution,
                chinese_subtitle_enabled: chineseSubtitleEnabled,
                karaoke: karaoke,
                subtitle_font: selectedFont,
                subtitle_font_size: fontSize,
                subtitle_color: subtitleColor,
            });
            if (res.success && res.data?.video_url) {
                message.success({ content: 'Video assembled successfully!', key: 'assemble' });
                setVideoUrl(res.data.video_url + '?t=' + Date.now());
                // Intentionally keeping editor open so users can re-edit and re-generate
            } else {
                throw new Error(res.message || "Unknown error during assembly");
            }
        } catch (e: any) {
            message.error({ content: 'Assembly failed: ' + e.message, key: 'assemble' });
        } finally {
            setAssembling(false);
        }
    };

    const handleRegenerateImage = async (index: number, scene: StoryScene) => {
        setRegeneratingIndexes(prev => ({ ...prev, [index]: true }));
        message.loading({ content: `Regenerating image for scene ${index + 1}...`, key: 'regen_img' });
        try {
            const res = await regenerateImage({
                task_id: taskId,
                scene_index: index + 1,
                scene_prompt: scene.scene_prompt,
                // Inherit global resolution setting, llm providers etc can be pushed as well but keeping simple
                resolution: resolution
            });
            if (res?.success === false) throw new Error(res?.message || 'Regeneration Failed');
            message.success({ content: `Scene ${index + 1} image regenerated!`, key: 'regen_img' });
            if (res?.data?.image_url) {
                const newScenes = [...scenes];
                newScenes[index].url = res.data.image_url;
                setScenes(newScenes);
            }
        } catch (err: any) {
            message.error({ content: 'Image Regeneration Failed: ' + err?.message, key: 'regen_img' });
        } finally {
            setRegeneratingIndexes(prev => ({ ...prev, [index]: false }));
        }
    };

    const previewText = "Hello, how are you?";

    return (
        <div style={{ marginTop: 24, padding: 24, background: '#f5f5f5', borderRadius: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2>Storyboard Timeline</h2>
                <Button onClick={onClose}>Close Editor</Button>
            </div>

            {/* Font Picker Section */}
            <Card
                size="small"
                title="Subtitle Font Style"
                style={{ marginBottom: 20 }}
            >
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16 }}>
                    {fontOptions.map(font => {
                        const style = FONT_STYLE_MAP[font.id] || { fontWeight: 400 };
                        const isSelected = selectedFont === font.id;
                        return (
                            <div
                                key={font.id}
                                onClick={() => setSelectedFont(font.id)}
                                style={{
                                    cursor: 'pointer',
                                    border: isSelected ? '2px solid #1677ff' : '1.5px solid #d9d9d9',
                                    borderRadius: 8,
                                    padding: '10px 14px',
                                    background: isSelected ? '#e8f4ff' : '#fff',
                                    minWidth: 150,
                                    position: 'relative',
                                    transition: 'all 0.15s',
                                    boxShadow: isSelected ? '0 0 0 2px rgba(22,119,255,0.2)' : undefined,
                                }}
                            >
                                {isSelected && (
                                    <CheckCircleFilled style={{ color: '#1677ff', position: 'absolute', top: 6, right: 6, fontSize: 14 }} />
                                )}
                                <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>{font.label}</div>
                                <div style={{
                                    fontSize: 18,
                                    color: subtitleColor,
                                    lineHeight: 1.3,
                                    ...style,
                                }}>
                                    {previewText}
                                </div>
                            </div>
                        );
                    })}
                </div>

                <div style={{ marginBottom: 12 }}>
                    <Text style={{ display: 'block', marginBottom: 8 }}>Subtitle Color</Text>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {[
                            { hex: '#F472B6', name: 'Pink (default)' },
                            { hex: '#FFFFFF', name: 'White' },
                            { hex: '#FACC15', name: 'Yellow' },
                            { hex: '#4ADE80', name: 'Green' },
                            { hex: '#60A5FA', name: 'Blue' },
                            { hex: '#F87171', name: 'Red' },
                            { hex: '#FB923C', name: 'Orange' },
                            { hex: '#A78BFA', name: 'Purple' },
                            { hex: '#000000', name: 'Black' },
                        ].map(c => (
                            <div
                                key={c.hex}
                                title={c.name}
                                onClick={() => setSubtitleColor(c.hex)}
                                style={{
                                    width: 32, height: 32, borderRadius: '50%',
                                    background: c.hex,
                                    border: subtitleColor === c.hex ? '3px solid #1677ff' : '2px solid #d9d9d9',
                                    cursor: 'pointer',
                                    boxShadow: subtitleColor === c.hex ? '0 0 0 2px rgba(22,119,255,0.3)' : undefined,
                                    transition: 'all 0.12s',
                                    flexShrink: 0,
                                }}
                            />
                        ))}
                        {/* Custom hex input */}
                        <input
                            type="color"
                            value={subtitleColor}
                            onChange={e => setSubtitleColor(e.target.value)}
                            title="Custom color"
                            style={{ width: 32, height: 32, padding: 0, border: 'none', borderRadius: '50%', cursor: 'pointer' }}
                        />
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <Text style={{ whiteSpace: 'nowrap' }}>Font Size: <b>{fontSize}px</b></Text>
                    <Slider
                        min={36}
                        max={100}
                        step={2}
                        value={fontSize}
                        onChange={setFontSize}
                        style={{ flex: 1, minWidth: 200 }}
                    />
                    <div style={{
                        padding: '4px 12px',
                        background: 'rgba(0,0,0,0.75)',
                        borderRadius: 6,
                        color: subtitleColor,
                        ...(FONT_STYLE_MAP[selectedFont] || {}),
                        fontSize: Math.min(Math.max(fontSize * 0.45, 12), 28),
                    }}>
                        {previewText}
                    </div>
                </div>
            </Card>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {scenes.map((scene, idx) => (
                    <Card key={idx} size="small" style={{ width: '100%' }}>
                        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
                            {/* Image slots UI - Left Side */}
                            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', maxWidth: '60%' }}>
                                {(scene.image_slots || (scene.url ? [{ url: scene.url, sub_start: null, sub_end: null }] : [])).map((slot, slotIdx) => {
                                    const lines = getScriptLines(scene.script);
                                    return (
                                        <div key={slotIdx} style={{ flexShrink: 0, width: 100 }}>
                                            <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>
                                                {slotIdx === 0 ? "Primary" : `Slot ${slotIdx + 1}`}
                                            </div>
                                            <div style={{ width: 100, height: 100, background: '#e0e0e0', borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                                                <Image
                                                    src={slot.url}
                                                    alt={`Slot ${slotIdx + 1}`}
                                                    width={100}
                                                    height={100}
                                                    style={{ objectFit: 'cover', display: 'block' }}
                                                    preview={{ mask: <div style={{ fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>🔍</div> }}
                                                />
                                                {slotIdx > 0 && (
                                                    <Popconfirm title="Remove this image slot?" onConfirm={() => removeExtraImage(idx, slotIdx)}>
                                                        <Button
                                                            size="small"
                                                            danger
                                                            type="text"
                                                            icon={<DeleteOutlined />}
                                                            style={{ position: 'absolute', top: 2, right: 2, background: 'rgba(255,255,255,0.8)', padding: '0 2px' }}
                                                        />
                                                    </Popconfirm>
                                                )}
                                            </div>
                                            {/* Subtitle Range Selection */}
                                            <div style={{ marginTop: 4 }}>
                                                <div style={{ fontSize: 10, color: '#999', marginBottom: 2 }}>Subtitles:</div>
                                                <Space.Compact size="small" style={{ width: '100%' }}>
                                                    <Select
                                                        value={slot.sub_start ?? undefined}
                                                        placeholder="Start"
                                                        style={{ width: '50%' }}
                                                        size="small"
                                                        onChange={(val) => updateSlotRange(idx, slotIdx, val, slot.sub_end ?? null)}
                                                        bordered={false}
                                                        allowClear
                                                    >
                                                        {lines.map((_, lIdx) => (
                                                            <Select.Option key={lIdx} value={lIdx}>{lIdx + 1}</Select.Option>
                                                        ))}
                                                    </Select>
                                                    <Select
                                                        value={slot.sub_end ?? undefined}
                                                        placeholder="End"
                                                        style={{ width: '50%' }}
                                                        size="small"
                                                        onChange={(val) => updateSlotRange(idx, slotIdx, slot.sub_start ?? null, val)}
                                                        bordered={false}
                                                        allowClear
                                                    >
                                                        {lines.map((_, lIdx) => (
                                                            <Select.Option key={lIdx} value={lIdx}>{lIdx + 1}</Select.Option>
                                                        ))}
                                                    </Select>
                                                </Space.Compact>
                                            </div>
                                        </div>
                                    );
                                })}

                                {/* Add extra image button */}
                                <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', width: 100 }}>
                                    <div style={{ fontSize: 11, color: 'transparent', marginBottom: 4 }}>-</div>
                                    <div
                                        onClick={() => openReuseModal(idx, 'add-extra')}
                                        style={{
                                            width: 100, height: 100,
                                            border: '2px dashed #d9d9d9', borderRadius: 4,
                                            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                                            cursor: 'pointer', color: '#aaa', fontSize: 12,
                                            transition: 'border-color 0.15s',
                                        }}
                                        onMouseEnter={e => (e.currentTarget.style.borderColor = '#1677ff')}
                                        onMouseLeave={e => (e.currentTarget.style.borderColor = '#d9d9d9')}
                                    >
                                        <span style={{ fontSize: 20 }}>＋</span>
                                        <span>Add Slot</span>
                                    </div>
                                </div>
                            </div>

                            {/* Script & Details - Right Side */}
                            <div style={{ flex: 1, borderLeft: '1px solid #f0f0f0', paddingLeft: 16 }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                    <Text strong>Scene {idx + 1} {scene.is_cover ? "(Cover)" : ""}</Text>
                                    <Space>
                                        <Button
                                            type="default"
                                            size="small"
                                            icon={<ReloadOutlined />}
                                            loading={!!regeneratingIndexes[idx]}
                                            onClick={() => handleRegenerateImage(idx, scene)}
                                        >
                                            Regen
                                        </Button>
                                        <Button
                                            type="default"
                                            size="small"
                                            icon={<SwapOutlined />}
                                            onClick={() => openReuseModal(idx, 'replace-primary')}
                                        >
                                            Replace...
                                        </Button>
                                        <Popconfirm title="Delete this scene?" onConfirm={() => handleDelete(idx)}>
                                            <Button type="text" danger icon={<DeleteOutlined />} size="small" />
                                        </Popconfirm>
                                    </Space>
                                </div>
                                <Paragraph style={{ margin: '8px 0', fontSize: 13, lineHeight: '1.6' }}>
                                    {getScriptLines(scene.script).map((line, lIdx) => (
                                        <span key={lIdx} style={{ display: 'inline-block', marginRight: 12, marginBottom: 4 }}>
                                            <Badge 
                                                count={lIdx + 1} 
                                                size="small" 
                                                style={{ backgroundColor: '#52c41a', marginRight: 6, fontSize: 10, minWidth: 16, height: 16, lineHeight: '16px' }} 
                                            />
                                            {line}
                                        </span>
                                    ))}
                                </Paragraph>
                                <Text type="secondary" style={{ fontSize: 11 }}><b>Prompt:</b> {scene.scene_prompt}</Text>
                            </div>
                        </div>
                    </Card>
                ))}
            </div>

            <div style={{ marginTop: 24, textAlign: 'right' }}>
                <Button type="primary" size="large" onClick={handleAssemble} loading={assembling}>
                    Step 2: Generate Video
                </Button>
            </div>

            <Modal
                title={
                    reuseMode === 'add-extra'
                        ? `Add Extra Image to Scene ${(targetSceneIdx ?? 0) + 1}`
                        : `Replace Primary Image for Scene ${(targetSceneIdx ?? 0) + 1}`
                }
                open={reuseModalVisible}
                onCancel={() => { setReuseModalVisible(false); setTargetSceneIdx(null); }}
                footer={null}
                width={740}
            >
                <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                    {reuseMode === 'add-extra'
                        ? 'Pick an image to add as an extra. The scene will cycle through images in order.'
                        : 'Pick an image to replace the primary image for this scene.'}
                </Text>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
                    {originalImages.map(({ idx: srcIdx, url }) => (
                        <Card
                            key={srcIdx}
                            hoverable
                            size="small"
                            style={{ width: 150 }}
                            onClick={() => handleReuseSelect(url)}
                            cover={<img alt={`Scene ${srcIdx + 1}`} src={url} style={{ height: 150, objectFit: 'cover' }} />}
                        >
                            <Card.Meta title={`Scene ${srcIdx + 1} (original)`} />
                        </Card>
                    ))}
                </div>
            </Modal>
        </div>
    );
};

export default StoryboardEditor;
