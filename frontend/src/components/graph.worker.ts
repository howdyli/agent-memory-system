// 图谱计算 Web Worker：在后台线程执行聚类/超边/连接度计算，避免阻塞 UI。
import { computeGraph, type GraphComputeInput } from './graphCompute';

interface WorkerRequest extends GraphComputeInput {
  _reqId: number;
}

self.onmessage = (e: MessageEvent<WorkerRequest>) => {
  const { _reqId, ...input } = e.data;
  const result = computeGraph(input);
  // Map 可被结构化克隆，直接回传
  (self as unknown as Worker).postMessage({ _reqId, ...result });
};
