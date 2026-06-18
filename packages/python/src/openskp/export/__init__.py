"""Export sub-package for OpenSKP.

Provides exporters for converting a parsed :class:`~openskp.model.SkpModel`
into various output formats:

* :mod:`openskp.export.glb` — GLB / glTF 2.0 binary.
* :mod:`openskp.export.obj` — Wavefront OBJ text.
* :mod:`openskp.export.json_export` — Full metadata JSON.

Example::

    from openskp.export import glb, obj, json_export

    glb.export(model, "output.glb")
    obj.export(model, "output.obj")
    json_export.export(model, "output.json")
"""

from __future__ import annotations
