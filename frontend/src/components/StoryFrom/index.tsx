import React, { useState, useEffect } from 'react';
import type { FormProps } from 'antd';
import { Button, Form, Input, Select, message } from 'antd';
import { useTranslation } from 'react-i18next'
import { getVoiceList, getLLMProviders, generateVideo } from '../../services/index';
import { VOICE_LANGUAGES, VOICE_LANGUAGES_LABELS } from '../../constants';
import { getSelectVoiceList } from '../../utils/index';
import styles from './index.module.css'
import { useVideoStore } from "../../stores/index";

type FieldType = {
    text_llm_provider?: string; // Text LLM provider
    image_llm_provider?: string; // Image LLM provider
    text_llm_model?: string; // Text LLM model
    image_llm_model?: string; // Image LLM model
    resolution?: string; // 分辨率
    test_mode?: boolean; // 是否为测试模式
    task_id?: string; // 任务ID，测试模式才需要
    segments: number; // 分段数量 (1-10)
    language?: Language; // 故事语言
    story_prompt?: string; // 故事提示词，测试模式不需要，非测试模式必填
    image_style?: string; // 图片风格，测试模式不需要，非测试模式必填
    voice_name: string; // 语音名称，需要和语言匹配
    voice_rate: number; // 语音速率，默认写1
};


const App: React.FC = () => {
    const { setVideoUrl }  = useVideoStore();
    const { t } = useTranslation();
    const [form] = Form.useForm();
    const [allVoiceList, setAllVoiceList] = useState<string[]>([]);
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
                });
        }).catch(err => {
            console.log(err);
        })
        getVoiceList({ area: VOICE_LANGUAGES }).then(res => {
            console.log('voiceList', res?.voices);
            if (res?.voices?.length > 0) {
                setAllVoiceList(res?.voices)
            }
        }).catch(err => {
            console.log(err);
        })
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
    useEffect(() => {
        form.setFieldsValue({
            text_llm_provider: llmProviders.textLLMProviders?.[0],
            image_llm_provider: llmProviders.imageLLMProviders?.[0],
         });
      }, [llmProviders.imageLLMProviders, llmProviders.textLLMProviders]);
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
                        initialValue={llmProviders.textLLMProviders?.[0]}
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
                        initialValue={llmProviders.imageLLMProviders?.[0]}
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
                        label={t('storyForm.videoLanguage')}
                        name="language"
                        rules={[{ required: true, message: t('storyForm.videoLanguageMissMsg') }]}
                    >
                        <Select
                            onChange={(value) => {
                                if (value?.startsWith('fixed')) {
                                    form.setFieldsValue({ voice_name: 'default' });
                                }else{
                                    let voiceList = getSelectVoiceList(value, allVoiceList);
                                    setNowVoiceList(voiceList);
                                    form.setFieldsValue({ voice_name: voiceList[0].replace('-Female', '').replace('-Male', '') });
                                }
                            }}
                        >
                            {
                                VOICE_LANGUAGES_LABELS.map((language) => {
                                    return <Select.Option value={language.value}>{language.label}</Select.Option>
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
                        label={t('storyForm.textPrompt')}
                        name="story_prompt"
                        rules={[{ required: true, message: t('storyForm.textPromptMissMsg') }]}
                    >
                        <Input.TextArea rows={4} placeholder={t('storyForm.storyPromptPlaceholder')} />
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