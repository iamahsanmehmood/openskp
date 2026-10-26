import { describe, it, expect } from 'vitest';
import { create } from '../src/create';
import { buildInstancedScene } from '../src/index';

/**
 * SketchUp renders the faces either side of a smooth edge as one surface:
 * the shared vertices carry the average of the faces' normals instead of a
 * crease. Two quads folded 30 degrees along x = 10 in.
 */
function folded(smoothEdges: boolean) {
  const builder = create();
  builder.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], { smoothEdges });
  const rise = 10 * Math.tan(Math.PI / 6);
  builder.addFace([[10, 0, 0], [20, 0, rise], [20, 10, rise], [10, 10, 0]], { smoothEdges });
  const bytes = builder.toBytes();
  const scene = buildInstancedScene(bytes.slice().buffer);
  const primitives = scene.meshResources.flatMap((resource) => resource.primitives);
  const seam: [number, number, number][] = [];
  for (const { positions, normals } of primitives) {
    for (let i = 0; i < positions.length; i += 3) {
      // glTF metres, Y-up: SketchUp's x = 10 in is x = 0.254 m.
      if (Math.abs(positions[i] - 0.254) < 1e-6) seam.push([normals[i], normals[i + 1], normals[i + 2]]);
    }
  }
  return seam;
}

const distinct = (normals: [number, number, number][]) =>
  new Set(normals.map((n) => n.map((c) => c.toFixed(4)).join(','))).size;

describe('normals across smooth edges', () => {
  it('averages the faces normals at the vertices of a smooth edge', () => {
    const seam = folded(true);
    expect(seam.length).toBeGreaterThan(0);
    expect(distinct(seam)).toBe(1);
    const [x, y, z] = seam[0];
    expect(Math.hypot(x, y, z)).toBeCloseTo(1, 6);
    // Halfway between the flat face (0, 1, 0) and the 30 degree one.
    expect(Math.abs(y)).toBeCloseTo(Math.cos(Math.PI / 12), 6);
  });

  it('keeps a crease along a hard edge', () => {
    expect(distinct(folded(false))).toBe(2);
  });
});
