"""GLB/glTF 2.0 export for parsed SketchUp models.

Uses the battle-tested core engine for scene construction and
trimesh for GLB serialization.

Example::

    from openskp import SkpFile
    from openskp.export import glb

    skp = SkpFile.open("model.skp")
    model = skp.parse()
    glb.export(skp, "output.glb")
"""

from __future__ import annotations

import os



def export(
    skp_file,
    output_path: str,
    *,
    coordinate_system: str = "y-up",
    units: str = "mm",
) -> str:
    """Export a parsed SkpFile to GLB (binary glTF 2.0) format.

    This function uses the proven core engine to build a trimesh scene
    with correct geometry, transformations, and layer colors, then
    serializes it to a GLB file.

    Args:
        skp_file: An :class:`~openskp.model.SkpFile` instance that has
            already been parsed (i.e., ``skp_file.parse()`` has been called).
        output_path: Filesystem path for the output ``.glb`` file.
        coordinate_system: Target coordinate system. Currently only
            ``"y-up"`` (glTF standard) is supported.
        units: Target unit system. Currently only ``"mm"`` is supported.

    Returns:
        Absolute path to the written GLB file.

    Raises:
        RuntimeError: If *skp_file* has not been parsed yet.
    """
    from .._core import build_scene

    if not hasattr(skp_file, '_parsed') or skp_file._parsed is None:
        raise RuntimeError(
            "SkpFile must be parsed before exporting. Call skp_file.parse() first."
        )

    output_dir = os.path.dirname(os.path.abspath(output_path))
    stem = os.path.splitext(os.path.basename(output_path))[0]

    result = build_scene(skp_file._parsed, output_dir, stem)
    return result['glb_path']
