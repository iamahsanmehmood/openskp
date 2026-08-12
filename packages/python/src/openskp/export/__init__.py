"""Export sub-package for OpenSKP.

Provides exporters for converting a parsed :class:`~openskp.model.SkpModel`
into various output formats:

* :mod:`openskp.export.glb` — GLB / glTF 2.0 binary.
* :mod:`openskp.export.obj` — Wavefront OBJ text.
* :mod:`openskp.export.json_export` — Full metadata JSON.

Example::

    from openskp import SkpFile
    from openskp.export import glb, obj, json_export

    skp = SkpFile.open("model.skp")
    model = skp.parse()
    scene = skp.build_scene()

    glb.export(skp, "output.glb")
    obj.export(scene, "output.obj")
    json_export.export(model, "output.json", scene=scene)
"""

from __future__ import annotations

from . import dxf, glb, ifc, json_export, obj, ply, stl

__all__ = ["dxf", "glb", "ifc", "json_export", "obj", "ply", "stl"]
