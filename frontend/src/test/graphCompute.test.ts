import { describe, it, expect } from 'vitest';
import {
  computeDegreeMap,
  buildDisplayData,
  computeGraph,
  CLUSTER_THRESHOLD,
  type Entity,
  type Relationship,
} from '../components/graphCompute';

// ============================================================
//  computeDegreeMap — 连接度计算
// ============================================================
describe('computeDegreeMap', () => {
  it('孤立实体的连接度应为 0', () => {
    const entities: Entity[] = [{ id: 'a' }, { id: 'b' }];
    const map = computeDegreeMap(entities, []);
    expect(map.get('a')).toBe(0);
    expect(map.get('b')).toBe(0);
  });

  it('应对边两端实体的连接度各 +1', () => {
    const entities: Entity[] = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const rels: Relationship[] = [
      { source_entity_id: 'a', target_entity_id: 'b' },
      { source_entity_id: 'a', target_entity_id: 'c' },
    ];
    const map = computeDegreeMap(entities, rels);
    expect(map.get('a')).toBe(2);
    expect(map.get('b')).toBe(1);
    expect(map.get('c')).toBe(1);
  });

  it('应兼容 entity_id 字段作为实体标识', () => {
    const entities: Entity[] = [{ entity_id: 'x' }];
    const rels: Relationship[] = [{ source_entity_id: 'x', target_entity_id: 'x' }];
    const map = computeDegreeMap(entities, rels);
    expect(map.get('x')).toBe(2);
  });
});

// ============================================================
//  buildDisplayData — 聚类 / 展开逻辑
// ============================================================
describe('buildDisplayData', () => {
  it('实体数低于阈值时原样返回，不聚类', () => {
    const entities: Entity[] = [
      { id: '1', entity_type: 'person' },
      { id: '2', entity_type: 'org' },
    ];
    const rels: Relationship[] = [{ source_entity_id: '1', target_entity_id: '2' }];
    const out = buildDisplayData({ entities, relationships: rels, expandedCluster: null, threshold: 500 });
    expect(out.shouldCluster).toBe(false);
    expect(out.displayEntities).toHaveLength(2);
    expect(out.displayRelationships).toHaveLength(1);
  });

  it('超过阈值时应按类型聚合为超级节点', () => {
    // 用低阈值触发聚类：3 person + 2 org = 5 实体，threshold=2
    const entities: Entity[] = [
      { id: 'p1', entity_type: 'person' },
      { id: 'p2', entity_type: 'person' },
      { id: 'p3', entity_type: 'person' },
      { id: 'o1', entity_type: 'org' },
      { id: 'o2', entity_type: 'org' },
    ];
    const rels: Relationship[] = [
      { source_entity_id: 'p1', target_entity_id: 'o1' }, // 跨组
      { source_entity_id: 'p2', target_entity_id: 'o2' }, // 跨组
      { source_entity_id: 'p1', target_entity_id: 'p2' }, // 组内，不产生超边
    ];
    const out = buildDisplayData({ entities, relationships: rels, expandedCluster: null, threshold: 2 });
    expect(out.shouldCluster).toBe(true);
    // 两个类型 -> 两个超级节点
    expect(out.displayEntities).toHaveLength(2);
    const ids = out.displayEntities.map(e => e.id).sort();
    expect(ids).toEqual(['cluster_org', 'cluster_person']);
    // person 超级节点应含 3 个成员
    const personNode = out.displayEntities.find(e => e.id === 'cluster_person')!;
    expect((personNode._clusterEntities as Entity[]).length).toBe(3);
  });

  it('聚类时应把跨组关系聚合为单条超边并计数', () => {
    const entities: Entity[] = [
      { id: 'p1', entity_type: 'person' },
      { id: 'p2', entity_type: 'person' },
      { id: 'o1', entity_type: 'org' },
    ];
    const rels: Relationship[] = [
      { source_entity_id: 'p1', target_entity_id: 'o1' },
      { source_entity_id: 'p2', target_entity_id: 'o1' },
    ];
    const out = buildDisplayData({ entities, relationships: rels, expandedCluster: null, threshold: 2 });
    // person<->org 两条跨组关系应合并为一条超边
    expect(out.displayRelationships).toHaveLength(1);
    expect(out.displayRelationships[0].relation_type).toBe('2 关系');
  });

  it('展开某聚类时只返回该类型实体及其内部关系', () => {
    const entities: Entity[] = [
      { id: 'p1', entity_type: 'person' },
      { id: 'p2', entity_type: 'person' },
      { id: 'o1', entity_type: 'org' },
    ];
    const rels: Relationship[] = [
      { source_entity_id: 'p1', target_entity_id: 'p2' }, // person 内部
      { source_entity_id: 'p1', target_entity_id: 'o1' }, // 跨类型，应被排除
    ];
    const out = buildDisplayData({ entities, relationships: rels, expandedCluster: 'cluster_person', threshold: 2 });
    expect(out.shouldCluster).toBe(false);
    expect(out.displayEntities.map(e => e.id).sort()).toEqual(['p1', 'p2']);
    expect(out.displayRelationships).toHaveLength(1);
  });
});

// ============================================================
//  computeGraph — 组合函数
// ============================================================
describe('computeGraph', () => {
  it('应同时返回展示数据与连接度', () => {
    const entities: Entity[] = [{ id: 'a' }, { id: 'b' }];
    const rels: Relationship[] = [{ source_entity_id: 'a', target_entity_id: 'b' }];
    const result = computeGraph({ entities, relationships: rels, expandedCluster: null });
    expect(result.displayEntities).toHaveLength(2);
    expect(result.degreeMap.get('a')).toBe(1);
    expect(result.degreeMap.get('b')).toBe(1);
    expect(result.shouldCluster).toBe(false);
  });

  it('degreeMap 应基于展示后的（聚类后）实体与超边计算', () => {
    const entities: Entity[] = [
      { id: 'p1', entity_type: 'person' },
      { id: 'p2', entity_type: 'person' },
      { id: 'o1', entity_type: 'org' },
    ];
    const rels: Relationship[] = [{ source_entity_id: 'p1', target_entity_id: 'o1' }];
    const result = computeGraph({ entities, relationships: rels, expandedCluster: null, threshold: 2 });
    expect(result.shouldCluster).toBe(true);
    // 一条超边连接 cluster_person 与 cluster_org，各 +1
    expect(result.degreeMap.get('cluster_person')).toBe(1);
    expect(result.degreeMap.get('cluster_org')).toBe(1);
  });

  it('默认 CLUSTER_THRESHOLD 应为 500', () => {
    expect(CLUSTER_THRESHOLD).toBe(500);
  });
});
