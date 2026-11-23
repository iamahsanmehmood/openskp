"""Ensure openskp never writes to stdout or stderr unless logging is configured (#556)."""
from __future__ import annotations

import pathlib
import subprocess
import sys

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _patched_circle_extrusion_frag() -> bytes:
    from array import array
    from openskp.export import fragments
    from openskp.instanced_scene import InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive
    from openskp._fragments_fb import RepresentationClass
    from openskp._fragments_fb.Model import Model

    ident = (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    prim = LocalPrimitive(
        positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]),
        normals=array("f", [0.0] * 12),
        uvs=array("f", [0.0] * 8),
        indices=array("I", [0, 1, 2, 0, 2, 3]),
        material_index=0,
    )
    res = InstancedMeshResource(
        id="mesh_0",
        definition_id=1,
        definition_name="Box",
        variant_key="1|255,255,255",
        primitives=[prim],
    )
    leaf = InstancedNode(name="Box1", matrix=ident, mesh_resource_id="mesh_0")
    root = InstancedNode(name="ROOT", matrix=ident, children=[leaf])
    scene = InstancedScene(
        bounds=None,
        scene_hierarchy=root,
        mesh_resources=[res],
        gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0]}}],
        textures=[],
    )
    frag_raw = bytearray(fragments.to_fragments(scene, raw=True))
    fb_model = Model.GetRootAsModel(frag_raw, 0)
    rep = fb_model.Meshes().Representations(0)
    frag_raw[rep._tab.Pos + 28] = RepresentationClass.RepresentationClass.CIRCLE_EXTRUSION
    return bytes(frag_raw)


def test_warnings_produce_no_stdout_or_stderr_without_logging_configured(tmp_path):
    frag_path = tmp_path / "patched.frag"
    frag_path.write_bytes(_patched_circle_extrusion_frag())

    script = """
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from openskp import SkpFile
from openskp.export import fragments

fix = pathlib.Path(sys.argv[2])
frag_path = pathlib.Path(sys.argv[3])

# 1. Scene texture warning (TIFF texture skipped in glTF scene)
SkpFile.open(str(fix / "texture_tiff_2021.skp")).build_scene()

# 2. Fragments unsupported RepresentationClass warning
fragments.read(str(frag_path))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(SRC), str(FIXTURES), str(frag_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_warnings_still_reach_configured_logger():
    import logging
    from openskp.export import fragments

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("openskp")
    handler = _Capture(level=logging.WARNING)
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        fragments.from_fragments(_patched_circle_extrusion_frag())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    assert any("unsupported RepresentationClass" in r.getMessage() for r in records)
