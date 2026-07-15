import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';

interface Entity {
  id?: string | number;
  entity_id?: string | number;
  name?: string;
  entity_type?: string;
  [key: string]: unknown;
}

interface Relationship {
  id?: string | number;
  relationship_id?: string | number;
  source_entity_id?: string;
  source_entity_name?: string;
  target_entity_id?: string;
  target_entity_name?: string;
  relation_type?: string;
  weight?: number;
  confidence?: number;
  [key: string]: unknown;
}

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

/** 计算每个实体的连接度 */
function computeDegreeMap(entities: Entity[], relationships: Relationship[]): Map<string, number> {
  const map = new Map<string, number>();
  entities.forEach(e => {
    const id = String(e.id ?? e.entity_id ?? '');
    map.set(id, 0);
  });
  relationships.forEach(r => {
    const src = String(r.source_entity_id || '');
    const tgt = String(r.target_entity_id || '');
    map.set(src, (map.get(src) || 0) + 1);
    map.set(tgt, (map.get(tgt) || 0) + 1);
  });
  return map;
}

const CLUSTER_THRESHOLD = 500;

export default function GraphVisualizer({
  entities, relationships, width = '100%', height = 500,
  searchKeyword, onNodeSelect, onNodeContextMenu, layoutMode = 'force',
  highlightNodeIds,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [expandedCluster, setExpandedCluster] = useState<string | null>(null);

  // Determine if we should cluster
  const shouldCluster = entities.length > CLUSTER_THRESHOLD && !expandedCluster;

  // Build clustered data when needed
  const { displayEntities, displayRelationships } = useMemo(() => {
    // If expanded cluster is set, show only that cluster's entities
    if (expandedCluster) {
      const clusterType = expandedCluster.replace('cluster_', '');
      const clusterEntities = entities.filter(
        e => (e.entity_type || 'other').toLowerCase() === clusterType
      );
      const clusterIds = new Set(clusterEntities.map(e => String(e.id ?? e.entity_id ?? '')));
      const clusterRels = relationships.filter(
        r => clusterIds.has(String(r.source_entity_id || '')) && clusterIds.has(String(r.target_entity_id || ''))
      );
      return { displayEntities: clusterEntities, displayRelationships: clusterRels };
    }

    if (shouldCluster) {
      // Group entities by type
      const groups: Record<string, Entity[]> = {};
      entities.forEach(e => {
        const t = (e.entity_type || 'other').toLowerCase();
        if (!groups[t]) groups[t] = [];
        groups[t].push(e);
      });

      // Create super nodes
      const superNodes: Entity[] = Object.entries(groups).map(([type, items]) => ({
        id: `cluster_${type}`,
        name: `${type} (${items.length})`,
        entity_type: type,
        _cluster: true,
        _clusterEntities: items,
      }));

      // Build cross-group edges
      const entityIdToType = new Map<string, string>();
      entities.forEach(e => {
        const id = String(e.id ?? e.entity_id ?? '');
        entityIdToType.set(id, (e.entity_type || 'other').toLowerCase());
      });

      const crossEdges: Record<string, number> = {};
      relationships.forEach(r => {
        const srcType = entityIdToType.get(String(r.source_entity_id || ''));
        const tgtType = entityIdToType.get(String(r.target_entity_id || ''));
        if (srcType && tgtType && srcType !== tgtType) {
          const key = [srcType, tgtType].sort().join('|');
          crossEdges[key] = (crossEdges[key] || 0) + 1;
        }
      });

      const superEdges: Relationship[] = Object.entries(crossEdges).map(([key, count], idx) => {
        const [src, tgt] = key.split('|');
        return {
          id: `cluster_edge_${idx}`,
          source_entity_id: `cluster_${src}`,
          target_entity_id: `cluster_${tgt}`,
          relation_type: `${count} 关系`,
          confidence: Math.min(count / 10, 1),
        };
      });

      return { displayEntities: superNodes, displayRelationships: superEdges };
    }

    return { displayEntities: entities, displayRelationships: relationships };
  }, [entities, relationships, shouldCluster, expandedCluster]);

  // Compute degree for node sizing
  const degreeMap = useMemo(() => computeDegreeMap(displayEntities, displayRelationships), [displayEntities, displayRelationships]);
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
