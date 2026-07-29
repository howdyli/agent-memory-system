import { Tabs } from 'antd';
import { ExperimentOutlined, ThunderboltOutlined, SearchOutlined, ClockCircleOutlined, HistoryOutlined } from '@ant-design/icons';
import InjectionSimulator from '../components/playground/InjectionSimulator';
import RecallDebugger from '../components/playground/RecallDebugger';
import LifecycleSimulator from '../components/playground/LifecycleSimulator';
import EvolutionTimeline from '../components/playground/EvolutionTimeline';

export default function PlaygroundPage() {
  return (
    <div>
      <div className="page-header">
        <h2><ExperimentOutlined /> 记忆 Playground</h2>
        <p>交互式记忆调试工具：模拟注入、召回、生命周期衰减与冲突演变（全部只读，不影响真实数据）</p>
      </div>

      <Tabs
        items={[
          {
            key: 'injection',
            label: <span><ThunderboltOutlined /> 注入模拟器</span>,
            children: <InjectionSimulator />,
          },
          {
            key: 'recall',
            label: <span><SearchOutlined /> 召回调试器</span>,
            children: <RecallDebugger />,
          },
          {
            key: 'lifecycle',
            label: <span><ClockCircleOutlined /> 生命周期模拟器</span>,
            children: <LifecycleSimulator />,
          },
          {
            key: 'evolution',
            label: <span><HistoryOutlined /> 冲突演变链</span>,
            children: <EvolutionTimeline />,
          },
        ]}
      />
    </div>
  );
}
