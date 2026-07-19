// 图谱计算纯函数模块。
// 抽离自 GraphVisualizer 的 useMemo 逻辑，供主线程、Web Worker 与单元测试复用。

export interface Entity {
  id?: string | number;
  entity_id?: string | number;
  name?: string;
  entity_type?: string;
  [key: string]: unknown;
}

export interface Relationship {
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

/** 超过该实体数量时进行聚类展示 */
export const CLUSTER_THRESHOLD = 500;

/** 计算每个实体的连接度 */
export function computeDegreeMap(
  entities: Entity[],
  relationships: Relationship[],
): Map<string, number> {
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

export interface GraphComputeInput {
  entities: Entity[];
  relationships: Relationship[];
  expandedCluster: string | null;
  threshold?: number;
}

export interface DisplayData {
  displayEntities: Entity[];
  displayRelationships: Relationship[];
  shouldCluster: boolean;
}

/**
 * 根据是否需要聚类 / 是否展开某个聚类，构建实际展示的实体与关系。
 * 与 GraphVisualizer 原 useMemo 逻辑保持一致。
 */
export function buildDisplayData(input: GraphComputeInput): DisplayData {
  const { entities, relationships, expandedCluster } = input;
  const threshold = input.threshold ?? CLUSTER_THRESHOLD;
  const shouldCluster = entities.length > threshold && !expandedCluster;

  // 展开某个聚类：只显示该类型的实体及其内部关系
  if (expandedCluster) {
    const clusterType = expandedCluster.replace('cluster_', '');
    const clusterEntities = entities.filter(
      e => (e.entity_type || 'other').toLowerCase() === clusterType,
    );
    const clusterIds = new Set(clusterEntities.map(e => String(e.id ?? e.entity_id ?? '')));
    const clusterRels = relationships.filter(
      r => clusterIds.has(String(r.source_entity_id || '')) && clusterIds.has(String(r.target_entity_id || '')),
    );
    return { displayEntities: clusterEntities, displayRelationships: clusterRels, shouldCluster: false };
  }

  if (shouldCluster) {
    // 按类型分组
    const groups: Record<string, Entity[]> = {};
    entities.forEach(e => {
      const t = (e.entity_type || 'other').toLowerCase();
      if (!groups[t]) groups[t] = [];
      groups[t].push(e);
    });

    // 构建超级节点
    const superNodes: Entity[] = Object.entries(groups).map(([type, items]) => ({
      id: `cluster_${type}`,
      name: `${type} (${items.length})`,
      entity_type: type,
      _cluster: true,
      _clusterEntities: items,
    }));

    // 构建跨组超边
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

    return { displayEntities: superNodes, displayRelationships: superEdges, shouldCluster: true };
  }

  return { displayEntities: entities, displayRelationships: relationships, shouldCluster: false };
}

export interface GraphComputeResult {
  displayEntities: Entity[];
  displayRelationships: Relationship[];
  degreeMap: Map<string, number>;
  shouldCluster: boolean;
}

/** 组合：构建展示数据 + 计算连接度。worker 与主线程回退均调用此函数。 */
export function computeGraph(input: GraphComputeInput): GraphComputeResult {
  const { displayEntities, displayRelationships, shouldCluster } = buildDisplayData(input);
  const degreeMap = computeDegreeMap(displayEntities, displayRelationships);
  return { displayEntities, displayRelationships, degreeMap, shouldCluster };
}
