"""A component instance's own paint and tag in classic (pre-2021) files.

The scene builders read both from the instance's D007 record, which only
2021+ files have, so in a classic file a painted component came out in its
tag's colour and every instance sat on its parent's tag. SketchUp's own library reports
the paint of an instance written this way (SUDrawingElementGetMaterial).
"""
from __future__ import annotations

from openskp import SkpFile, create

SQUARE = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]


def _save(builder, tmp_path):
    path = tmp_path / "model.skp"
    path.write_bytes(builder.to_bytes())
    return SkpFile.open(str(path))


def _primitives(built):
    if hasattr(built, "glb_primitives"):
        return built.glb_primitives
    return [p for r in built.mesh_resources for p in r.primitives]


def _painted_model(tmp_path):
    builder = create()
    red = builder.add_material("Red", (200, 10, 10, 255))
    blue = builder.add_material("Blue", (10, 10, 200, 128))
    with builder.add_component_definition("Leaf") as leaf:
        leaf.add_face(SQUARE)
    with builder.add_component_definition("Outer") as outer:
        outer.add_instance(leaf, name="InheritsPaint")
        outer.add_instance(leaf, name="OwnPaint", material=blue, translation=(20, 0, 0))
    builder.add_instance(outer, name="Painted", material=red)
    return _save(builder, tmp_path)


def test_paint_is_applied_and_inherited_by_nested_components(tmp_path):
    skp = _painted_model(tmp_path)
    for built in (skp.build_scene(), skp.build_instanced_scene()):
        materials = {
            (m.get("name"), tuple(m["pbrMetallicRoughness"]["baseColorFactor"][:3]), bool(m.get("doubleSided")))
            for m in (built.gltf_materials[p.material_index] for p in _primitives(built))
        }
        assert materials == {
            ("Red", (200 / 255, 10 / 255, 10 / 255), True),
            ("Blue", (10 / 255, 10 / 255, 200 / 255), True),
        }
        # the paint is inherited whole, so the translucent one stays translucent
        (blue,) = [m for m in built.gltf_materials if m.get("name") == "Blue"]
        assert blue["alphaMode"] == "BLEND"


def test_each_paint_is_its_own_instanced_mesh(tmp_path):
    scene = _painted_model(tmp_path).build_instanced_scene()
    nodes = {n.name: n for n in scene.scene_hierarchy.children[0].children}
    assert nodes["InheritsPaint"].mesh_resource_id != nodes["OwnPaint"].mesh_resource_id


def test_tag_is_the_instances_own(tmp_path):
    builder = create()
    walls = builder.add_layer("Walls")
    with builder.add_component_definition("Part") as part:
        part.add_face(SQUARE)
    builder.add_instance(part, name="Tagged", layer=walls)
    builder.add_instance(part, name="Untagged", translation=(20, 0, 0))
    skp = _save(builder, tmp_path)
    for built in (skp.build_scene(), skp.build_instanced_scene()):
        layers = {n.name: n.layer for n in built.scene_hierarchy.children}
        assert layers == {"Tagged": "Walls", "Untagged": "Layer0"}


# Minimal valid 1x1 and 2x1 RGB PNGs for writer-made texture tests (#559).
_PNG_1X1 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x90, 0x77, 0x53, 0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x78,
    0x9C, 0x63, 0x38, 0x91, 0x62, 0x04, 0x00, 0x03, 0x56, 0x01, 0x5F, 0xE8, 0x17, 0x84,
    0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
])

_PNG_2X1 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x7B, 0x40, 0xE8, 0xDD, 0x00, 0x00, 0x00, 0x0F, 0x49, 0x44, 0x41, 0x54, 0x78,
    0x9C, 0x63, 0x30, 0x4A, 0x39, 0x61, 0x94, 0x72, 0x02, 0x00, 0x08, 0x6D, 0x02, 0xBD,
    0xEB, 0xAE, 0x9B, 0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42,
    0x60, 0x82,
])


