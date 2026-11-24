// Build the shared deterministic VFF model with the built TypeScript package.
// Usage: node ts_driver.cjs <repo-root> <output.skp>
const fs = require('fs');
const path = require('path');

const root = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', '..'));
const out = process.argv[3] || '/tmp/ts_vff_golden.skp';
const fixtures = path.join(root, 'packages/typescript/tests/fixtures/vff_writer');
const spec = JSON.parse(fs.readFileSync(path.join(fixtures, 'spec.json'), 'utf8'));
const { createVff } = require(path.join(root, 'packages/typescript/dist/index.js'));

function convert(value) {
  if (Array.isArray(value)) return value.map(convert);
  if (value && typeof value === 'object' && value.$type) {
    const api = require(path.join(root, 'packages/typescript/dist/index.js'));
    if (value.$type === 'timestamp') return new api.VffTimestamp(value.value);
    if (value.$type === 'length') return new api.VffLength(value.value);
    if (value.$type === 'point') return new api.VffPoint3d(...value.value);
    if (value.$type === 'vector') return new api.VffVector3d(...value.value);
  }
  return value;
}
function attrs(name) {
  if (!name || !spec.attributes) return undefined;
  const out = {};
  for (const [key, value] of Object.entries(spec.attributes)) out[key] = convert(value);
  return out;
}

const builder = createVff(undefined, spec.options);
const materials = {};
const layers = {};
const handles = {};
for (const [image, info] of Object.entries(spec.textures)) {
  materials[info.name] = builder.addTextureMaterial(info.name, new Uint8Array(fs.readFileSync(path.join(fixtures, image))), {
    appliedWidth: info.width, appliedHeight: info.height, averageColor: info.averageColor,
  });
}
function run(ctx, op) {
  switch (op.op) {
    case 'material': materials[op.name] = builder.addMaterial(op.name, op.rgba, op.opacity); break;
    case 'texture': break;
    case 'layer': layers[op.name] = builder.addLayer(op.name, op.color, op.hidden || false); break;
    case 'face':
      ctx.addFace(op.points, {
        material: materials[op.material], layer: layers[op.layer], backMaterial: materials[op.backMaterial],
        hidden: op.hidden || false, holes: op.holes,
        frontUv: op.frontUv, backUv: op.backUv, attributes: attrs(op.attributes),
      });
      break;
    case 'circle':
      ctx.addCircle(op.center, op.normal, op.radius, {
        numSegments: op.numSegments, material: materials[op.material], layer: layers[op.layer], attributes: attrs(op.attributes),
      });
      break;
    case 'arc': ctx.addArc(op.center, op.normal, op.radius, op.startAngle, op.endAngle, { numSegments: op.numSegments }); break;
    case 'polyline': ctx.addPolyline(op.points, { closed: op.closed || false }); break;
    case 'definition': {
      const definition = builder.addComponentDefinition(op.name, { attributes: attrs(op.attributes) });
      for (const inner of op.ops || []) run(definition, inner);
      definition.close();
      handles[op.name] = definition;
      break;
    }
    case 'instance':
      ctx.addInstance(handles[op.definition], {
        translation: op.translation || [0, 0, 0],
        rotation: op.rotation ? { axis: op.rotation.axis, angleRadians: op.rotation.angle } : undefined,
        attributes: attrs(op.attributes),
      });
      break;
    case 'group': {
      const group = ctx.addGroup(op.name, { translation: (op.placement && op.placement.translation) || [0, 0, 0] });
      for (const inner of op.ops || []) run(group, inner);
      group.close();
      break;
    }
    case 'text': op.screen ? ctx.addScreenText(op.text, op.point, op.vector) : ctx.addText(op.text, op.point, op.vector); break;
    case 'dimension': ctx.addDimension(op.start, op.end, op.offset || 10, op.offsetVector || null, op.anchor || false); break;
    case 'guideLine': ctx.addGuideLine(op.point, op.point2 || null, op.direction || null); break;
    case 'guidePoint': ctx.addGuidePoint(op.position); break;
    case 'section': ctx.addSectionPlane(op.point, op.normal, { name: op.name, symbol: op.symbol, active: op.active || false }); break;
    case 'scene':
      ctx.addScene(op.name, op.eye, op.target, {
        perspective: op.perspective !== false, fov: op.fov || 35, orthoHeight: op.orthoHeight || 200,
        hiddenLayers: (op.hiddenLayers || []).map((name) => layers[name]),
      });
      break;
    case 'camera':
      ctx.setCamera(op.eye, op.target, { perspective: op.perspective !== false, fov: op.fov || 35, orthoHeight: op.orthoHeight || 200 });
      break;
    case 'modelAttributes': builder.setModelAttributes({ attributes: attrs(op.attributes) }); break;
    default: throw new Error(`unknown op ${op.op}`);
  }
}
for (const op of spec.ops) run(builder, op);
fs.writeFileSync(out, Buffer.from(builder.toBytes()));
console.log(`wrote ${out}`);
