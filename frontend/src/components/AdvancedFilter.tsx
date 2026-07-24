import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Collapse,
  DatePicker,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Tag,
  type RadioChangeEvent,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  SaveOutlined,
  FilterOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';

export type FilterOperator =
  | 'eq'
  | 'neq'
  | 'gt'
  | 'lt'
  | 'gte'
  | 'lte'
  | 'contains'
  | 'between';

export interface FilterFieldOption {
  label: string;
  value: string;
}

export interface FilterField {
  key: string;
  label: string;
  type: 'select' | 'dateRange' | 'number' | 'text' | 'multiSelect';
  options?: FilterFieldOption[];
  placeholder?: string;
}

export interface FilterCondition {
  field: string;
  operator: FilterOperator;
  value: unknown;
}

export type FilterLogic = 'AND' | 'OR';

export interface FilterPreset {
  name: string;
  conditions: FilterCondition[];
  logic: FilterLogic;
}

export interface AdvancedFilterProps {
  fields: FilterField[];
  pageName: string;
  onFilter: (conditions: FilterCondition[], logic: FilterLogic) => void;
  onReset: () => void;
  presets?: FilterPreset[];
  onSavePreset?: (preset: FilterPreset) => void;
}

const OPERATOR_LABELS: Record<FilterOperator, string> = {
  eq: '等于',
  neq: '不等于',
  gt: '大于',
  lt: '小于',
  gte: '大于等于',
  lte: '小于等于',
  contains: '包含',
  between: '区间',
};

function operatorsForType(type: FilterField['type']): FilterOperator[] {
  switch (type) {
    case 'select':
      return ['eq', 'neq'];
    case 'multiSelect':
      return ['contains', 'eq'];
    case 'dateRange':
      return ['between', 'eq'];
    case 'number':
      return ['eq', 'neq', 'gt', 'lt', 'gte', 'lte', 'between'];
    case 'text':
    default:
      return ['eq', 'neq', 'contains'];
  }
}

function defaultOperator(type: FilterField['type']): FilterOperator {
  switch (type) {
    case 'dateRange':
      return 'between';
    case 'text':
      return 'contains';
    case 'multiSelect':
      return 'contains';
    default:
      return 'eq';
  }
}

function isDayjsArray(value: unknown): value is [Dayjs, Dayjs] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    (value[0] === null || dayjs.isDayjs(value[0])) &&
    (value[1] === null || dayjs.isDayjs(value[1]))
  );
}

function serializeConditions(conditions: FilterCondition[]): FilterCondition[] {
  return conditions.map((c) => {
    if (isDayjsArray(c.value)) {
      return {
        ...c,
        value: [c.value[0]?.toISOString() ?? null, c.value[1]?.toISOString() ?? null],
      };
    }
    if (dayjs.isDayjs(c.value)) {
      return { ...c, value: c.value.toISOString() };
    }
    return c;
  });
}

function deserializeConditions(conditions: FilterCondition[]): FilterCondition[] {
  return conditions.map((c) => {
    if (
      Array.isArray(c.value) &&
      c.value.length === 2 &&
      (typeof c.value[0] === 'string' || c.value[0] === null) &&
      (typeof c.value[1] === 'string' || c.value[1] === null)
    ) {
      return {
        ...c,
        value: [
          c.value[0] ? dayjs(c.value[0]) : null,
          c.value[1] ? dayjs(c.value[1]) : null,
        ] as unknown,
      };
    }
    if (typeof c.value === 'string' && /\d{4}-\d{2}-\d{2}T/.test(c.value)) {
      return { ...c, value: dayjs(c.value) };
    }
    return c;
  });
}

function storageKey(pageName: string): string {
  return `advanced_filter_presets_${pageName}`;
}

function loadPresets(pageName: string): FilterPreset[] {
  try {
    const raw = localStorage.getItem(storageKey(pageName));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as FilterPreset[];
    return parsed.map((p) => ({
      ...p,
      conditions: deserializeConditions(p.conditions),
    }));
  } catch {
    return [];
  }
}

function savePresets(pageName: string, presets: FilterPreset[]) {
  try {
    const serializable = presets.map((p) => ({
      ...p,
      conditions: serializeConditions(p.conditions),
    }));
    localStorage.setItem(storageKey(pageName), JSON.stringify(serializable));
  } catch {
    // ignore storage errors
  }
}