def _uv_bounds(uvs):
    us = [uvs[i] for i in range(0, len(uvs), 2)]
    vs = [uvs[i] for i in range(1, len(uvs), 2)]
    return (round(min(us), 4), round(max(us), 4), round(min(vs), 4), round(max(vs), 4))


def test_sdk_classic_and_vff_textured_painted_instances_and_tags():
    import pathlib

    fixtures = pathlib.Path(__file__).parent / "fixtures"
    for fname in ("instance_textured_paint_2020.skp", "instance_textured_paint_2021.skp"):
        skp = SkpFile.open(str(fixtures / fname))
        baked = skp.build_scene()
        instanced = skp.build_instanced_scene()

        assert len(baked.textures) == 2
        assert len(instanced.textures) == 2
        assert len(baked.glb_primitives) == 10
        assert len(instanced.mesh_resources) == 6

        for built in (baked, instanced):
            top = {n.name: n for n in built.scene_hierarchy.children}
            assert {k: v.layer for k, v in top.items()} == {
                "BrickPanel": "ExteriorTag",
                "WoodPanel": "InteriorTag",
                "PlainPanel": "Layer0",
                "BrickGroup": "ExteriorTag",
                "OuterInst": "ExteriorTag",
            }
            nested = {n.name: n.layer for n in top["OuterInst"].children}
            assert nested == {"ChildInherits": "ExteriorTag", "ChildOverrides": "InteriorTag"}

        by_id = {r.id: r for r in instanced.mesh_resources}
        top_inst = {n.name: n for n in instanced.scene_hierarchy.children}
        nested_inst = {n.name: n for n in top_inst["OuterInst"].children}

        def tex_prims(node):
            res = by_id[node.mesh_resource_id]
            out = []
            for p in res.primitives:
                m = instanced.gltf_materials[p.material_index]
                pbr = m["pbrMetallicRoughness"]
                out.append((
                    pbr.get("baseColorTexture", {}).get("index"),
                    round(pbr["baseColorFactor"][3], 4),
                    _uv_bounds(p.uvs),
                ))
            return sorted(out, key=lambda x: (-1 if x[0] is None else x[0], x[1], x[2]))

        brick_prims = tex_prims(top_inst["BrickPanel"])
        wood_prims = tex_prims(top_inst["WoodPanel"])
        assert len(brick_prims) == 2
        assert len(wood_prims) == 2
        # Unpainted 10x20 face inherits BrickTex (10x20 -> UVs 0..1) vs WoodTex (4x8 -> UVs 0..2.5),
        # while GlassSolid face keeps alpha=0.5 untextured.
        assert brick_prims[0] == (None, 0.5, (0.0, 10.0, 25.0, 45.0))
        assert wood_prims[0] == (None, 0.5, (0.0, 10.0, 25.0, 45.0))
        brick_tex_idx = brick_prims[1][0]
        wood_tex_idx = wood_prims[1][0]
        assert brick_tex_idx is not None and wood_tex_idx is not None and brick_tex_idx != wood_tex_idx
        assert brick_prims[1] == (brick_tex_idx, 1.0, (0.0, 1.0, 0.0, 1.0))
        assert wood_prims[1] == (wood_tex_idx, 1.0, (0.0, 2.5, 0.0, 2.5))
        assert tex_prims(top_inst["BrickGroup"]) == [(brick_tex_idx, 1.0, (0.0, 1.0, 0.0, 1.0))]
        assert tex_prims(nested_inst["ChildInherits"]) == [(brick_tex_idx, 1.0, (0.0, 1.0, 0.0, 1.0))]
        assert tex_prims(nested_inst["ChildOverrides"]) == [(wood_tex_idx, 1.0, (0.0, 2.5, 0.0, 2.5))]


