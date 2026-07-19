import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import {
  computeGraph,
  CLUSTER_THRESHOLD,
  type Entity,
  type Relationship,
  type GraphComputeResult,
} from './graphCompute';

interface Props {
  entities: Entity[];
  relationships: Relationship[];
  width?: string | number;
  height?: string | number;
  /** 外部搜索关键词，匹配实体名高亮 */
  searchKeyword?: string;
  /** 节点点击回调 */
  onNodeSelect?: (entity: Entity | null) => void;
  /** 节点右键菜单回调 */
  onNodeContextMenu?: (entity: Entity, event: { x: number; y: number }) => void;
  /** 布局模式 */
  layoutMode?: 'force' | 'hierarchical' | 'static';
  /** 高亮节点 ID 集合（用于邻居高亮） */
  highlightNodeIds?: Set<string>;
}

const ENTITY_COLORS: Record<string, string> = {
  person: '#1677ff',
  organization: '#52c41a',
  location: '#faad14',
  event: '#ff4d4f',
  concept: '#722ed1',
  technology: '#eb2f96',
  product: '#fa8c16',
  other: '#13c2c2',
};

const DEFAULT_COLOR = '#8c8c8c';

function getEntityColor(type?: string): string {
  return ENTITY_COLORS[(type || 'other').toLowerCase()] || DEFAULT_COLOR;
}

// computeDegreeMap / CLUSTER_THRESHOLD 已抽离到 graphCompute.ts

/** 实体数量超过该阈值时，将图谱计算放到 Web Worker 后台线程 */
const WORKER_ENTITY_THRESHOLD = 200;

