const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..', '..', '..');
const { parseSkp } = require(path.join(root, 'packages/typescript/dist/index.js'));
const bytes = new Uint8Array(fs.readFileSync(process.argv[2]));
const model = parseSkp(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
const rel = (v) => (v === null || v === undefined ? null : Array.isArray(v) ? v.map(rel) : typeof v === 'number' ? Math.round(v * 1e9) / 1e9 : v);
const summary = {
  counts: {
    faces: model.root.faces.length,
    instances: model.root.instances.length,
    definitions: model.definitions.size,
    materials: model.materials.length,
    layers: model.layers.length,
    pages: model.pages.length,
    dimensions: model.dimensions.length,
    sectionPlanes: model.root.sectionPlanes.length,
    texts: model.root.texts.length,
    constructionLines: model.root.constructionLines.length,
    constructionPoints: model.root.constructionPoints.length,
  },
  definitions: [...model.definitions.values()].map((d) => [d.name, d.isGroup, d.faces.length, d.instances.length]).sort(),
  materials: model.materials.map((m) => [m.name, [m.color.r, m.color.g, m.color.b, m.color.a], m.texture ? [m.texture.filename, m.texture.width, m.texture.height] : null]).sort(),
  pages: model.pages.map((p) => [p.name, rel(p.eye), rel(p.target), rel(p.up), p.parallel, p.fov, p.orthoHeight, p.hiddenLayers]).sort(),
  dimensions: model.dimensions.map((d) => [rel(d.a), rel(d.b), d.offset, rel(d.normal)]),
  sectionPlanes: model.root.sectionPlanes.map((s) => [rel(s.plane), s.name, s.label]).sort(),
  texts: model.root.texts.map((t) => t.text).sort(),
  guides: [model.root.constructionLines.map((c) => [rel(c.start), rel(c.end), rel(c.direction)]).sort(), model.root.constructionPoints.map((c) => rel(c.position)).sort()],
};
console.log(JSON.stringify(summary, null, 2));