def test_writer_unnamed_and_named_textured_materials_on_instances_and_groups(tmp_path):
    from openskp import create_vff

    tex1 = tmp_path / "a.png"
    tex2 = tmp_path / "b.png"
    tex1.write_bytes(_PNG_1X1)
    tex2.write_bytes(_PNG_2X1)

    rect = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 20.0, 0.0), (0.0, 20.0, 0.0)]
    for is_vff, factory in ((False, create), (True, create_vff)):
        b = factory()
        named_mat = b.add_texture_material("Brick", str(tex1), 10.0, 20.0)
        unnamed_mat = b.add_texture_material("", str(tex2), 4.0, 8.0)
        ext = b.add_layer("Ext")
        inter = b.add_layer("Int")
        with b.add_component_definition("Panel") as panel:
            panel.add_face(rect)
        with b.add_group(name="PaintedGroup", translation=(40.0, 0.0, 0.0), material=named_mat, layer=ext) as grp:
            grp.add_face(rect)
        b.add_instance(panel, name="NamedInst", material=named_mat, layer=ext)
        b.add_instance(panel, name="UnnamedInst", translation=(20.0, 0.0, 0.0), material=unnamed_mat, layer=inter)

        out_skp = tmp_path / ("vff.skp" if is_vff else "classic.skp")
        out_skp.write_bytes(b.to_bytes())
        skp = SkpFile.open(str(out_skp))
        baked = skp.build_scene()
        instanced = skp.build_instanced_scene()
        assert len(baked.textures) == 2
        assert len(instanced.textures) == 2
        for built in (baked, instanced):
            layers = {n.name: n.layer for n in built.scene_hierarchy.children}
            assert layers == {"PaintedGroup": "Ext", "NamedInst": "Ext", "UnnamedInst": "Int"}

        by_id = {r.id: r for r in instanced.mesh_resources}
        nodes = {n.name: n for n in instanced.scene_hierarchy.children}
        assert nodes["NamedInst"].mesh_resource_id != nodes["UnnamedInst"].mesh_resource_id
        assert _uv_bounds(by_id[nodes["NamedInst"].mesh_resource_id].primitives[0].uvs) == (0.0, 1.0, 0.0, 1.0)
        assert _uv_bounds(by_id[nodes["UnnamedInst"].mesh_resource_id].primitives[0].uvs) == (0.0, 2.5, 0.0, 2.5)
        assert _uv_bounds(by_id[nodes["PaintedGroup"].mesh_resource_id].primitives[0].uvs) == (0.0, 1.0, 0.0, 1.0)


