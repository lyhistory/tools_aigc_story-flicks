import React, { useState, useEffect } from 'react';
import { Button, Card, Typography, Space, Popconfirm, message, Modal, Slider, Image, Select, Badge, Spin, Input } from 'antd';
import { DeleteOutlined, ReloadOutlined, SwapOutlined, CheckCircleFilled } from '@ant-design/icons';
import { assembleVideo, regenerateImage, getSubtitleFonts } from '../../services/index';
import { useVideoStore } from '../../stores/index';

const { Text } = Typography;

interface FontOption { id: string; label: string; default: boolean; }

interface StoryboardEditorProps {
    taskId: string;
    scenes: StoryScene[];
    resolution: string;
    chineseSubtitleEnabled: boolean;
    karaoke: boolean;
    subject?: string;
    topicType?: string;
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
    taskId, scenes: initialScenes, resolution, chineseSubtitleEnabled, karaoke, subject, topicType, onClose
}) => {
    const [scenes, setScenes] = useState<StoryScene[]>(() =>
        initialScenes.map((s, idx) => ({ 
            ...s, 
            extra_images: s.extra_images || [],
            original_index: s.original_index || idx + 1
        }))
    );
    const [allGeneratedImages, setAllGeneratedImages] = useState<{ label: string; url: string }[]>([]);

    useEffect(() => {
        setAllGeneratedImages(prev => {
            const poolMap = new Map<string, { label: string; url: string }>();
            
            // Keep everything we've already discovered
            prev.forEach(img => poolMap.set(img.url, img));
            
            // 1. Add originals that might not be in prev yet
            initialScenes.forEach((s, i) => {
                if (s.url && !poolMap.has(s.url)) poolMap.set(s.url, { label: `Scene ${i + 1} Original`, url: s.url });
            });

            // 2. Add newly discovered active URLs
            scenes.forEach((s, i) => {
                if (s.url && !poolMap.has(s.url)) {
                    poolMap.set(s.url, { label: `Scene ${i + 1} Regen`, url: s.url });
                }
                if (s.image_slots) {
                    s.image_slots.forEach((slot, sIdx) => {
                        if (slot.url && !poolMap.has(slot.url)) {
                            poolMap.set(slot.url, { label: `Scene ${i + 1} Slot ${sIdx + 1}`, url: slot.url });
                        }
                    });
                }
            });
            
            // Only trigger re-render if something actually changed length
            if (poolMap.size === prev.length) return prev;
            return Array.from(poolMap.values());
        });
    }, [initialScenes, scenes]);

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

    // Prompt Regen Modal State
    const [regenModalVisible, setRegenModalVisible] = useState(false);
    const [regenSceneIdx, setRegenSceneIdx] = useState<number | null>(null);
    const [regenPromptText, setRegenPromptText] = useState("");

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
            
            // If they are deleting before slots even initialized properly, fallback construct it
            if (newSlots.length === 0 && s.url) {
                newSlots = [{ url: s.url, sub_start: null, sub_end: null }];
            }
            
            newSlots.splice(slotIdx, 1);
            
            // If we deleted the primary slot or all slots, we MUST clear s.url so the video assembler gets the empty array
            const newUrl = newSlots.length > 0 ? newSlots[0].url : "";
            
            return { ...s, url: newUrl, image_slots: newSlots };
        }));
    };

    // Helper to count lines in script (by sentence delimiters)
    // Matches backend's split_string_by_punctuations(s, split_minor_punct=False)
    const getScriptLines = (script: string) => {
        if (!script) return [];
        const activePunctuations = ["?", ".", "!", "…", "？", "。", "！", "..."];
        let result: string[] = [];
        let txt = "";
        
        for (let i = 0; i < script.length; i++) {
            const char = script[i];
            
            if (char === "\n") {
                if (txt.trim()) result.push(txt.trim());
                txt = "";
                continue;
            }
            
            // Handle numeric decimals like in backend (1.5 should not split)
            const prev = i > 0 ? script[i-1] : "";
            const next = i < script.length - 1 ? script[i+1] : "";
            if (char === "." && /\d/.test(prev) && /\d/.test(next)) {
                txt += char;
                continue;
            }

            txt += char;
            if (activePunctuations.includes(char)) {
                while (i + 1 < script.length && ['\'', '"', '”', '’', ')', ']'].includes(script[i+1])) {
                    txt += script[i+1];
                    i++;
                }
                if (txt.trim()) result.push(txt.trim());
                txt = "";
            }
        }
        if (txt.trim()) result.push(txt.trim());

        // Basic validation: must contain at least one word/character
        return result.filter(seg => /[\w\u4e00-\u9fa5]/.test(seg));
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

    const openRegenModal = (index: number, scene: StoryScene) => {
        setRegenSceneIdx(index);
        setRegenPromptText(scene.scene_prompt || "");
        setRegenModalVisible(true);
    };

    const handleConfirmRegen = async () => {
        if (regenSceneIdx === null) return;
        const index = regenSceneIdx;
        const editedPrompt = regenPromptText.trim();
        
        setRegenModalVisible(false);
        setRegeneratingIndexes(prev => ({ ...prev, [index]: true }));
        message.loading({ content: `Regenerating image for scene ${index + 1}...`, key: 'regen_img' });
        try {
            const res = await regenerateImage({
                task_id: taskId,
                scene_index: index + 1,
                scene_prompt: editedPrompt,
                // Inherit global resolution setting, llm providers etc can be pushed as well but keeping simple
                resolution: resolution
            });
            if (res?.success === false) throw new Error(res?.message || 'Regeneration Failed');
            message.success({ content: `Scene ${index + 1} image regenerated!`, key: 'regen_img' });
                if (res?.data?.image_url) {
                    const newUrl = res.data.image_url;
                    const newScenes = [...scenes];
                    newScenes[index].url = newUrl;
                    // Important: sync with image_slots as well so the UI updates
                    if (newScenes[index].image_slots && newScenes[index].image_slots.length > 0) {
                        newScenes[index].image_slots[0].url = newUrl;
                    } else {
                        newScenes[index].image_slots = [{ url: newUrl, sub_start: null, sub_end: null }];
                    }
                    setScenes(newScenes);
                }
        } catch (err: any) {
            message.error({ content: 'Image Regeneration Failed: ' + err?.message, key: 'regen_img' });
        } finally {
            setRegeneratingIndexes(prev => ({ ...prev, [index]: false }));
            setRegenSceneIdx(null);
            setRegenPromptText("");
        }
    };

    const previewText = "Hello, how are you?";

    return (
        <div style={{ marginTop: 24, padding: 24, background: '#f5f5f5', borderRadius: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, marginBottom: 8 }}>Storyboard Timeline</h2>
                    <Space>
                        {topicType && <Badge count={`Topic: ${topicType}`} style={{ backgroundColor: '#108ee9' }} />}
                        {subject && <Badge count={`Subject: ${subject}`} style={{ backgroundColor: '#87d068' }} />}
                    </Space>
                </div>
                <Space>
                    <Button onClick={() => {
                        setScenes(initialScenes.map((s, idx) => ({ 
                            ...s, 
                            extra_images: s.extra_images || [],
                            original_index: s.original_index || idx + 1
                        })));
                        message.success("Storyboard restored to initial state.");
                    }}>Restore Initial State</Button>
                    <Button onClick={onClose}>Close Editor</Button>
                </Space>
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
                                {(scene.image_slots && scene.image_slots.length > 0
                                    ? scene.image_slots
                                    : (scene.url ? [{ url: scene.url, sub_start: null, sub_end: null }] : [])
                                ).map((slot, slotIdx) => {
                                    const lines = getScriptLines(scene.script);
                                    const isRegenerating = slotIdx === 0 && !!regeneratingIndexes[idx];
                                    return (
                                        <div key={slotIdx} style={{ flexShrink: 0, width: 100 }}>
                                            <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>
                                                {slotIdx === 0 ? "Primary" : `Slot ${slotIdx + 1}`}
                                            </div>
                                            <div style={{ width: 100, height: 100, background: '#e0e0e0', borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                                                <Spin spinning={isRegenerating}>
                                                    {slot.url ? (
                                                        <Image
                                                            src={slot.url}
                                                            alt={`Slot ${slotIdx + 1}`}
                                                            width={100}
                                                            height={100}
                                                            style={{ objectFit: 'cover', display: 'block' }}
                                                            preview={{ mask: <div style={{ fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>🔍</div> }}
                                                        />
                                                    ) : (
                                                        <div style={{ width: 100, height: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                                            <div style={{ fontSize: 24, color: '#999' }}>🖼️</div>
                                                        </div>
                                                    )}
                                                </Spin>
                                                
                                                <Popconfirm title="Remove this image slot?" onConfirm={() => removeExtraImage(idx, slotIdx)}>
                                                    <Button
                                                        size="small"
                                                        danger
                                                        type="text"
                                                        icon={<DeleteOutlined />}
                                                        style={{ position: 'absolute', top: 2, right: 2, background: 'rgba(255,255,255,0.8)', padding: '0 2px' }}
                                                    />
                                                </Popconfirm>
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
                                            onClick={() => openRegenModal(idx, scene)}
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
                                <div style={{ margin: '8px 0', fontSize: 13, lineHeight: '1.8' }}>
                                    {getScriptLines(scene.script).map((line, lIdx) => (
                                        <div key={lIdx} style={{ marginBottom: 6, display: 'flex', alignItems: 'flex-start' }}>
                                            <Badge 
                                                count={lIdx + 1} 
                                                size="small" 
                                                style={{ backgroundColor: '#52c41a', marginRight: 10, flexShrink: 0, marginTop: 4 }} 
                                            />
                                            <Text>{line}</Text>
                                        </div>
                                    ))}
                                </div>
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
                    {allGeneratedImages.map((img, poolIdx) => (
                        <Card
                            key={poolIdx}
                            hoverable
                            size="small"
                            style={{ width: 140 }}
                            onClick={() => handleReuseSelect(img.url)}
                            cover={<img alt={img.label} src={img.url} style={{ height: 140, objectFit: 'cover' }} />}
                        >
                            <Card.Meta title={img.label} />
                        </Card>
                    ))}
                </div>
            </Modal>

            <Modal
                title={`Regenerate Scene ${(regenSceneIdx ?? 0) + 1} Image`}
                open={regenModalVisible}
                onOk={handleConfirmRegen}
                onCancel={() => {
                    setRegenModalVisible(false);
                    setRegenSceneIdx(null);
                    setRegenPromptText("");
                }}
                okText="Generate"
                cancelText="Cancel"
            >
                <div style={{ marginBottom: 16 }}>
                    <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                        Edit the prompt for the new image. This will not change your story text, only the visual representation.
                    </Text>
                    <Input.TextArea
                        rows={6}
                        value={regenPromptText}
                        onChange={e => setRegenPromptText(e.target.value)}
                    />
                </div>
            </Modal>
        </div>
    );
};

export default StoryboardEditor;