export function evaluateCondition(
  record: Record<string, unknown>,
  condition: FilterCondition,
): boolean {
  const { field, operator, value } = condition;
  const raw = record[field];

  if (operator === 'between') {
    if (Array.isArray(value) && value.length === 2) {
      if (field.toLowerCase().includes('at') || field.toLowerCase().includes('time') || field.toLowerCase().includes('date')) {
        const start = value[0] ? dayjs(value[0] as string | number | Date) : null;
        const end = value[1] ? dayjs(value[1] as string | number | Date) : null;
        const t = raw ? dayjs(raw as string | number | Date) : null;
        if (!t || !t.isValid()) return false;
        if (start && start.isValid() && t.isBefore(start)) return false;
        if (end && end.isValid() && t.isAfter(end.endOf('day'))) return false;
        return true;
      }
      const left = Number(value[0]);
      const right = Number(value[1]);
      const num = Number(raw);
      if (Number.isNaN(num) || Number.isNaN(left) || Number.isNaN(right)) return false;
      return num >= left && num <= right;
    }
    return false;
  }

  if (operator === 'contains') {
    if (Array.isArray(raw)) {
      const targets = Array.isArray(value) ? value : [value];
      return targets.every((v) => raw.includes(v));
    }
    if (typeof raw === 'string') {
      return raw.toLowerCase().includes(String(value ?? '').toLowerCase());
    }
    if (raw == null) return false;
    return String(raw).toLowerCase().includes(String(value ?? '').toLowerCase());
  }

  // For date fields with non-between operators, compare at day precision.
  if (
    field.toLowerCase().includes('at') ||
    field.toLowerCase().includes('time') ||
    field.toLowerCase().includes('date')
  ) {
    const t = raw ? dayjs(raw as string | number | Date) : null;
    const v = value ? dayjs(value as string | number | Date) : null;
    if (!t || !t.isValid() || !v || !v.isValid()) return false;
    const a = t.startOf('day').valueOf();
    const b = v.startOf('day').valueOf();
    switch (operator) {
      case 'eq':
        return a === b;
      case 'neq':
        return a !== b;
      case 'gt':
        return a > b;
      case 'lt':
        return a < b;
      case 'gte':
        return a >= b;
      case 'lte':
        return a <= b;
      default:
        return false;
    }
  }

  const recordValue = typeof raw === 'number' ? raw : String(raw ?? '').toLowerCase();
  const compareValue = typeof raw === 'number' ? Number(value) : String(value ?? '').toLowerCase();

  switch (operator) {
    case 'eq':
      return recordValue === compareValue;
    case 'neq':
      return recordValue !== compareValue;
    case 'gt':
      return Number(recordValue) > Number(compareValue);
    case 'lt':
      return Number(recordValue) < Number(compareValue);
    case 'gte':
      return Number(recordValue) >= Number(compareValue);
    case 'lte':
      return Number(recordValue) <= Number(compareValue);
    default:
      return false;
  }
}

export function filterRecords<T extends Record<string, unknown>>(
  records: T[],
  conditions: FilterCondition[],
  logic: FilterLogic,
): T[] {
  if (!conditions.length) return records;
  return records.filter((record) => {
    const results = conditions.map((c) => evaluateCondition(record, c));
    return logic === 'AND' ? results.every(Boolean) : results.some(Boolean);
  });
}