def test_instanced_scene_isolates_same_name_same_rgb_inherited_material_variants(tmp_path):
    import io
    import pathlib
    import re
    import zipfile

    raw = (pathlib.Path(__file__).parent / "fixtures" / "instance_textured_paint_2021.skp").read_bytes()
    pk_pos = raw.index(b"PK\x03\x04")
    prefix = raw[:pk_pos]

    # Variant 1: BrickTex and WoodTex given identical XML name="Shared" and RGB (170, 58, 40),
    # differing only in texture image bytes and tile scale (10x20 vs 4x8).
    out1 = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw[pk_pos:]), "r") as zin, zipfile.ZipFile(out1, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in ("materials/BrickTex/material.xml", "materials/WoodTex/material.xml"):
                txt = data.decode("utf-8")
                txt = txt.replace('name="BrickTex"', 'name="Shared"').replace('name="WoodTex"', 'name="Shared"')
                txt = re.sub(r'colorRed="\d+"', 'colorRed="170"', txt)
                txt = re.sub(r'colorGreen="\d+"', 'colorGreen="58"', txt)
                txt = re.sub(r'colorBlue="\d+"', 'colorBlue="40"', txt)
                data = txt.encode("utf-8")
            zout.writestr(item, data)

    skp1 = tmp_path / "var1.skp"
    skp1.write_bytes(prefix + out1.getvalue())
    inst1 = SkpFile.open(str(skp1)).build_instanced_scene()
    by_id1 = {r.id: r for r in inst1.mesh_resources}
    top1 = {n.name: n for n in inst1.scene_hierarchy.children}
    assert top1["BrickPanel"].mesh_resource_id != top1["WoodPanel"].mesh_resource_id
    nested1 = {n.name: n for n in top1["OuterInst"].children}
    assert nested1["ChildInherits"].mesh_resource_id != nested1["ChildOverrides"].mesh_resource_id
    assert _uv_bounds(by_id1[nested1["ChildInherits"].mesh_resource_id].primitives[0].uvs) == (0.0, 1.0, 0.0, 1.0)
    assert _uv_bounds(by_id1[nested1["ChildOverrides"].mesh_resource_id].primitives[0].uvs) == (0.0, 2.5, 0.0, 2.5)

    # Variant 2: identical name="Shared", RGB (170, 58, 40), texture image, and 10x20 scale,
    # differing only in transparency (opaque 1.0 vs translucent 0.5).
    out2 = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw[pk_pos:]), "r") as zin, zipfile.ZipFile(out2, "w", zipfile.ZIP_DEFLATED) as zout:
        brick_xml = zin.read("materials/BrickTex/material.xml").decode("utf-8")
        brick_xml = brick_xml.replace('name="BrickTex"', 'name="Shared"')
        brick_xml = re.sub(r'colorRed="\d+"', 'colorRed="170"', brick_xml)
        brick_xml = re.sub(r'colorGreen="\d+"', 'colorGreen="58"', brick_xml)
        brick_xml = re.sub(r'colorBlue="\d+"', 'colorBlue="40"', brick_xml)
        wood_trans_xml = re.sub(r'useTrans="\d+"', 'useTrans="1"', brick_xml)
        wood_trans_xml = re.sub(r'trans="[^"]+"', 'trans="0.5"', wood_trans_xml)
        if 'useTrans=' not in wood_trans_xml:
            wood_trans_xml = wood_trans_xml.replace('name="Shared"', 'name="Shared" useTrans="1" trans="0.5"')
        brick_img = next(
            zin.read(n) for n in zin.namelist()
            if n.startswith("materials/BrickTex/") and not n.endswith(".xml")
        )
        for item in zin.infolist():
            if item.filename == "materials/BrickTex/material.xml":
                zout.writestr(item, brick_xml.encode("utf-8"))
            elif item.filename == "materials/WoodTex/material.xml":
                zout.writestr(item, wood_trans_xml.encode("utf-8"))
            elif item.filename.startswith("materials/WoodTex/") and not item.filename.endswith(".xml"):
                zout.writestr(item, brick_img)
            else:
                zout.writestr(item, zin.read(item.filename))

    skp2 = tmp_path / "var2.skp"
    skp2.write_bytes(prefix + out2.getvalue())
    inst2 = SkpFile.open(str(skp2)).build_instanced_scene()
    by_id2 = {r.id: r for r in inst2.mesh_resources}
    nested2 = {
        n.name: n
        for n in {c.name: c for c in inst2.scene_hierarchy.children}["OuterInst"].children
    }
    assert nested2["ChildInherits"].mesh_resource_id != nested2["ChildOverrides"].mesh_resource_id
    alpha_inh = inst2.gltf_materials[by_id2[nested2["ChildInherits"].mesh_resource_id].primitives[0].material_index]["pbrMetallicRoughness"]["baseColorFactor"][3]
    alpha_ovr = inst2.gltf_materials[by_id2[nested2["ChildOverrides"].mesh_resource_id].primitives[0].material_index]["pbrMetallicRoughness"]["baseColorFactor"][3]
    assert round(alpha_inh, 4) == 1.0
    assert round(alpha_ovr, 4) == 0.5


_PNG_BRICK_4X4 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x08, 0x02, 0x00, 0x00, 0x00, 0x26, 0x93, 0x09,
    0x29, 0x00, 0x00, 0x00, 0x18, 0x49, 0x44, 0x41, 0x54, 0x78, 0xDA, 0x63, 0xD8, 0xE0, 0x62, 0x00,
    0x41, 0x97, 0xCE, 0x1E, 0x66, 0x80, 0x50, 0x10, 0x3E, 0x03, 0x4E, 0x19, 0x00, 0x5A, 0xCA, 0x17,
    0x39, 0xF4, 0x43, 0xAB, 0x79, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60,
    0x82,
])


