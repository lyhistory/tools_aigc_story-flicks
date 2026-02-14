import React, { useState, useEffect } from 'react';
import type { FormProps } from 'antd';
import { Button, Form, Input, Select, message, Switch, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next'
import { getVoiceList, getLLMProviders, generateVideo, getVoiceOptions } from '../../services/index';
import { VOICE_LANGUAGES, VOICE_LANGUAGES_LABELS, VOICE_PROVIDERS, LEARNER_AGE_OPTIONS } from '../../constants';
import styles from './index.module.css'
import { useVideoStore } from "../../stores/index";

type FieldType = {
    text_llm_provider?: string; // Text LLM provider
    image_llm_provider?: string; // Image LLM provider
    text_llm_model?: string; // Text LLM model
    image_llm_model?: string; // Image LLM model
    use_inpainting?: boolean; // Use img2img/inpainting
    avoid_exact_counts?: boolean; // Avoid exact numeric counts
    resolution?: string; // 分辨率
    test_mode?: boolean; // 是否为测试模式
    task_id?: string; // 任务ID，测试模式才需要
    segments: number; // 分段数量 (1-10)
    language?: Language; // 故事语言
    story_prompt?: string; // 故事提示词，测试模式不需要，非测试模式必填
    topic_type?: "dialogue" | "explanation" | "scene";
    image_style?: string; // 图片风格，测试模式不需要，非测试模式必填
    subject?: string; // Optional cover subject
    learner_age?: "3-5" | "6-8" | "9-12" | "13-15" | "16-18";
    voice_provider?: string; // gtts | edge-tts | google-tts
    voice_name: string; // 语音名称，需要和语言匹配
    voice_rate: number; // 语音速率，默认写1
    karaoke?: boolean; // Karaoke word-level highlight
};


const App: React.FC = () => {
    const { setVideoUrl }  = useVideoStore();
    const { t } = useTranslation();
    const [form] = Form.useForm();
    const [voiceOptions, setVoiceOptions] = useState<{ providers: string[]; languages: Record<string, string[]> }>({ providers: [], languages: {} });
    const [voiceLanguages, setVoiceLanguages] = useState<string[]>(VOICE_LANGUAGES);
    const [nowVoiceList, setNowVoiceList] = useState<string[]>([]);
    const [llmProviders, setLLMProviders] = useState<{ 
		textLLMProviders: string[], 
		imageLLMProviders: string[],
		defaults?: { 
            text_llm_model?: string;
            image_llm_model?: string;
            resolution?: string;
        }
	}>({ textLLMProviders: [], imageLLMProviders: [] });

    useEffect(() => {
        console.log('useEffect');
        getLLMProviders().then(res => {
            console.log('llmProviders', res);
            setLLMProviders(res);
			// Set default model & resolution values from backend
            form.setFieldsValue({
                    text_llm_model: res.text_llm_model,
                    image_llm_model: res.image_llm_model,
                    resolution: res.resolution || '1080*1920', // fallback
                    avoid_exact_counts: true,
                    text_llm_provider: res.text_llm_provider || res.textLLMProviders?.[0],
                    image_llm_provider: res.image_llm_provider || res.imageLLMProviders?.[0],
                    learner_age: '3-5',
                });
        }).catch(err => {
            console.log(err);
        })
        getVoiceOptions().then(res => {
            setVoiceOptions(res);
            const provider = res.providers?.[0] || 'gtts';
            form.setFieldsValue({ voice_provider: provider });
            const langs = res.languages?.[provider] || VOICE_LANGUAGES;
            setVoiceLanguages(langs);
            const initLang = provider === 'gtts' ? 'fixed-en-GB' : (langs[0] || 'en-GB');
            form.setFieldsValue({ language: initLang });
            if (provider === 'gtts') {
                form.setFieldsValue({ voice_name: 'default' });
            } else {
                getVoiceList({ provider, language: initLang }).then(vres => {
                    setNowVoiceList(vres?.voices || []);
                    if (vres?.voices?.length > 0) {
                        form.setFieldsValue({ voice_name: vres.voices[0].replace('-Female', '').replace('-Male', '') });
                    }
                });
            }
        }).catch(err => {
            console.log(err);
        });
    }, []);
    const onFinish: FormProps<FieldType>['onFinish'] = (values) => {
        console.log('Success:', values);
        message.loading('Generating Video, please wait...', 0);
        generateVideo(values).then(res => {
            message.destroy();
            if (res?.success === false) {
                throw new Error(res?.message || 'Generate Video Failed');
            }
            console.log('generateVideo res', res);
            message.success('Generate Video Success');
            if (res?.data?.video_url) {
                setVideoUrl(res?.data?.video_url);
            }
        }).catch(err => {
            message.error('Generate Video Failed: ' + err?.message || JSON.stringify(err), 10);
            console.log('generateVideo err', err);
        })
    };
    
    const onFinishFailed: FormProps<FieldType>['onFinishFailed'] = (errorInfo) => {
        console.log('Failed:', errorInfo);
    };
    const languageLabelMap = new Map(VOICE_LANGUAGES_LABELS.map((l) => [l.value, l.label]));

    const handleVoiceProviderChange = (provider: string) => {
        const fallbackLangs = provider === 'gtts'
            ? ['fixed-en-GB']
            : VOICE_LANGUAGES.filter((l) => !l.startsWith('fixed-'));
        const langs = voiceOptions.languages?.[provider] || fallbackLangs;
        setVoiceLanguages(langs);
        const nextLang = provider === 'gtts' ? 'fixed-en-GB' : (langs[0] || 'en-GB');
        form.setFieldsValue({ language: nextLang });
        if (provider === 'gtts') {
            setNowVoiceList([]);
            form.setFieldsValue({ voice_name: 'default' });
            return;
        }
        getVoiceList({ provider, language: nextLang }).then(res => {
            setNowVoiceList(res?.voices || []);
            if (res?.voices?.length > 0) {
                form.setFieldsValue({ voice_name: res.voices[0].replace('-Female', '').replace('-Male', '') });
            }
        });
    };
    // defaults are set from backend in the initial load effect
        return (
            <div className={styles.formDiv}>
                <Form
                    form={form}
                    name="basic"
                    labelCol={{ span: 8 }}
                    wrapperCol={{ span: 16 }}
                    style={{ minWidth: 600, justifyContent: 'flex-start' }}
                    initialValues={{ remember: true }}
                    onFinish={onFinish}
                    onFinishFailed={onFinishFailed}
                    autoComplete="off"
                >
                    <Form.Item<FieldType>
                        label={t('storyForm.txtLLMProvider')}
                        name="text_llm_provider"
                        rules={[{ required: true, message: t('storyForm.txtLLMProviderMissMsg') }]}
                    >
                        <Select>
                            {
                                llmProviders.textLLMProviders.map((provider) => {
                                    return <Select.Option value={provider}>{provider}</Select.Option>
                                })
                            }
                        </Select>
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.imgLLMProvider')}
                        name="image_llm_provider"
                        rules={[{ required: true, message: t('storyForm.imgLLMProviderMissMsg') }]}
                    >
                        <Select>
                            {
                                llmProviders.imageLLMProviders.map((provider) => {
                                    return <Select.Option value={provider}>{provider}</Select.Option>
                                })
                            }
                        </Select>
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.txtLLMModel')}
                        name="text_llm_model"
                        rules={[{ required: true, message: t('storyForm.txtLLMModelMissMsg') }]}
                    >
                        <Input placeholder={t('storyForm.textLLMPlaceholder')} />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.imgLLMModel')}
                        name="image_llm_model"
                        rules={[{ required: true, message: t('storyForm.imgLLMModelMissMsg') }]}
                    >
                        <Input placeholder={t('storyForm.imageLLMPlaceholder')} />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.resolution')}
                        name="resolution"
                        rules={[{ required: true, message: t('storyForm.resolutionMissMsg') }]}
                    >
                        <Input placeholder={t('storyForm.resolutionPlaceholder')} />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={
                            <Tooltip title="Use img2img/inpainting to keep visual continuity. Turn off for text-to-image models.">
                                <span>Use Inpainting</span>
                            </Tooltip>
                        }
                        name="use_inpainting"
                        valuePropName="checked"
                    >
                        <Switch />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={
                            <Tooltip title="Avoid exact numbers like 'two cats' when the image model struggles with counting.">
                                <span>Avoid Exact Counts</span>
                            </Tooltip>
                        }
                        name="avoid_exact_counts"
                        valuePropName="checked"
                        initialValue={true}
                    >
                        <Switch defaultChecked />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label="Voice Provider"
                        name="voice_provider"
                        rules={[{ required: true, message: "Please select a voice provider" }]}
                    >
                        <Select onChange={handleVoiceProviderChange}>
                            {VOICE_PROVIDERS.map((p) => (
                                <Select.Option value={p.value}>{p.label}</Select.Option>
                            ))}
                        </Select>
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.videoLanguage')}
                        name="language"
                        rules={[{ required: true, message: t('storyForm.videoLanguageMissMsg') }]}
                    >
                        <Select
                            onChange={(value) => {
                                const provider = form.getFieldValue('voice_provider') || 'gtts';
                                if (provider === 'gtts' || value?.startsWith('fixed')) {
                                    setNowVoiceList([]);
                                    form.setFieldsValue({ voice_name: 'default' });
                                    return;
                                }
                                getVoiceList({ provider, language: value }).then(res => {
                                    setNowVoiceList(res?.voices || []);
                                    if (res?.voices?.length > 0) {
                                        form.setFieldsValue({ voice_name: res.voices[0].replace('-Female', '').replace('-Male', '') });
                                    }
                                });
                            }}
                        >
                            {
                                voiceLanguages.map((lang) => {
                                    return <Select.Option value={lang}>{languageLabelMap.get(lang) || lang}</Select.Option>
                                })
                            }
                        </Select>
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(prev, curr) => prev.language !== curr.language}>
                        {({ getFieldValue }) => {
                            const lang = getFieldValue('language');
                            if (lang?.startsWith('fixed')) {
                                return (
                                    <div style={{ color: '#888', marginBottom: 16 }}>
                                        Using fixed gTTS voice (no selection needed for British English)
                                        <Form.Item name="voice_name" noStyle>
                                            <Input type="hidden" /> {/* hidden input to force inclusion */}
                                        </Form.Item>
                                    </div>
                                );
                            }
                            return (
                                <Form.Item<FieldType>
                                    label={t('storyForm.voiceName')}
                                    name="voice_name"
                                    rules={[{ required: true, message: t('storyForm.voiceNameMissMsg') }]}
                                >
                                    <Select>
                                        {
                                            nowVoiceList.map((voice) => {
                                                return <Select.Option value={voice.replace('-Female', '').replace('-Male', '')}>{voice}</Select.Option>
                                            })
                                        }
                                    </Select>
                                </Form.Item>
                            );
                        }}
                    </Form.Item>
                    <Form.Item<FieldType>
                        label="Karaoke"
                        name="karaoke"
                        valuePropName="checked"
                        initialValue={false}
                    >
                        <Switch />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label="Subject (Optional)"
                        name="subject"
                    >
                        <Input placeholder="Plural" />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label="Learner Age"
                        name="learner_age"
                        initialValue="3-5"
                    >
                        <Select>
                            {LEARNER_AGE_OPTIONS.map((item) => (
                                <Select.Option key={item.value} value={item.value}>
                                    {item.label}
                                </Select.Option>
                            ))}
                        </Select>
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.textPrompt')}
                        name="story_prompt"
                        rules={[{ required: true, message: t('storyForm.textPromptMissMsg') }]}
                    >
                        <Input.TextArea rows={4} placeholder={t('storyForm.storyPromptPlaceholder')} />
                    </Form.Item>
                    <Form.Item<FieldType>
                        label="Topic Type"
                        name="topic_type"
                        initialValue="explanation"
                        rules={[{ required: true, message: "Please select a topic type" }]}
                    >
                        <Select>
                            <Select.Option value="dialogue">Dialogue (kid + kid/teacher/parent)</Select.Option>
                            <Select.Option value="explanation">Explanation (word/grammar/science)</Select.Option>
                            <Select.Option value="scene">Scene Description</Select.Option>
                        </Select>
                    </Form.Item>
                    <Form.Item<FieldType>
                        label={t('storyForm.segments')}
                        name="segments"
                        rules={[{ required: true, message: t('storyForm.segmentsMissMsg'), min: 1, max: 10 }]}
                    >
                        <Input type='number' min={1} max={10} placeholder="3" />
                    </Form.Item>
                    <Form.Item label={null}>
                        <Button type="primary" htmlType="submit">
                            {t('storyForm.submit')}
                        </Button>
                    </Form.Item>
                </Form>
            </div>
        )
}

export default App;