export default function AdvancedFilter({
  fields,
  pageName,
  onFilter,
  onReset,
  presets: initialPresets,
  onSavePreset,
}: AdvancedFilterProps) {
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [logic, setLogic] = useState<FilterLogic>('AND');
  const [presets, setPresets] = useState<FilterPreset[]>(() => [
    ...(initialPresets ?? []),
    ...loadPresets(pageName),
  ]);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [presetName, setPresetName] = useState('');

  useEffect(() => {
    setPresets([
      ...(initialPresets ?? []),
      ...loadPresets(pageName),
    ]);
  }, [initialPresets, pageName]);

  const activeCount = useMemo(
    () =>
      conditions.filter((c) => {
        if (!c.field) return false;
        if (c.value === undefined || c.value === null || c.value === '') return false;
        if (Array.isArray(c.value) && c.value.length === 0) return false;
        if (Array.isArray(c.value) && c.value.every((v) => v === null || v === undefined))
          return false;
        return true;
      }).length,
    [conditions],
  );

  const addCondition = () => {
    const first = fields[0];
    setConditions((prev) => [
      ...prev,
      {
        field: first?.key ?? '',
        operator: first ? defaultOperator(first.type) : 'eq',
        value: first?.type === 'multiSelect' ? [] : null,
      },
    ]);
    setSelectedPreset(null);
  };

  const removeCondition = (index: number) => {
    setConditions((prev) => prev.filter((_, i) => i !== index));
    setSelectedPreset(null);
  };

  const updateCondition = (index: number, patch: Partial<FilterCondition>) => {
    setConditions((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
    setSelectedPreset(null);
  };

  const handleFieldChange = (index: number, fieldKey: string) => {
    const field = fields.find((f) => f.key === fieldKey);
    if (!field) return;
    updateCondition(index, {
      field: fieldKey,
      operator: defaultOperator(field.type),
      value: field.type === 'multiSelect' ? [] : null,
    });
  };

  const handleOperatorChange = (index: number, operator: FilterOperator) => {
    const condition = conditions[index];
    let value: unknown = condition.value;
    if (operator === 'between' && !Array.isArray(value)) {
      value = [null, null];
    } else if (operator !== 'between' && Array.isArray(value)) {
      value = null;
    }
    updateCondition(index, { operator, value });
  };

  const handleApply = () => {
    const activeConditions = conditions.filter((c) => {
      if (!c.field) return false;
      if (c.value === undefined || c.value === null || c.value === '') return false;
      if (Array.isArray(c.value) && c.value.length === 0) return false;
      if (Array.isArray(c.value) && c.value.every((v) => v === null || v === undefined))
        return false;
      return true;
    });
    const serializable = serializeConditions(activeConditions);
    onFilter(serializable, logic);
  };

  const handleReset = () => {
    setConditions([]);
    setLogic('AND');
    setSelectedPreset(null);
    onReset();
  };

  const handleSelectPreset = (name: string | null) => {
    if (!name) {
      setSelectedPreset(null);
      return;
    }
    const preset = presets.find((p) => p.name === name);
    if (!preset) return;
    setConditions(preset.conditions);
    setLogic(preset.logic);
    setSelectedPreset(name);
    onFilter(serializeConditions(preset.conditions), preset.logic);
  };

  const handleSavePreset = () => {
    const trimmed = presetName.trim();
    if (!trimmed) return;
    const newPreset: FilterPreset = {
      name: trimmed,
      conditions: [...conditions],
      logic,
    };
    setPresets((prev) => {
      const filtered = prev.filter((p) => p.name !== trimmed);
      const next = [...filtered, newPreset];
      savePresets(pageName, next.filter((p) => !initialPresets?.some((ip) => ip.name === p.name)));
      return next;
    });
    onSavePreset?.(newPreset);
    setSelectedPreset(trimmed);
    setSaveModalOpen(false);
    setPresetName('');
  };

  const renderValueInput = (condition: FilterCondition, index: number) => {
    const field = fields.find((f) => f.key === condition.field);
    if (!field) return null;

    switch (field.type) {
      case 'select':
        return (
          <Select
            placeholder={field.placeholder || '请选择'}
            options={field.options}
            value={condition.value as string | undefined}
            onChange={(value) => updateCondition(index, { value })}
            style={{ minWidth: 160 }}
            allowClear
          />
        );
      case 'multiSelect':
        return (
          <Select
            mode="multiple"
            placeholder={field.placeholder || '请选择'}
            options={field.options}
            value={(condition.value as string[]) ?? []}
            onChange={(value) => updateCondition(index, { value })}
            style={{ minWidth: 200 }}
            allowClear
          />
        );
      case 'dateRange':
        return (
          <DatePicker.RangePicker
            value={condition.value as [Dayjs | null, Dayjs | null] | null}
            onChange={(dates) =>
              updateCondition(index, { value: dates, operator: 'between' })
            }
            style={{ minWidth: 240 }}
          />
        );
      case 'number':
        if (condition.operator === 'between') {
          const [start, end] = (condition.value as [number | null, number | null] | null) ?? [
            null,
            null,
          ];
          return (
            <Space>
              <InputNumber
                placeholder="最小值"
                value={start ?? undefined}
                onChange={(value) =>
                  updateCondition(index, {
                    value: [value === null ? null : value, end],
                  })
                }
              />
              <span>~</span>
              <InputNumber
                placeholder="最大值"
                value={end ?? undefined}
                onChange={(value) =>
                  updateCondition(index, {
                    value: [start, value === null ? null : value],
                  })
                }
              />
            </Space>
          );
        }
        return (
          <InputNumber
            placeholder={field.placeholder}
            value={(condition.value as number | undefined) ?? undefined}
            onChange={(value) => updateCondition(index, { value })}
            style={{ minWidth: 160 }}
          />
        );
      case 'text':
      default:
        return (
          <Input
            placeholder={field.placeholder}
            value={(condition.value as string | undefined) ?? ''}
            onChange={(e) => updateCondition(index, { value: e.target.value })}
            style={{ minWidth: 200 }}
          />
        );
    }
  };

  return (
    <>
    <Collapse
      size="small"
      style={{ marginBottom: 16 }}
      items={[
        {
          key: 'advanced-filter',
          label: (
            <Space>
              <FilterOutlined />
              <span>高级过滤</span>
              {activeCount > 0 && <Tag color="blue">{activeCount} 个条件</Tag>}
            </Space>
          ),
          children: (
            <div>
              <div style={{ marginBottom: 12 }}>
                <Radio.Group
                  value={logic}
                  onChange={(e: RadioChangeEvent) => setLogic(e.target.value as FilterLogic)}
                  optionType="button"
                  buttonStyle="solid"
                  options={[
                    { label: '全部满足 (AND)', value: 'AND' },
                    { label: '任一满足 (OR)', value: 'OR' },
                  ]}
                />
              </div>

              {conditions.map((condition, index) => {
                const field = fields.find((f) => f.key === condition.field);
                const operators = field ? operatorsForType(field.type) : [];
                return (
                  <Space
                    key={`${condition.field}-${index}`}
                    style={{ display: 'flex', marginBottom: 12, flexWrap: 'wrap' }}
                    align="baseline"
                  >
                    <Select
                      placeholder="字段"
                      value={condition.field}
                      onChange={(value) => handleFieldChange(index, value)}
                      options={fields.map((f) => ({ value: f.key, label: f.label }))}
                      style={{ minWidth: 140 }}
                    />
                    <Select
                      placeholder="操作符"
                      value={condition.operator}
                      onChange={(value) => handleOperatorChange(index, value as FilterOperator)}
                      options={operators.map((op) => ({
                        value: op,
                        label: OPERATOR_LABELS[op],
                      }))}
                      style={{ minWidth: 120 }}
                    />
                    {renderValueInput(condition, index)}
                    <Button
                      icon={<DeleteOutlined />}
                      danger
                      size="small"
                      onClick={() => removeCondition(index)}
                    />
                  </Space>
                );
              })}

              <Space wrap style={{ marginTop: 12 }}>
                <Button icon={<PlusOutlined />} onClick={addCondition}>
                  添加条件
                </Button>
                <Button type="primary" onClick={handleApply}>
                  应用过滤
                </Button>
                <Button onClick={handleReset}>重置</Button>

                <Select
                  placeholder="选择预设方案"
                  value={selectedPreset}
                  onChange={handleSelectPreset}
                  allowClear
                  style={{ minWidth: 160 }}
                  options={presets.map((p) => ({ value: p.name, label: p.name }))}
                />
                <Button icon={<SaveOutlined />} onClick={() => setSaveModalOpen(true)}>
                  保存当前
                </Button>
              </Space>
            </div>
          ),
        },
      ]}
    />

      <Modal
        title="保存过滤方案"
        open={saveModalOpen}
        onOk={handleSavePreset}
        onCancel={() => {
          setSaveModalOpen(false);
          setPresetName('');
        }}
        okButtonProps={{ disabled: !presetName.trim() }}
      >
        <Input
          placeholder="方案名称"
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
          onPressEnter={handleSavePreset}
        />
      </Modal>
    </>
  );
}