def _build_ground_truth_writer_skp(tmp_path, vff: bool) -> SkpFile:
    from openskp import create_vff

    brick_png = tmp_path / "brick.png"
    brick_png.write_bytes(_PNG_BRICK_4X4)
    b = create_vff() if vff else create()
    plain_red = b.add_material("PlainRed", (210, 50, 40, 255))
    brick_tex = b.add_texture_material(
        "BrickTex", str(brick_png), applied_height=20.0, applied_width=10.0, filename="brick.png"
    )
    own_gold = b.add_material("OwnGold", (230, 180, 30, 255))
    tag = b.add_layer("PaintedTag", color=(40, 120, 200))

    f_unp = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 20.0, 0.0), (0.0, 20.0, 0.0)]
    f_own = [(0.0, 30.0, 0.0), (10.0, 30.0, 0.0), (10.0, 50.0, 0.0), (0.0, 50.0, 0.0)]

    with b.add_component_definition("PanelDef") as panel:
        panel.add_face(f_unp)
        panel.add_face(f_own, material=own_gold, back_material=own_gold)
    with b.add_component_definition("OuterDef") as outer:
        outer.add_instance(panel, name="InnerUnpainted", translation=(5.0, 0.0, 0.0))
    with b.add_group(name="PlainGroup", translation=(60.0, 0.0, 0.0), material=plain_red, layer=tag) as g1:
        g1.add_face(f_unp)
        g1.add_face(f_own, material=own_gold, back_material=own_gold)
    with b.add_group(name="TexturedGroup", translation=(90.0, 0.0, 0.0), material=brick_tex, layer=tag) as g2:
        g2.add_face(f_unp)
        g2.add_face(f_own, material=own_gold, back_material=own_gold)

    b.add_instance(panel, name="PlainComp", translation=(0.0, 0.0, 0.0), material=plain_red, layer=tag)
    b.add_instance(panel, name="TexturedComp", translation=(30.0, 0.0, 0.0), material=brick_tex, layer=tag)
    b.add_instance(outer, name="OuterTextured", translation=(120.0, 0.0, 0.0), material=brick_tex, layer=tag)

    out_path = tmp_path / ("gt_writer_vff.skp" if vff else "gt_writer_classic.skp")
    out_path.write_bytes(b.to_bytes())
    return SkpFile.open(str(out_path))


