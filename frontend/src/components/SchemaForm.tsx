import { Form, Input, InputNumber, Switch, Select, Button, Space, Typography } from 'antd';
import { PlusOutlined, MinusCircleOutlined } from '@ant-design/icons';
import type { FormInstance } from 'antd';

const { TextArea } = Input;
const { Text } = Typography;

export interface SchemaProperty {
  type?: string;
  description?: string;
  enum?: string[];
  items?: SchemaProperty;
  properties?: Record<string, SchemaProperty>;
  required?: string[];
  additionalProperties?: unknown;
  default?: unknown;
}

interface SchemaFormProps {
  schema: {
    type?: string;
    properties?: Record<string, SchemaProperty>;
    required?: string[];
  };
  form: FormInstance;
}

/** 根据 JSON Schema property 渲染对应表单控件 */
function renderField(prop: SchemaProperty, fieldName: string): React.ReactNode {
  // enum -> Select
  if (prop.enum && prop.enum.length > 0) {
    return (
      <Select
        placeholder={`选择 ${fieldName}`}
        allowClear
        options={prop.enum.map((v) => ({ label: v, value: v }))}
      />
    );
  }

  switch (prop.type) {
    case 'string':
      return <TextArea rows={2} placeholder={prop.description || `输入 ${fieldName}`} />;
    case 'integer':
    case 'number':
      return <InputNumber style={{ width: '100%' }} placeholder={prop.description || `输入 ${fieldName}`} />;
    case 'boolean':
      return <Switch />;
    case 'array':
      return renderArrayField(prop, fieldName);
    case 'object':
      return renderObjectFields(prop);
    default:
      // object without explicit type but has properties
      if (prop.properties) {
        return renderObjectFields(prop);
      }
      // additionalProperties or unknown -> JSON textarea
      return <TextArea rows={3} placeholder="输入 JSON 对象" />;
  }
}

function renderArrayField(prop: SchemaProperty, fieldName: string): React.ReactNode {
  const itemProp = prop.items;
  // array of simple types
  if (!itemProp || itemProp.type === 'string' || itemProp.type === 'number' || itemProp.type === 'integer') {
    return (
      <Form.List name={fieldName}>
        {(fields, { add, remove }) => (
          <>
            {fields.map((field) => (
              <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                <Form.Item {...field} noStyle>
                  <Input placeholder={`#${field.name + 1}`} style={{ width: 260 }} />
                </Form.Item>
                <MinusCircleOutlined onClick={() => remove(field.name)} />
              </Space>
            ))}
            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
              添加项
            </Button>
          </>
        )}
      </Form.List>
    );
  }

  // array of objects
  if (itemProp.type === 'object' && itemProp.properties) {
    const objProps = itemProp.properties;
    const objRequired = itemProp.required || [];
    return (
      <Form.List name={fieldName}>
        {(fields, { add, remove }) => (
          <>
            {fields.map((field) => (
              <div
                key={field.key}
                style={{
                  border: '1px dashed #d9d9d9',
                  borderRadius: 6,
                  padding: '8px 12px',
                  marginBottom: 8,
                  position: 'relative',
                }}
              >
                <MinusCircleOutlined
                  style={{ position: 'absolute', top: 8, right: 8, color: '#999' }}
                  onClick={() => remove(field.name)}
                />
                {Object.entries(objProps).map(([subKey, subProp]) => (
                  <Form.Item
                    key={subKey}
                    name={[field.name, subKey]}
                    label={subKey}
                    rules={objRequired.includes(subKey) ? [{ required: true, message: `请输入 ${subKey}` }] : []}
                    valuePropName={subProp.type === 'boolean' ? 'checked' : 'value'}
                    style={{ marginBottom: 8 }}
                  >
                    {renderField(subProp, subKey)}
                  </Form.Item>
                ))}
              </div>
            ))}
            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
              添加项
            </Button>
          </>
        )}
      </Form.List>
    );
  }

  // fallback
  return <TextArea rows={3} placeholder="输入 JSON 数组" />;
}

function renderObjectFields(prop: SchemaProperty): React.ReactNode {
  if (!prop.properties) {
    return <TextArea rows={3} placeholder="输入 JSON 对象" />;
  }
  const required = prop.required || [];
  return (
    <div style={{ paddingLeft: 8, borderLeft: '2px solid #f0f0f0' }}>
      {Object.entries(prop.properties).map(([key, subProp]) => (
        <Form.Item
          key={key}
          name={key}
          label={
            <span>
              {key}
              {required.includes(key) && <Text type="danger" style={{ marginLeft: 4 }}>*</Text>}
            </span>
          }
          tooltip={subProp.description}
          rules={required.includes(key) ? [{ required: true, message: `请输入 ${key}` }] : []}
          valuePropName={subProp.type === 'boolean' ? 'checked' : 'value'}
          style={{ marginBottom: 12 }}
        >
          {renderField(subProp, key)}
        </Form.Item>
      ))}
    </div>
  );
}

/**
 * SchemaForm - 根据 JSON Schema 自动生成 Ant Design 表单
 *
 * 通用可复用组件，支持：
 * - string / number / integer / boolean / enum / object / array
 * - 递归嵌套渲染
 * - required 校验
 */
export default function SchemaForm({ schema, form: _form }: SchemaFormProps) {
  const properties = schema.properties || {};
  const required = schema.required || [];

  if (Object.keys(properties).length === 0) {
    return <Text type="secondary">该工具无需参数</Text>;
  }

  return (
    <>
      {Object.entries(properties).map(([key, prop]) => {
        // array with Form.List handled internally
        if (prop.type === 'array') {
          return (
            <Form.Item key={key} label={key} tooltip={prop.description} style={{ marginBottom: 12 }}>
              {renderArrayField(prop, key)}
            </Form.Item>
          );
        }

        // nested object (non-array)
        if (prop.type === 'object' && prop.properties) {
          return (
            <Form.Item key={key} label={key} tooltip={prop.description} style={{ marginBottom: 4 }}>
              {renderObjectFields(prop)}
            </Form.Item>
          );
        }

        return (
          <Form.Item
            key={key}
            name={key}
            label={
              <span>
                {key}
                {required.includes(key) && <Text type="danger" style={{ marginLeft: 4 }}>*</Text>}
              </span>
            }
            tooltip={prop.description}
            rules={required.includes(key) ? [{ required: true, message: `请输入 ${key}` }] : []}
            valuePropName={prop.type === 'boolean' ? 'checked' : 'value'}
            style={{ marginBottom: 12 }}
          >
            {renderField(prop, key)}
          </Form.Item>
        );
      })}
    </>
  );
}
