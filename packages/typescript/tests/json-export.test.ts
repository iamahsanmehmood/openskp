import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, toJSON, SkpModel } from '../src/index';

/**
 * Real-file regression test for toJSON() - openskp's canonical JSON
 * export schema, shared with the Python port's to_dict(). Every
 * assertion here was cross-checked directly against Python's
 * to_dict() on this exact fixture.
 */
describe('toJSON', () => {
  const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
  const buf = fs.readFileSync(filePath);
  const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;

  it('matches Python to_dict() ground truth on a real file', () => {
    const model = parseSkp(arrayBuffer);
    const d = toJSON(model) as any;

    expect(d.format_version).toBe('1.0');
    expect(d.sketchup_version).toBe('{17.0.18899}');
    expect(d.total_definitions).toBe(2);
    expect(d.total_layers).toBe(1);
    expect(d.total_meshes).toBe(0); // no scene passed

    expect(d.root.vertex_count).toBe(251);
    expect(d.root.edge_count).toBe(390);
    expect(d.root.face_count).toBe(146);
    expect(d.root.instances.length).toBe(3);
    // The raw (pre-bake) instance shape is flat, matching TS's own
    // Instance type - no layer/properties/children (see item 17: those
    // are dead fields at parse time in every language except C++, and
    // TS's Instance type doesn't declare them at all).
    expect(Object.keys(d.root.instances[0]).sort()).toEqual(
      ['guid', 'matrix', 'name', 'ref_idx'].sort()
    );

    const puerta = Object.values(d.definitions).find((v: any) => v.name === 'puerta') as any;
    expect(puerta.id).toBe(40);
    expect(puerta.vertex_count).toBe(64);
    expect(puerta.edge_count).toBe(95);
    expect(puerta.face_count).toBe(24);
    expect(puerta.edges.length).toBe(95);
    expect(puerta.faces.length).toBe(24);

    // Layer/material colors are nested {r,g,b[,a]} objects, not a flat
    // color_r/g/b or a positional [r,g,b,a] list.
    expect(d.layers[0].color).toEqual({ r: 255, g: 84, b: 84 });
    expect(typeof d.materials[0].color.r).toBe('number');
    expect(typeof d.materials[0].color.a).toBe('number');

    expect(d.mesh_index).toEqual({});
    expect(d.scene_hierarchy).toBeNull();
  });

  it('includes scene_hierarchy/mesh_index with snake_case keys when a scene is passed', () => {
    const model = parseSkp(arrayBuffer);
    const scene = buildScene(arrayBuffer);
    const d = toJSON(model, scene) as any;

    expect(d.total_meshes).toBe(Object.keys(scene.meshIndex).length);
    expect(d.scene_hierarchy.name).toBe('ROOT');
    // Snake_case, matching the rest of the schema - this function used
    // to emit definitionName/positionMm here instead.
    expect(d.scene_hierarchy).toHaveProperty('definition_name');
    expect(d.scene_hierarchy).toHaveProperty('position_mm');
    expect(d.scene_hierarchy).toHaveProperty('attribute_dictionaries');
    expect(d.scene_hierarchy).not.toHaveProperty('definitionName');

    const firstMesh = Object.values(d.mesh_index)[0] as any;
    expect(firstMesh).toHaveProperty('definition_name');
    expect(firstMesh).toHaveProperty('position_mm');
    expect(firstMesh).toHaveProperty('attribute_dictionaries');
  });

  it('includes attribute_dictionaries from real plugin data in scene_hierarchy and mesh_index', () => {
    const untitledBuf = fs.readFileSync(path.join(__dirname, 'fixtures', 'Untitled.skp'));
    const untitledAb = untitledBuf.buffer.slice(
      untitledBuf.byteOffset,
      untitledBuf.byteOffset + untitledBuf.byteLength
    ) as ArrayBuffer;
    const model = parseSkp(untitledAb);
    const scene = buildScene(untitledAb);
    const d = toJSON(model, scene) as any;

    const findNode = (node: any, target: string): any => {
      if (node.name === target) return node;
      for (const child of node.children ?? []) {
        const found = findNode(child, target);
        if (found) return found;
      }
      return null;
    };

    const w1 = findNode(d.scene_hierarchy, 'W1');
    expect(w1).not.toBeNull();
    expect(Object.keys(w1)).toEqual([
      'name',
      'definition_name',
      'layer',
      'position_mm',
      'properties',
      'attribute_dictionaries',
      'children',
    ]);
    expect(w1.properties).toEqual({});
    expect(w1.attribute_dictionaries['steelframer-dict'].generator).toBe(
      'SteelFramer::Engine::PanelGenerator'
    );
    expect(w1.attribute_dictionaries['steelframer-dict'].profile).toBe('362S200-43');
    expect(w1.attribute_dictionaries['steelframer-dict'].height).toBe('2700');
    expect(w1.attribute_dictionaries['steelframer-dict'].auto_dimensions).toBe('1');

    const meshWithSteel = Object.values(d.mesh_index).find(
      (m: any) => m.attribute_dictionaries?.['steelframer-dict']
    ) as any;
    expect(meshWithSteel).toBeDefined();
    expect(Object.keys(meshWithSteel)).toEqual([
      'name',
      'definition_name',
      'layer',
      'position_mm',
      'properties',
      'attribute_dictionaries',
      'path',
    ]);
  });

  it('exports all VFF attribute value types (numbers, booleans, arrays, Point3d/Length) matching Python shape', () => {
    const attrFixture = path.resolve(
      __dirname,
      '../../python/tests/fixtures/attribute_bool_time_2025.skp'
    );
    const attrBuf = fs.readFileSync(attrFixture);
    const attrAb = attrBuf.buffer.slice(
      attrBuf.byteOffset,
      attrBuf.byteOffset + attrBuf.byteLength
    ) as ArrayBuffer;
    const model = parseSkp(attrAb);
    const scene = buildScene(attrAb);
    const d = JSON.parse(JSON.stringify(toJSON(model, scene))) as any;

    const attrNode = d.scene_hierarchy.children.find((n: any) => n.name === 'AttrComp');
    expect(attrNode).toBeDefined();
    expect(attrNode.properties).toEqual({});
    expect(attrNode.attribute_dictionaries).toEqual({
      stage3: {
        array: '1,two,3.5,1',
        bool_false: '0',
        bool_true: '1',
        float: '1.5',
        int: '42',
        length: '60',
        nested: '1,2,3,4',
        nothing: '',
        point: '1,2,3',
        string: 'hello',
        timestamp: '1700000000',
        vector: '4,5,6',
      },
      stage3_other: {
        key: 'value',
      },
    });

    const attrMeshes = Object.values(d.mesh_index).filter(
      (m: any) => m.name === 'AttrComp'
    ) as any[];
    expect(attrMeshes.length).toBeGreaterThan(0);
    for (const m of attrMeshes) {
      expect(m.attribute_dictionaries).toEqual(attrNode.attribute_dictionaries);
    }
  });

  it('produces JSON-serializable output with no undefined/circular values', () => {
    const model = parseSkp(arrayBuffer);
    const scene = buildScene(arrayBuffer);
    const d = toJSON(model, scene);
    expect(() => JSON.stringify(d)).not.toThrow();
  });
});
