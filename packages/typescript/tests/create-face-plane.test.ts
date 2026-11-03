import { describe, it, expect } from 'vitest';
import { create, Point3 } from '../src/create';

/**
 * A face's stored plane satisfies n.p + d = 0 for the face's own vertices.
 *
 * SketchUp stores the plane that way (all 481 faces of two real files checked: gondola_v20.skp and
 * capilla_quiroz_v17.skp). The writer used to store d as +n.centroid, i.e. with the wrong sign: openskp's own
 * reader only uses the normal, so a round trip never noticed, and the SDK's SUFaceGetPlane recomputes the
 * plane, so it does not either - the raw record has to be read, as here (C++ fixed the same in #477).
 */
const MARKER = Buffer.from([0xff, 0xff, 3, 0, 5, 0, ...Buffer.from('CFace')]);
const POLYGONS: Point3[][] = [
  [[0, 0, 3], [2, 0, 3], [2, 2, 3], [0, 2, 3]],
  [[0, 2, 3], [2, 2, 3], [2, 0, 3], [0, 0, 3]],
  [[-7, 0, 0], [-7, 2, 0], [-7, 2, 2], [-7, 0, 2]],
  [[10, 20, 35], [12, 20, 37], [12, 22, 39], [10, 22, 37]],
];

function storedPlane(data: Uint8Array): [number, number, number, number] {
  const buf = Buffer.from(data);
  let pos = buf.indexOf(MARKER) + MARKER.length;
  expect(buf.readUInt16LE(pos)).toBe(0); // null attribute ref
  pos += 2;
  const mask = buf[pos++];
  for (let bit = 0; bit < 8; bit++) if (mask & (1 << bit)) pos++; // the persistent-id bytes the mask announces
  pos += 10; // material and drawing flags
  return [buf.readDoubleLE(pos), buf.readDoubleLE(pos + 8), buf.readDoubleLE(pos + 16), buf.readDoubleLE(pos + 24)];
}

describe('the face plane the writer stores', () => {
  for (const nested of [false, true]) {
    POLYGONS.forEach((polygon, i) => {
      it(`agrees with the vertices (polygon ${i}, ${nested ? 'in a component definition' : 'at the root'})`, () => {
        const builder = create();
        if (nested) {
          const def = builder.addComponentDefinition('offset', (d) => d.addFace(polygon));
          builder.addInstance(def);
        } else {
          builder.addFace(polygon);
        }
        const [a, b, c, d] = storedPlane(builder.toBytes());
        for (const [x, y, z] of polygon) expect(a * x + b * y + c * z + d).toBeCloseTo(0, 9);
      });
    });
  }
});
