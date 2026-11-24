"""Build the deterministic VFF golden models shared with the TypeScript tests."""
from __future__ import annotations
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / 'packages/python/src'))
from openskp.create import Length, Point3d, Timestamp, Vector3d
from openskp.create_vff import VffBuilder

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPEC = ROOT / 'packages/typescript/tests/fixtures/vff_writer/spec.json'
IMAGES = ROOT / 'packages/typescript/tests/fixtures/vff_writer'
OUT = ROOT / 'packages/typescript/tests/fixtures/vff_writer/golden'


def convert(value):
    if isinstance(value, dict) and '$type' in value:
        kind = value['$type']
        if kind == 'timestamp': return Timestamp(int(value['value']))
        if kind == 'length': return Length(float(value['value']))
        if kind == 'point': return Point3d(*[float(v) for v in value['value']])
        if kind == 'vector': return Vector3d(*[float(v) for v in value['value']])
    if isinstance(value, list): return [convert(v) for v in value]
    return value


def attrs(spec, name):
    data = spec.get('attributes')
    if name is None or data is None: return {}
    return {k: convert(v) for k, v in data.items()}


class Runner:
    def __init__(self, spec):
        self.spec = spec
        raw = spec['options']
        options = {
            'guid_seed': raw.get('guidSeed'),
            'model_guid': raw.get('modelGuid'),
            'save_time': raw.get('saveTime'),
            'definition_time': raw.get('definitionTime'),
            'portableTrig': raw.get('portableTrig'),
        }
        self.builder = VffBuilder(options={k: v for k, v in options.items() if v is not None})
        self.handles = {}
        self.materials = {}
        self.layers = {}
        for image, info in spec.get('textures', {}).items():
            self.materials[info['name']] = self.builder.add_texture_material(
                info['name'], (IMAGES / image).read_bytes(), info['width'], info['height'],
                average_color=info['averageColor'])
        for op in spec['ops']:
            self.run(self.builder, op)

    def run(self, ctx, op):
        kind = op['op']
        if kind == 'material':
            self.materials[op['name']] = self.builder.add_material(op['name'], op['rgba'], op.get('opacity'))
        elif kind == 'texture':
            pass
        elif kind == 'layer':
            self.layers[op['name']] = self.builder.add_layer(op['name'], op.get('color'), op.get('hidden', False))
        elif kind == 'face':
            ctx.add_face(op['points'], material=self.materials.get(op.get('material')),
                         layer=self.layers.get(op.get('layer')), back_material=self.materials.get(op.get('backMaterial')),
                         hidden=op.get('hidden', False), holes=op.get('holes', ()),
                         front_uv=[(tuple(p), tuple(uv)) for p, uv in op['frontUv']] if 'frontUv' in op else None,
                         back_uv=[(tuple(p), tuple(uv)) for p, uv in op['backUv']] if 'backUv' in op else None,
                         attributes=attrs(self.spec, op.get('attributes')))
        elif kind == 'circle':
            ctx.add_circle(op['center'], op['normal'], op['radius'], op.get('numSegments', 24),
                           material=self.materials.get(op.get('material')), layer=self.layers.get(op.get('layer')),
                           attributes=attrs(self.spec, op.get('attributes')))
        elif kind == 'arc':
            ctx.add_arc(op['center'], op['normal'], op['radius'], op['startAngle'], op['endAngle'], op.get('numSegments', 24))
        elif kind == 'polyline':
            ctx.add_polyline(op['points'], closed=op.get('closed', False))
        elif kind == 'definition':
            definition = self.builder.add_component_definition(op['name'], attributes=attrs(self.spec, op.get('attributes')))
            for inner in op.get('ops', []):
                self.run(definition, inner)
            definition.close()
            self.handles[op['name']] = definition
        elif kind == 'instance':
            ctx.add_instance(self.handles[op['definition']], translation=op.get('translation', (0, 0, 0)),
                             rotation=(op['rotation']['axis'], op['rotation']['angle']) if 'rotation' in op else None,
                             attributes=attrs(self.spec, op.get('attributes')))
        elif kind == 'group':
            group = ctx.add_group(op.get('name'), translation=op.get('placement', {}).get('translation', (0, 0, 0)))
            for inner in op.get('ops', []):
                self.run(group, inner)
            group.close()
        elif kind == 'text':
            if op.get('screen'):
                ctx.add_screen_text(op['text'], op['point'], op['vector'])
            else:
                ctx.add_text(op['text'], op['point'], op['vector'])
        elif kind == 'dimension':
            ctx.add_dimension(op['start'], op['end'], op.get('offset', 10), op.get('offsetVector'), op.get('anchor', False))
        elif kind == 'guideLine':
            ctx.add_guide_line(op['point'], op.get('point2'), op.get('direction'))
        elif kind == 'guidePoint':
            ctx.add_guide_point(op['position'])
        elif kind == 'section':
            ctx.add_section_plane(op['point'], op['normal'], name=op.get('name'), symbol=op.get('symbol'), active=op.get('active', False))
        elif kind == 'scene':
            ctx.add_scene(op['name'], op['eye'], op['target'], perspective=op.get('perspective', True),
                          fov=op.get('fov', 35), ortho_height=op.get('orthoHeight', 200),
                          hidden_layers=[self.layers[n] for n in op.get('hiddenLayers', [])])
        elif kind == 'camera':
            ctx.set_camera(op['eye'], op['target'], perspective=op.get('perspective', True),
                           fov=op.get('fov', 35), ortho_height=op.get('orthoHeight', 200))
        elif kind == 'modelAttributes':
            self.builder.set_model_attributes(attributes=attrs(self.spec, 'attrs'))
        else:
            raise ValueError(kind)

    def bytes(self):
        return self.builder.to_bytes()


def build(spec=None):
    spec = spec or json.loads(SPEC.read_text())
    return Runner(spec).bytes()


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    data = build()
    (OUT / 'all_features.skp').write_bytes(data)
    print('wrote', OUT / 'all_features.skp', len(data))