def test_sdk_2017_2020_2021_and_writer_ground_truth_scene_paint_and_tags(tmp_path):
    import pathlib

    fixtures = pathlib.Path(__file__).parent / "fixtures"
    sources = [
        ("2017", SkpFile.open(str(fixtures / "sdk_scene_instance_paint_2017.skp"))),
        ("2020", SkpFile.open(str(fixtures / "sdk_scene_instance_paint_2020.skp"))),
        ("2021", SkpFile.open(str(fixtures / "sdk_scene_instance_paint_2021.skp"))),
        ("writer", _build_ground_truth_writer_skp(tmp_path, vff=False)),
        ("writer_vff", _build_ground_truth_writer_skp(tmp_path, vff=True)),
    ]

    expected_parse = {
        "PlainComp": (False, "PaintedTag", "PlainRed", False),
        "TexturedComp": (False, "PaintedTag", "BrickTex", True),
        "PlainGroup": (True, "PaintedTag", "PlainRed", False),
        "TexturedGroup": (True, "PaintedTag", "BrickTex", True),
        "OuterTextured": (False, "PaintedTag", "BrickTex", True),
        "OuterTextured / InnerUnpainted": (False, "", None, False),
    }
    expected_node_layers = {k: "PaintedTag" for k in expected_parse}
    plain_parts = [
        ("OwnGold", (230, 180, 30, 255), False, (0.0, 10.0, 30.0, 50.0)),
        ("PlainRed", (210, 50, 40, 255), False, (0.0, 10.0, 0.0, 20.0)),
    ]

    for label, skp in sources:
        brick_rgba = (255, 255, 255, 255) if label == "writer" else (184, 102, 84, 255)
        textured_parts = [
            ("BrickTex", brick_rgba, True, (0.0, 1.0, 0.0, 1.0)),
            ("OwnGold", (230, 180, 30, 255), False, (0.0, 10.0, 30.0, 50.0)),
        ]
        expected_leaf_parts = {
            "PlainComp": plain_parts,
            "PlainGroup": plain_parts,
            "TexturedComp": textured_parts,
            "TexturedGroup": textured_parts,
            "OuterTextured / InnerUnpainted": textured_parts,
        }
        model = skp.parse()
        parsed_nodes = {}

        def walk_model(defn, prefix=""):
            for ins in defn.instances:
                cdef = model.definitions[ins.ref_idx]
                path = f"{prefix} / {ins.name}" if prefix else ins.name
                mat = model.materials_by_id.get(ins.material_id) if ins.material_id is not None else None
                parsed_nodes[path] = (
                    cdef.is_group,
                    ins.layer,
                    mat.name if mat else None,
                    bool(mat and mat.texture and mat.texture.data),
                )
                walk_model(cdef, path)

        walk_model(model.root)
        assert parsed_nodes == expected_parse, label

        baked = skp.build_scene()
        instanced = skp.build_instanced_scene()
        assert len(baked.textures) == 1, label
        assert len(instanced.textures) == 1, label
        assert len(baked.glb_primitives) == 10, label
        assert len(instanced.mesh_resources) == 4, label

        def collect_layers(node, prefix=""):
            out = {}
            for ch in node.children:
                p = f"{prefix} / {ch.name}" if prefix else ch.name
                out[p] = ch.layer
                out.update(collect_layers(ch, p))
            return out

        assert collect_layers(baked.scene_hierarchy) == expected_node_layers, label
        assert collect_layers(instanced.scene_hierarchy) == expected_node_layers, label

        def part_tuple(gm, uvs):
            pbr = gm["pbrMetallicRoughness"]
            bcf = pbr["baseColorFactor"]
            rgba = tuple(int(round(c * 255)) for c in bcf)
            has_tex = pbr.get("baseColorTexture", {}).get("index") == 0
            assert gm.get("doubleSided") is True
            return (gm.get("name", ""), rgba, has_tex, _uv_bounds(uvs))

        baked_by_path = {}
        for p in baked.glb_primitives:
            meta = baked.mesh_index[p.geom_name]
            assert meta.layer == "PaintedTag", label
            rel = meta.path[7:] if meta.path.startswith("ROOT / ") else meta.path
            baked_by_path.setdefault(rel, []).append(part_tuple(baked.gltf_materials[p.material_index], p.uvs))
        for k in baked_by_path:
            baked_by_path[k].sort()
        assert baked_by_path == expected_leaf_parts, label

        by_id = {r.id: r for r in instanced.mesh_resources}
        inst_nodes = {}

        def walk_inst(node, prefix=""):
            for ch in node.children:
                p = f"{prefix} / {ch.name}" if prefix else ch.name
                inst_nodes[p] = ch
                walk_inst(ch, p)

        walk_inst(instanced.scene_hierarchy)
        assert inst_nodes["OuterTextured"].mesh_resource_id is None, label
        assert (
            inst_nodes["TexturedComp"].mesh_resource_id
            == inst_nodes["OuterTextured / InnerUnpainted"].mesh_resource_id
        ), label
        assert inst_nodes["PlainComp"].mesh_resource_id != inst_nodes["TexturedComp"].mesh_resource_id, label

        inst_by_path = {}
        for path, exp in expected_leaf_parts.items():
            res = by_id[inst_nodes[path].mesh_resource_id]
            inst_by_path[path] = sorted(
                part_tuple(instanced.gltf_materials[p.material_index], p.uvs) for p in res.primitives
            )
        assert inst_by_path == expected_leaf_parts, label

