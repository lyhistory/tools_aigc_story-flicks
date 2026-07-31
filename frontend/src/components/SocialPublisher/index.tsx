import React, { useState, useEffect, useRef } from 'react';
import { Card, Row, Col, Typography, Button, message, Upload, Form, Input, Checkbox, Space, Spin, Modal, Image, DatePicker } from 'antd';
import { InboxOutlined, CheckCircleFilled, CloseCircleFilled, CloudUploadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { getSocialStatus, startSocialLogin, pollSocialLogin, publishSocialVideo, debugUploadSocialVideo, pollPublishStatus, stopPublishTask, skipCurrentPublishPlatform, type PublishPlatformResult } from '../../services';
import { API_BASE_URL } from '../../utils/request';

const { Title, Text } = Typography;
const { Dragger } = Upload;

const SocialPublisher: React.FC = () => {
    const [status, setStatus] = useState<{ douyin: boolean; xiaohongshu: boolean; channels: boolean }>({
        douyin: false,
        xiaohongshu: false,
        channels: false
    });
    
    // Auth Modal state
    const [authModalVisible, setAuthModalVisible] = useState(false);
    const [currentPlatform, setCurrentPlatform] = useState<string | null>(null);
    const [qrCode, setQrCode] = useState<string | null>(null);
    const authPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const publishPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // Form State
    const [form] = Form.useForm();
    const [fileList, setFileList] = useState<any[]>([]);
    const [publishing, setPublishing] = useState(false);
    const [debugging, setDebugging] = useState(false);
    
    // Publish Task Polling State
    const [taskId, setTaskId] = useState<string | null>(null);
    const [publishResults, setPublishResults] = useState<Record<string, PublishPlatformResult>>({});
    const [currentPublishingPlatform, setCurrentPublishingPlatform] = useState<string | null>(null);

    const checkStatus = async () => {
        try {
            const res = await getSocialStatus();
            if (res.success && res.data) {
                setStatus(res.data);
                return res.data;
            }
        } catch (e) {
            console.error("Failed to check status", e);
        }
        return null;
    };

    const clearAuthPolling = () => {
        if (authPollingRef.current) {
            clearInterval(authPollingRef.current);
            authPollingRef.current = null;
        }
    };

    const clearPublishPolling = () => {
        if (publishPollingRef.current) {
            clearInterval(publishPollingRef.current);
            publishPollingRef.current = null;
        }
    };

    useEffect(() => {
        checkStatus().then(data => {
            if (data) {
                const connected = Object.keys(data).filter(k => data[k as keyof typeof data]);
                form.setFieldsValue({ platforms: connected, scheduledAt: dayjs().add(1, 'day') });
            }
        });
        return () => {
            clearAuthPolling();
            clearPublishPolling();
        };
    }, [form]);

    const handleConnect = async (platform: string) => {
        setCurrentPlatform(platform);
        setAuthModalVisible(true);
        setQrCode(null);
        
        // First, do a fresh auth check before starting the login flow.
        // This avoids showing a QR code when the platform is already connected.
        try {
            const freshStatus = await getSocialStatus();
            if (freshStatus.success && freshStatus.data) {
                const isConnected = freshStatus.data[platform as keyof typeof freshStatus.data];
                if (isConnected) {
                    message.success(`${platform} is already connected!`);
                    setAuthModalVisible(false);
                    // Ensure the checkbox reflects the connected state
                    checkStatus().then(data => {
                        if (data) {
                            const current = form.getFieldValue('platforms') || [];
                            if (!current.includes(platform)) {
                                form.setFieldsValue({ platforms: [...current, platform] });
                            }
                        }
                    });
                    return;
                }
            }
        } catch (e) {
            console.warn("Fresh status check failed, proceeding with login flow:", e);
        }

        try {
            const res = await startSocialLogin(platform);
            if (res.success) {
                if (res.data?.already_logged_in) {
                    message.success(`Already logged into ${platform}! Connected successfully.`);
                    setAuthModalVisible(false);
                    checkStatus().then(data => {
                        if (data) {
                            const current = form.getFieldValue('platforms') || [];
                            if (!current.includes(platform)) {
                                form.setFieldsValue({ platforms: [...current, platform] });
                            }
                        }
                    });
                    return;
                }

                if (res.data?.qr_base64) {
                    setQrCode(res.data.qr_base64);
                    // Start polling
                    clearAuthPolling();
                    authPollingRef.current = setInterval(async () => {
                        try {
                            const pollRes = await pollSocialLogin(platform);
                            if (pollRes.success && pollRes.data?.status) {
                                if (pollRes.data.status === 'success') {
                                    clearAuthPolling();
                                    setAuthModalVisible(false);
                                    
                                    setTimeout(async () => {
                                        try {
                                            const fresh = await getSocialStatus();
                                            if (fresh.success && fresh.data && fresh.data[platform as keyof typeof fresh.data]) {
                                                message.success(`Logged into ${platform} successfully!`);
                                                // Refresh full status to update UI checkboxes
                                                await checkStatus();
                                                const current = form.getFieldValue('platforms') || [];
                                                if (!current.includes(platform)) {
                                                    form.setFieldsValue({ platforms: [...current, platform] });
                                                }
                                            } else {                                                // Backend said "success" but status check disagrees — stale race condition
                                                console.warn(`Backend said ${platform} succeeded but status check disagreed`);
                                                message.warning(`${platform} login detected but confirmation failed. Refreshing...`);
                                                // Auto-close warning after user sees it
                                                setTimeout(() => setAuthModalVisible(false), 5000);
                                                // Optionally re-check once more after brief delay
                                                setTimeout(async () => {
                                                    const recheck = await getSocialStatus();
                                                    if (recheck?.success && recheck.data?.[platform as keyof typeof recheck.data]) {
                                                        message.success(`${platform} finally confirmed!`);
                                                        const current = form.getFieldValue('platforms') || [];
                                                        if (!current.includes(platform)) {
                                                            form.setFieldsValue({ platforms: [...current, platform] });
                                                        }
                                                    }
                                                }, 2000);
                                            }
                                        } catch (verifyErr) {
                                            console.error("Status verification error:", verifyErr);
                                            message.error(`${platform}: Login process completed but status could not be verified`);
                                            // Don't auto-reopen - let user manually click "Reconnect" if needed
                                        }
                                    }, 300);  // Short delay to allow UI update before verification
                                } else if (pollRes.data.status === 'failed') {
                                    message.error(`Login to ${platform} failed or timed out.`);
                                    clearAuthPolling();
                                    setAuthModalVisible(false);
                                }
                            }
                        } catch (e) {
                            clearAuthPolling();
                            setAuthModalVisible(false);
                            message.error(`Login status polling stopped for ${platform}.`);
                        }
                    }, 2000);
                } else {
                    message.error("Failed to start login session: No QR code returned");
                    setAuthModalVisible(false);
                }
            } else {
                message.error(res.message || "Failed to start login session");
                setAuthModalVisible(false);
            }
        } catch (e) {
            message.error("Failed to connect");
            setAuthModalVisible(false);
        }
    };

    const handleCancelAuth = () => {
        clearAuthPolling();
        setAuthModalVisible(false);
    };

    const startPublishPolling = (newTaskId: string, completionMessage: string) => {
        setTaskId(newTaskId);
        clearPublishPolling();
        publishPollingRef.current = setInterval(async () => {
            try {
                const pollRes = await pollPublishStatus(newTaskId);
                if (pollRes.success && pollRes.data) {
                    setPublishResults(pollRes.data.results || {});
                    setCurrentPublishingPlatform(pollRes.data.current_platform || null);

                    if (pollRes.data.status === "completed") {
                        clearPublishPolling();
                        setPublishing(false);
                        setDebugging(false);
                        setCurrentPublishingPlatform(null);
                        message.success(completionMessage);
                    } else if (pollRes.data.status === "stopped") {
                        clearPublishPolling();
                        setPublishing(false);
                        setDebugging(false);
                        setCurrentPublishingPlatform(null);
                        message.info("Task stopped.");
                    }
                }
            } catch (e) {
                clearPublishPolling();
                setPublishing(false);
                setDebugging(false);
                setTaskId(null);
                setCurrentPublishingPlatform(null);
                message.warning("Status polling stopped because the backend no longer has this task.");
            }
        }, 3000);
    };

    const onFinish = async (values: any) => {
        if (!values.platforms || values.platforms.length === 0) {
            message.warning("Select at least one platform");
            return;
        }
        if (fileList.length === 0) {
            message.warning("Please upload a video file");
            return;
        }
        if (!values.scheduledAt) {
            message.warning("Please choose a schedule time");
            return;
        }

        setPublishing(true);
        setDebugging(false);
        setPublishResults({});
        setCurrentPublishingPlatform(null);
        clearPublishPolling();
        try {
            const file = fileList[0].originFileObj;
            const scheduledAt = values.scheduledAt.toISOString();
            const res = await publishSocialVideo(
                values.platforms.join(','),
                values.title,
                values.description,
                scheduledAt,
                file
            );

            if (res.success && res.data?.task_id) {
                const newTaskId = res.data.task_id;
                message.success("Submitting scheduled publish on each platform.");
                startPublishPolling(newTaskId, "Publish task completed!");
            } else {
                message.error(res.message || "Failed to start publish task");
                setPublishing(false);
            }
        } catch(e) {
            message.error("Publish request failed");
            setPublishing(false);
        }
    };

    const handleDebugUpload = async () => {
        try {
            const values = await form.validateFields();
            if (!values.platforms || values.platforms.length === 0) {
                message.warning("Select at least one platform");
                return;
            }
            if (fileList.length === 0) {
                message.warning("Please upload a video file");
                return;
            }
            if (!values.scheduledAt) {
                message.warning("Please choose a schedule time");
                return;
            }

            setDebugging(true);
            setPublishing(false);
            setPublishResults({});
            setCurrentPublishingPlatform(null);
            clearPublishPolling();

            const file = fileList[0].originFileObj;
            const scheduledAt = values.scheduledAt.toISOString();
            const res = await debugUploadSocialVideo(
                values.platforms.join(','),
                values.title,
                values.description,
                scheduledAt,
                file
            );

            if (res.success && res.data?.task_id) {
                message.success("Debug upload started. The publish button will not be clicked.");
                startPublishPolling(res.data.task_id, "Debug upload completed. Review the screenshots before publishing.");
            } else {
                message.error(res.message || "Failed to start debug upload");
                setDebugging(false);
            }
        } catch (e) {
            setDebugging(false);
            message.error("Debug upload request failed");
        }
    };

    const handleStopTask = async () => {
        if (!taskId) return;
        try {
            await stopPublishTask(taskId);
            message.info("Stop requested. Waiting for current attempts to cancel...");
        } catch (e) {
            message.error("Failed to stop task");
        }
    };

    const handleSkipCurrentPlatform = async () => {
        if (!taskId) return;
        try {
            const res = await skipCurrentPublishPlatform(taskId);
            if (res.success) {
                message.info(res.message || "Skip requested. Moving to the next platform...");
            } else {
                message.warning(res.message || "No platform is currently publishing.");
            }
        } catch (e) {
            message.error("Failed to skip current platform");
        }
    };

    const platforms = [
        { id: 'douyin', name: 'Douyin / 抖音', color: '#000000', url: 'https://creator.douyin.com/creator-micro/content/upload' },
        { id: 'xiaohongshu', name: 'Xiaohongshu / 小红书', color: '#ff2442', url: 'https://creator.xiaohongshu.com/publish/publish?source=official' },
        { id: 'channels', name: 'WeChat Channels / 视频号', color: '#1aad19', url: 'https://channels.weixin.qq.com/platform/post/create' },
    ];

    const getScreenshotSrc = (url?: string | null) => {
        if (!url) return null;
        return url.startsWith('http') ? url : `${API_BASE_URL}${url}`;
    };

    return (
        <div style={{ padding: 24, background: '#f5f5f5', borderRadius: 8, minHeight: 600 }}>
            <Title level={3}>Social Publisher Hub</Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
                Connect your social accounts via QR Code and submit videos through each platform's scheduled publish flow.
            </Text>
            
            {/* How publishing works */}
            <div style={{
                background: 'linear-gradient(135deg, #0d1b2a 0%, #1b263b 100%)',
                border: '1px solid #415a77',
                borderRadius: 8,
                padding: '14px 18px',
                marginBottom: 24,
                color: '#e0e1dd',
                fontSize: 13,
                display: 'flex',
                gap: 12,
                alignItems: 'flex-start',
            }}>
                <CloudUploadOutlined style={{ fontSize: 20, flexShrink: 0, color: '#e9c46a', marginTop: 2 }} />
                <div>
                    <div style={{ fontWeight: 700, color: '#778da9', marginBottom: 4 }}>How publishing works — zero setup required</div>
                    <div style={{ color: '#a9b4c2', lineHeight: 1.7 }}>
                        Schedule Publish opens the creator portal now, selects the platform's own 定时发布 option,
                        fills the chosen publish time, and submits the post to that platform.
                    </div>
                </div>
            </div>

            <Row gutter={[16, 16]} style={{ marginBottom: 32 }}>
                {platforms.map(p => {
                    const isConnected = status[p.id as keyof typeof status];
                    return (
                        <Col span={8} key={p.id}>
                            <Card 
                                bordered={false} 
                                style={{ borderLeft: `4px solid ${p.color}`, boxShadow: '0 2px 8px rgba(0,0,0,0.05)' }}
                            >
                                <Space direction="vertical" style={{ width: '100%' }}>
                                    <Title level={5} style={{ margin: 0, color: p.color }}>{p.name}</Title>
                                    <div>
                                        {isConnected ? (
                                            <Space><CheckCircleFilled style={{ color: '#52c41a' }} /> <Text strong>Connected</Text></Space>
                                        ) : (
                                            <Space><CloseCircleFilled style={{ color: '#ff4d4f' }} /> <Text type="secondary">Not Connected</Text></Space>
                                        )}
                                    </div>
                                    
                                    {/* Publish Status Block */}
                                    {publishResults[p.id] && (
                                        <div style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', border: '1px solid #f0f0f0', marginTop: 8 }}>
                                            <Text strong style={{ fontSize: 12, display: 'block' }}>Task Status:</Text>
                                            <Space align="start">
                                                {publishResults[p.id].status === 'success' && <CheckCircleFilled style={{ color: '#52c41a', marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'ready' && <CheckCircleFilled style={{ color: '#52c41a', marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'publishing' && <Spin size="small" style={{ marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'debugging' && <Spin size="small" style={{ marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'waiting_retry' && <Spin size="small" style={{ marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'stopped' && <CloseCircleFilled style={{ color: '#ff4d4f', marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'failed' && <CloseCircleFilled style={{ color: '#ff4d4f', marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'skipped' && <CloseCircleFilled style={{ color: '#faad14', marginTop: 4 }} />}
                                                {publishResults[p.id].status === 'pending' && <CloudUploadOutlined style={{ marginTop: 4 }} />}
                                                
                                                <div style={{ flex: 1, fontSize: 13 }}>
                                                    <div style={{ fontWeight: 500 }}>
                                                        {publishResults[p.id].status.toUpperCase()}
                                                        {publishResults[p.id].retries > 0 && ` (Retry #${publishResults[p.id].retries})`}
                                                    </div>
                                                    {publishResults[p.id].message && (
                                                        <Text type="danger" style={{ fontSize: 11, display: 'block', lineHeight: 1.2, marginTop: 4 }}>
                                                            {publishResults[p.id].message}
                                                        </Text>
                                                    )}
                                                    {getScreenshotSrc(publishResults[p.id].screenshot_url) && (
                                                        <div style={{ marginTop: 8 }}>
                                                            <Image
                                                                src={getScreenshotSrc(publishResults[p.id].screenshot_url)!}
                                                                alt={`${p.name} publish screenshot`}
                                                                width="100%"
                                                                style={{ borderRadius: 6, border: '1px solid #f0f0f0', maxHeight: 520, objectFit: 'contain', background: '#fff' }}
                                                            />
                                                        </div>
                                                    )}
                                                </div>
                                            </Space>
                                        </div>
                                    )}
                                    <Button 
                                        type={isConnected ? "default" : "primary"} 
                                        block 
                                        onClick={() => handleConnect(p.id)}
                                        style={!isConnected ? { background: p.color, borderColor: p.color } : {}}
                                    >
                                        {isConnected ? "Reconnect Session" : "Connect"}
                                    </Button>
                                    <Button type="link" block href={p.url} target="_blank">
                                        Open Creator Portal (Manual)
                                    </Button>
                                </Space>
                            </Card>
                        </Col>
                    )
                })}
            </Row>

            <Card title="New Post" bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,0.05)' }}>
                    <Form
                    form={form}
                    layout="vertical"
                    onFinish={onFinish}
                    initialValues={{ scheduledAt: dayjs().add(1, 'day') }}
                >
                    <Form.Item label="Upload Video" required>
                        <Dragger
                            fileList={fileList}
                            onChange={(info) => {
                                let newFileList = [...info.fileList];
                                newFileList = newFileList.slice(-1);
                                setFileList(newFileList);
                            }}
                            beforeUpload={() => false}
                            accept="video/mp4,video/quicktime"
                            maxCount={1}
                        >
                            <p className="ant-upload-drag-icon">
                                <InboxOutlined />
                            </p>
                            <p className="ant-upload-text">Click or drag MP4 video here</p>
                            <p className="ant-upload-hint">Upload a local video or pick one generated from the Studio tab.</p>
                        </Dragger>
                    </Form.Item>

                    <Form.Item 
                        name="title" 
                        label="Post Title" 
                        rules={[{ required: true, message: 'Please enter a title' }]}
                    >
                        <Input size="large" placeholder="Enter an engaging title..." />
                    </Form.Item>

                    <Form.Item 
                        name="description" 
                        label="Description & Tags" 
                        rules={[{ required: true, message: 'Please enter a description' }]}
                    >
                        <Input.TextArea rows={4} placeholder="Write your post description and #tags... (#English #Kids)" />
                    </Form.Item>

                    <Form.Item 
                        name="platforms" 
                        label="Target Platforms"
                        rules={[{ required: true, message: 'Please select platforms' }]}
                    >
                        <Checkbox.Group>
                            <Space size="large">
                                <Checkbox value="douyin" disabled={!status.douyin}>Douyin</Checkbox>
                                <Checkbox value="xiaohongshu" disabled={!status.xiaohongshu}>Xiaohongshu</Checkbox>
                                <Checkbox value="channels" disabled={!status.channels}>WeChat Channels</Checkbox>
                            </Space>
                        </Checkbox.Group>
                    </Form.Item>

                    <Form.Item
                        name="scheduledAt"
                        label="Schedule Time"
                        rules={[{ required: true, message: 'Please choose a schedule time' }]}
                        tooltip="Defaults to the same time tomorrow. Used to fill each platform's 定时发布 field."
                    >
                        <DatePicker
                            showTime
                            size="large"
                            style={{ width: 280 }}
                            disabledDate={(current) => !!current && current < dayjs().startOf('day')}
                        />
                    </Form.Item>

                    <Form.Item>
                        <Space>
                            <Button 
                                type="primary" 
                                htmlType="submit" 
                                size="large" 
                                icon={<CloudUploadOutlined />} 
                                loading={publishing}
                                disabled={debugging}
                                style={{ width: 180 }}
                            >
                                Schedule Publish
                            </Button>
                            <Button
                                size="large"
                                onClick={handleDebugUpload}
                                loading={debugging}
                                disabled={publishing}
                                style={{ width: 160 }}
                            >
                                Debug Upload
                            </Button>
                            
                            {taskId && (publishing || debugging) && (
                                <>
                                    {publishing && (
                                        <Button size="large" onClick={handleSkipCurrentPlatform} style={{ marginLeft: 16 }}>
                                            Skip Current Platform{currentPublishingPlatform ? ` (${currentPublishingPlatform})` : ''}
                                        </Button>
                                    )}
                                    <Button size="large" danger onClick={handleStopTask}>
                                        Stop Task
                                    </Button>
                                </>
                            )}
                        </Space>
                    </Form.Item>
                </Form>
            </Card>

            <Modal
                title={`Scan to Login (${currentPlatform})`}
                open={authModalVisible}
                onCancel={handleCancelAuth}
                footer={null}
                width={900}
                centered
            >
                <div style={{ textAlign: 'center', padding: '10px 0' }}>
                    {!qrCode ? (
                        <Space direction="vertical">
                            <Spin size="large" />
                            <Text>Loading Browser Session & Capturing Screen...</Text>
                            <Text type="secondary">This may take 10-20 seconds as it bypasses anti-bot checks.</Text>
                        </Space>
                    ) : (
                        <Space direction="vertical" style={{ width: '100%' }}>
                            <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', padding: '10px', borderRadius: '4px', marginBottom: '10px' }}>
                                <Text strong style={{ color: '#d46b08', fontSize: '16px' }}>This is a screenshot of the login page</Text><br/>
                                <Text>Do <Text strong>NOT</Text> try to click buttons on the image. You must open the {currentPlatform} App on your mobile phone and <b>SCAN THE QR CODE</b> shown in the image below to log in.</Text>
                            </div>
                            <Image src={qrCode} style={{ maxWidth: '100%', maxHeight: '50vh', objectFit: 'contain', border: '2px solid #ddd' }} preview={true} />
                            <Text type="secondary" style={{ fontSize: 14, marginTop: 10 }}>Waiting for your phone confirmation... (Auto-closes on success)</Text>
                        </Space>
                    )}
                </div>
            </Modal>
        </div>
    );
};

export default SocialPublisher;
