import { Affix, Badge, Button, Space } from 'antd';
import { CloseOutlined, SelectOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';

export interface BatchAction {
  label: string;
  icon?: ReactNode;
  onClick: () => void;
  danger?: boolean;
  loading?: boolean;
}

export interface BatchOperationBarProps {
  selectedCount: number;
  onSelectAll?: () => void;
  onClear: () => void;
  actions: BatchAction[];
  /** Affix offset from bottom in px */
  offsetBottom?: number;
}

/**
 * 浮动批量操作工具条
 *
 * 当 selectedCount > 0 时显示在页面底部（Affix 固定），
 * 展示已选数量并提供全选、取消、以及自定义操作按钮。
 */
export default function BatchOperationBar({
  selectedCount,
  onSelectAll,
  onClear,
  actions,
  offsetBottom = 0,
}: BatchOperationBarProps) {
  if (selectedCount === 0) return null;

  return (
    <Affix offsetBottom={offsetBottom}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 24px',
          background: '#fff',
          boxShadow: '0 -2px 8px rgba(0,0,0,0.12)',
          borderTop: '1px solid #f0f0f0',
        }}
      >
        <Space size="middle">
          <Badge
            count={selectedCount}
            style={{ backgroundColor: '#1677ff' }}
            overflowCount={9999}
          />
          <span style={{ fontWeight: 500 }}>已选 {selectedCount} 项</span>
        </Space>
        <Space>
          {onSelectAll && (
            <Button icon={<SelectOutlined />} onClick={onSelectAll}>
              全选
            </Button>
          )}
          <Button icon={<CloseOutlined />} onClick={onClear}>
            取消
          </Button>
          {actions.map((action) => (
            <Button
              key={action.label}
              icon={action.icon}
              danger={action.danger}
              loading={action.loading}
              onClick={action.onClick}
            >
              {action.label}
            </Button>
          ))}
        </Space>
      </div>
    </Affix>
  );
}
