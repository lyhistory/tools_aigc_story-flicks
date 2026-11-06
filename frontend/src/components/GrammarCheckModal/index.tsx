import React, { useEffect, useState } from 'react';
import { Alert, Button, Modal, Select, Space, Tag, Typography } from 'antd';
import { grammarIssueKey } from '../../utils/grammar';

const { Text } = Typography;

interface GrammarCheckModalProps {
    open: boolean;
    issues: GrammarIssue[];
    labels: string[];
    onClose: () => void;
    onApplySuggestions: (choices: Record<string, string>) => void;
}

const GrammarCheckModal: React.FC<GrammarCheckModalProps> = ({
    open,
    issues,
    labels,
    onClose,
    onApplySuggestions,
}) => {
    const [choices, setChoices] = useState<Record<string, string>>({});

    useEffect(() => {
        if (open) setChoices({});
    }, [open]);

    const canApply = issues.some((issue) => (
        issue.replacements.length === 1 || Boolean(choices[grammarIssueKey(issue)])
    ));

    return (
    <Modal
        open={open}
        title="Fix grammar and spelling before generating"
        width={720}
        onCancel={onClose}
        footer={[
            <Button key="manual" onClick={onClose}>I will fix manually</Button>,
            <Button
                key="apply"
                type="primary"
                onClick={() => onApplySuggestions(choices)}
                disabled={!canApply}
            >
                Apply suggestions
            </Button>,
        ]}
    >
        <Alert
            type="warning"
            showIcon
            message={`Found ${issues.length} grammar/spelling issue${issues.length === 1 ? '' : 's'}. Fix them, then click Generate again.`}
            description="When several corrections are offered, choose the right one below or fix the line manually."
            style={{ marginBottom: 12 }}
        />
        <div style={{ maxHeight: 420, overflowY: 'auto', paddingRight: 4 }}>
            {issues.map((issue, index) => (
                <div
                    key={`${issue.text_index}-${issue.offset}-${issue.length}-${index}`}
                    style={{
                        padding: '10px 12px',
                        border: '1px solid #F0F0F0',
                        borderRadius: 8,
                        marginBottom: 8,
                        background: '#FAFAFA',
                    }}
                >
                    <Space wrap size={6} style={{ marginBottom: 4 }}>
                        <Tag color="blue">{labels[issue.text_index] || `Text ${issue.text_index + 1}`}</Tag>
                        <Tag>{issue.short_message || issue.category || 'Suggestion'}</Tag>
                    </Space>
                    <div style={{ fontSize: 13, marginBottom: 2 }}>
                        <Text delete>{issue.original || '(empty)'}</Text>
                        {issue.replacements.length === 1 && (
                            <>
                                <Text type="secondary"> {'->'} </Text>
                                <Text strong>{issue.replacements[0]}</Text>
                            </>
                        )}
                        {issue.replacements.length > 1 && (
                            <>
                                <Text type="secondary"> {'->'} </Text>
                                <Select
                                    size="small"
                                    style={{ minWidth: 180 }}
                                    placeholder="Choose a correction"
                                    value={choices[grammarIssueKey(issue)]}
                                    onChange={(value) => setChoices((previous) => ({
                                        ...previous,
                                        [grammarIssueKey(issue)]: value,
                                    }))}
                                    options={issue.replacements.map((replacement) => ({
                                        label: replacement,
                                        value: replacement,
                                    }))}
                                />
                            </>
                        )}
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{issue.message}</Text>
                </div>
            ))}
        </div>
    </Modal>
    );
};

export default GrammarCheckModal;