export default function GraphVisualizer({
  entities, relationships, width = '100%', height = 500,
  searchKeyword, onNodeSelect, onNodeContextMenu, layoutMode = 'force',
  highlightNodeIds,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [expandedCluster, setExpandedCluster] = useState<string | null>(null);

  // 是否处于聚类展示模式（用于点击展开/统计显示）
  const shouldCluster = entities.length > CLUSTER_THRESHOLD && !expandedCluster;

  // 展示数据（聚类/超边/连接度）计算：大数据量走 Web Worker，小数据量走主线程
  const [computed, setComputed] = useState<GraphComputeResult>(() =>
    computeGraph({ entities, relationships, expandedCluster, threshold: CLUSTER_THRESHOLD })
  );
  const workerRef = useRef<Worker | null>(null);
  const reqIdRef = useRef(0);

  useEffect(() => {
    const input = { entities, relationships, expandedCluster, threshold: CLUSTER_THRESHOLD };

    // 小数据量或环境不支持 Worker：直接主线程计算，避免通信开销
    if (entities.length < WORKER_ENTITY_THRESHOLD || typeof Worker === 'undefined') {
      setComputed(computeGraph(input));
      return;
    }

    let cancelled = false;
    try {
      if (!workerRef.current) {
        workerRef.current = new Worker(new URL('./graph.worker.ts', import.meta.url), { type: 'module' });
      }
      const worker = workerRef.current;
      const reqId = ++reqIdRef.current;
      const handler = (e: MessageEvent<GraphComputeResult & { _reqId: number }>) => {
        if (cancelled || e.data?._reqId !== reqId) return;
        setComputed({
          displayEntities: e.data.displayEntities,
          displayRelationships: e.data.displayRelationships,
          degreeMap: e.data.degreeMap,
          shouldCluster: e.data.shouldCluster,
        });
        worker.removeEventListener('message', handler);
      };
      worker.addEventListener('message', handler);
      worker.postMessage({ ...input, _reqId: reqId });
      return () => {
        cancelled = true;
        worker.removeEventListener('message', handler);
      };
    } catch {
      // Worker 不可用：回退主线程
      setComputed(computeGraph(input));
    }
  }, [entities, relationships, expandedCluster]);

  // 组件卸载时销毁 worker
  useEffect(() => () => {
    workerRef.current?.terminate();
    workerRef.current = null;
  }, []);

  const { displayEntities, displayRelationships, degreeMap } = computed;

  // Compute max degree for node sizing
  const maxDegree = useMemo(() => Math.max(1, ...degreeMap.values()), [degreeMap]);

  // Build node data
  const nodeItems = useMemo(() => {
    return displayEntities.map(e => {
      const id = String(e.id ?? e.entity_id ?? '');
      const entityType = (e.entity_type || 'other').toLowerCase();
      const degree = degreeMap.get(id) || 0;
      const size = 12 + (degree / maxDegree) * 28; // 12~40 range
      const isCluster = (e as any)._cluster;

      // Search highlight
      const isHighlighted = searchKeyword
        && (e.name || '').toLowerCase().includes(searchKeyword.toLowerCase());

      // Neighbor highlight dimming
      const isDimmed = highlightNodeIds && highlightNodeIds.size > 0 && !highlightNodeIds.has(id);

      return {
        id,
        label: isCluster ? (e.name || id) : (e.name || String(id)).substring(0, 24),
        title: `<div style="padding:4px 0"><b>${e.name || id}</b><br/>` +
               `类型: ${e.entity_type || '-'}<br/>` +
               `连接数: ${degree}</div>`,
        color: {
          background: isDimmed ? '#e8e8e8' : isCluster ? '#d9d9d9' : getEntityColor(entityType),
          border: isHighlighted ? '#ff4d4f' : isDimmed ? '#d9d9d9' : getEntityColor(entityType),
          highlight: { background: '#fff', border: getEntityColor(entityType) },
          hover: { background: getEntityColor(entityType), border: '#333' },
        },
        borderWidth: isHighlighted ? 4 : 2,
        shape: isCluster ? 'box' : 'dot',
        size: isCluster ? 28 : size,
        font: {
          size: isCluster ? 14 : (isHighlighted ? 14 : 12),
          face: '-apple-system, BlinkMacSystemFont, Arial, sans-serif',
          color: isDimmed ? '#bbb' : (isHighlighted ? '#ff4d4f' : '#333'),
          bold: isHighlighted ? { color: '#ff4d4f' } : undefined,
        },
        shadow: isHighlighted ? { enabled: true, color: 'rgba(255,77,79,0.4)', size: 10 } : false,
        opacity: isDimmed ? 0.3 : 1,
        _entity: e, // store original entity for callbacks
      };
    });
  }, [displayEntities, degreeMap, maxDegree, searchKeyword, highlightNodeIds]);

  // Build edge data
  const edgeItems = useMemo(() => {
    return displayRelationships.map(r => ({
      id: String(r.id ?? r.relationship_id ?? ''),
      from: String(r.source_entity_id || ''),
      to: String(r.target_entity_id || ''),
      label: r.relation_type || '',
      title: `<b>${r.relation_type || '关系'}</b><br/>` +
             `${r.source_entity_name || r.source_entity_id || '?'} → ${r.target_entity_name || r.target_entity_id || '?'}<br/>` +
             `权重: ${r.weight ?? r.confidence ?? '-'}`,
      width: Math.max(1, (r.weight ?? r.confidence ?? 0.5) * 3),
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      color: { color: '#c0c0c0', highlight: '#1677ff', hover: '#666' },
      font: { size: 10, color: '#888', strokeWidth: 2, strokeColor: '#fff' },
      smooth: { enabled: true, type: 'continuous' },
    }));
  }, [displayRelationships]);

  // Build options based on layout mode
  const options = useMemo(() => {
    const base: Record<string, unknown> = {
      interaction: {
        hover: true,
        tooltipDelay: 150,
        navigationButtons: true,
        keyboard: true,
        zoomView: true,
        selectConnectedEdges: true,
      },
      edges: {
        smooth: { type: 'continuous' },
        selectionWidth: 2,
      },
      nodes: {
        chosen: true,
      },
    };

    if (layoutMode === 'hierarchical') {
      base.layout = {
        hierarchical: {
          enabled: true,
          direction: 'UD',
          sortMethod: 'hubsize',
          nodeSpacing: 140,
          levelSeparation: 120,
        },
      };
      base.physics = { enabled: false };
    } else if (layoutMode === 'static') {
      base.layout = { improvedLayout: true };
      base.physics = { enabled: false };
    } else {
      base.physics = {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -50,
          centralGravity: 0.008,
          springLength: 180,
          springConstant: 0.03,
          damping: 0.4,
          avoidOverlap: 0.3,
        },
        stabilization: { iterations: 150 },
      };
      base.layout = { improvedLayout: true };
    }

    return base;
  }, [layoutMode]);

  // Initialize / update network
  useEffect(() => {
    if (!containerRef.current) return;

    const nodes = new DataSet(nodeItems as any[]);
    const edges = new DataSet(edgeItems as any[]);
    const data = { nodes, edges } as any;

    if (networkRef.current) {
      networkRef.current.destroy();
    }

    networkRef.current = new Network(containerRef.current, data, options as any);

    // Node click → select / expand cluster
    networkRef.current.on('click', (params: any) => {
      if (params.nodes?.length > 0) {
        const nodeId = params.nodes[0];
        setSelectedNodeId(nodeId);
        const node = nodeItems.find(n => n.id === nodeId);
        const entity = (node as any)?._entity;
        if (entity?._cluster && shouldCluster) {
          // Expand cluster
          setExpandedCluster(String(nodeId));
          return;
        }
        onNodeSelect?.(entity || null);
      } else {
        setSelectedNodeId(null);
        onNodeSelect?.(null);
      }
    });

    // Right-click → context menu
    networkRef.current.on('oncontext', (params: any) => {
      if (params.pointer?.DOM && onNodeContextMenu) {
        const nodeId = params.nodes?.[0];
        if (nodeId) {
          const node = nodeItems.find(n => n.id === nodeId);
          const entity = (node as any)?._entity;
          if (entity && !(entity as any)._cluster) {
            onNodeContextMenu(entity, { x: params.pointer.DOM.x, y: params.pointer.DOM.y });
          }
        }
      }
    });

    // Fit view after stabilization
    networkRef.current.once('stabilizationIterationsDone', () => {
      networkRef.current?.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
    });

    return () => {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
      }
    };
  }, [nodeItems, edgeItems, options, onNodeSelect]);

  // Programmatic highlight when searchKeyword changes (select matching nodes)
  useEffect(() => {
    if (!networkRef.current) return;
    if (searchKeyword) {
      const matchIds = nodeItems
        .filter(n => searchKeyword && (n.label || '').toLowerCase().includes(searchKeyword.toLowerCase()))
        .map(n => n.id);
      if (matchIds.length > 0) {
        networkRef.current.selectNodes(matchIds, false);
        if (matchIds.length <= 3) {
          networkRef.current.focus(matchIds[0], { scale: 1.5, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
        }
      }
    } else {
      networkRef.current.unselectAll();
    }
  }, [searchKeyword, nodeItems]);

  // Re-fit helper
  const handleFit = useCallback(() => {
    networkRef.current?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
  }, []);

  // No data state
  if (displayEntities.length === 0 && displayRelationships.length === 0) {
    return (
      <div style={{
        width, height,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#fafafa', borderRadius: 8, color: '#999', fontSize: 14,
        border: '1px dashed #d9d9d9',
      }}>
        暂无实体和关系数据
      </div>
    );
  }

  // Unique entity types for legend
  const activeTypes = [...new Set(displayEntities.map(e => (e.entity_type || 'other').toLowerCase()))];

  return (
    <div style={{ position: 'relative' }}>
      <div
        ref={containerRef}
        style={{ width, height, border: '1px solid #f0f0f0', borderRadius: 8, background: '#fff' }}
      />

      {/* Legend */}
      <div style={{
        position: 'absolute', bottom: 12, left: 12,
        background: 'rgba(255,255,255,0.95)', borderRadius: 6, padding: '8px 12px',
        fontSize: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
        display: 'flex', flexWrap: 'wrap', gap: '6px 14px', maxWidth: 360,
      }}>
        {activeTypes.map(t => (
          <span key={t} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{
              width: 10, height: 10, borderRadius: '50%',
              background: getEntityColor(t), display: 'inline-block',
            }} />
            <span style={{ color: '#555' }}>{t}</span>
          </span>
        ))}
      </div>

      {/* Stats overlay */}
      <div style={{
        position: 'absolute', top: 8, right: 8,
        fontSize: 11, color: '#888', background: 'rgba(255,255,255,0.92)',
        padding: '4px 10px', borderRadius: 4,
        display: 'flex', gap: 12,
      }}>
        <span>{displayEntities.length} 实体</span>
        <span>{displayRelationships.length} 关系</span>
        {shouldCluster && <span style={{ color: '#fa8c16' }}>聚类模式</span>}
        {selectedNodeId && <span style={{ color: '#1677ff' }}>已选中</span>}
      </div>

      {/* Controls */}
      <div style={{
        position: 'absolute', bottom: 12, right: 12,
        display: 'flex', gap: 4,
      }}>
        <button
          onClick={handleFit}
          style={{
            background: 'rgba(255,255,255,0.95)', border: '1px solid #d9d9d9',
            borderRadius: 4, padding: '4px 8px', fontSize: 11, cursor: 'pointer',
            color: '#555',
          }}
        >
          适应视图
        </button>
      </div>

      {/* Hint */}
      <div style={{
        position: 'absolute', top: 8, left: 12,
        fontSize: 11, color: '#bbb', background: 'rgba(255,255,255,0.85)',
        padding: '2px 8px', borderRadius: 4,
      }}>
        拖拽移动 · 滚轮缩放 · 点击选中 · 右键菜单 · 悬停查看
      </div>

      {/* Cluster mode controls */}
      {shouldCluster && (
        <div style={{
          position: 'absolute', bottom: 40, right: 12,
          display: 'flex', gap: 4,
        }}>
          <button
            onClick={() => setExpandedCluster(null)}
            style={{
              background: 'rgba(255,255,255,0.95)', border: '1px solid #fa8c16',
              borderRadius: 4, padding: '4px 8px', fontSize: 11, cursor: 'pointer',
              color: '#fa8c16',
            }}
          >
            展开所有节点
          </button>
        </div>
      )}
      {expandedCluster && (
        <div style={{
          position: 'absolute', bottom: 40, right: 12,
          display: 'flex', gap: 4,
        }}>
          <button
            onClick={() => setExpandedCluster(null)}
            style={{
              background: 'rgba(255,255,255,0.95)', border: '1px solid #1677ff',
              borderRadius: 4, padding: '4px 8px', fontSize: 11, cursor: 'pointer',
              color: '#1677ff',
            }}
          >
            返回聚类视图
          </button>
        </div>
      )}
    </div>
  );
}
