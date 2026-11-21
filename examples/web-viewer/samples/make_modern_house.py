"""Builds samples/modern_house.skp with OpenSKP's own writer.

A small, two-volume house - a rendered living block and a taller timber-clad
studio - with real window/door openings (faces with holes plus reveal faces),
glazing, flat roofs with overhangs, a timber deck and a site plane. Every face
carries an explicit front AND back material, so no viewer ever has to fall
back to a default/layer colour.

Run from the repository root:

    python examples/web-viewer/samples/make_modern_house.py

Units are inches (SketchUp's internal unit).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "packages", "python", "src"))

from openskp import create  # noqa: E402

b = create()

# ---- materials -------------------------------------------------------------
PLASTER = b.add_material("White Plaster", (226, 222, 213))
TIMBER = b.add_material("Cedar Cladding", (146, 101, 64))
GLASS = b.add_material("Glass", (122, 154, 172))
FRAME = b.add_material("Anthracite Frame", (44, 46, 50))
ROOF = b.add_material("Roof Membrane", (70, 72, 76))
DOOR = b.add_material("Oak Door", (176, 132, 86))
DECK = b.add_material("Deck Boards", (128, 92, 62))
CONCRETE = b.add_material("Concrete", (178, 176, 170))
GRASS = b.add_material("Lawn", (104, 132, 84))

# ---- layers (SketchUp "tags") ------------------------------------------------
L_WALLS = b.add_layer("Walls", color=(226, 222, 213))
L_GLAZING = b.add_layer("Glazing", color=(122, 154, 172))
L_ROOF = b.add_layer("Roof", color=(70, 72, 76))
L_DECK = b.add_layer("Deck", color=(128, 92, 62))
L_SITE = b.add_layer("Site", color=(104, 132, 84))


def face(target, pts, mat, holes=None):
    if holes:
        target.add_face(pts, material=mat, back_material=mat, holes=holes)
    else:
        target.add_face(pts, material=mat, back_material=mat)


def box(target, x0, y0, z0, x1, y1, z1, mat):
    """Axis-aligned box: 6 faces."""
    face(target, [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)], mat)  # bottom
    face(target, [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], mat)  # top
    face(target, [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], mat)  # -y
    face(target, [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)], mat)  # +y
    face(target, [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)], mat)  # -x
    face(target, [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], mat)  # +x


def wall(target, axis, origin, length, thick, height, mat, openings=(), glass=None, frame=None):
    """A straight wall with rectangular openings.

    Built in local (u along the wall, v through it, z up) and mapped to world:
    axis 'x' runs along +X from origin, axis 'y' along +Y. Each opening is
    (u0, u1, z0, z1); z0 must be > 0 (doors get a small threshold) so every
    opening is a true interior hole. Openings get reveal faces through the
    wall's thickness and, when `glass` is given, a pane set in the middle.
    """
    ox, oy, oz = origin

    def w(u, v, z):
        return (ox + u, oy + v, oz + z) if axis == "x" else (ox + v, oy + u, oz + z)

    L, T, H = length, thick, height
    outer = [w(0, 0, 0), w(L, 0, 0), w(L, 0, H), w(0, 0, H)]
    inner = [w(L, T, 0), w(0, T, 0), w(0, T, H), w(L, T, H)]
    holes_o = [[w(a, 0, c), w(a, 0, d), w(bb, 0, d), w(bb, 0, c)] for a, bb, c, d in openings]
    holes_i = [[w(bb, T, c), w(bb, T, d), w(a, T, d), w(a, T, c)] for a, bb, c, d in openings]
    face(target, outer, mat, holes_o)
    face(target, inner, mat, holes_i)
    face(target, [w(0, 0, H), w(L, 0, H), w(L, T, H), w(0, T, H)], mat)  # top
    face(target, [w(0, T, 0), w(L, T, 0), w(L, 0, 0), w(0, 0, 0)], mat)  # bottom
    face(target, [w(0, T, 0), w(0, 0, 0), w(0, 0, H), w(0, T, H)], mat)  # start end
    face(target, [w(L, 0, 0), w(L, T, 0), w(L, T, H), w(L, 0, H)], mat)  # far end
    for a, bb, c, d in openings:
        face(target, [w(a, 0, c), w(bb, 0, c), w(bb, T, c), w(a, T, c)], mat)  # sill
        face(target, [w(a, T, d), w(bb, T, d), w(bb, 0, d), w(a, 0, d)], mat)  # head
        face(target, [w(a, T, c), w(a, T, d), w(a, 0, d), w(a, 0, c)], mat)  # jamb
        face(target, [w(bb, 0, c), w(bb, 0, d), w(bb, T, d), w(bb, T, c)], mat)  # jamb
        if glass is not None:
            m = T / 2
            face(target, [w(a, m, c), w(bb, m, c), w(bb, m, d), w(a, m, d)], glass)
            if frame is not None and (bb - a) > 60:
                # a slim vertical mullion on wide panes
                mu = (a + bb) / 2
                face(target, [w(mu - 1.2, m - 0.6, c), w(mu + 1.2, m - 0.6, c),
                              w(mu + 1.2, m - 0.6, d), w(mu - 1.2, m - 0.6, d)], frame)


# Dimensions (inches): living block 340 x 240 x 112; studio 170 x 200 x 150.
T = 9

# ---- component definitions (all declared before any root geometry) ----------
with b.add_component_definition("Living block") as living:
    wall(living, "x", (0, 0, 0), 340, T, 112, PLASTER,
         openings=[(30, 150, 26, 96), (190, 230, 3, 96), (260, 320, 26, 96)], glass=GLASS, frame=FRAME)
    wall(living, "x", (0, 240 - T, 0), 340, T, 112, PLASTER,
         openings=[(60, 140, 30, 90), (220, 300, 30, 90)], glass=GLASS, frame=FRAME)
    wall(living, "y", (0, T, 0), 240 - 2 * T, T, 112, PLASTER,
         openings=[(70, 150, 30, 90)], glass=GLASS, frame=FRAME)
    wall(living, "y", (340 - T, T, 0), 240 - 2 * T, T, 112, PLASTER)

with b.add_component_definition("Front door") as door:
    face(door, [(0, 0, 0), (40, 0, 0), (40, 0, 93), (0, 0, 93)], DOOR)
    box(door, 34, -1.5, 44, 35.5, 0, 52, FRAME)  # pull handle

with b.add_component_definition("Studio") as studio:
    wall(studio, "x", (0, 0, 0), 170, T, 150, TIMBER,
         openings=[(24, 146, 30, 132)], glass=GLASS, frame=FRAME)
    wall(studio, "x", (0, 200 - T, 0), 170, T, 150, TIMBER)
    wall(studio, "y", (0, T, 0), 200 - 2 * T, T, 150, TIMBER,
         openings=[(40, 80, 40, 120)], glass=GLASS)
    wall(studio, "y", (170 - T, T, 0), 200 - 2 * T, T, 150, TIMBER,
         openings=[(110, 160, 40, 120)], glass=GLASS)

with b.add_component_definition("Living roof") as roof_a:
    box(roof_a, -16, -16, 112, 356, 256, 122, ROOF)

with b.add_component_definition("Studio roof") as roof_b:
    box(roof_b, -14, -14, 150, 184, 214, 160, ROOF)

with b.add_component_definition("Deck") as deck:
    for i in range(12):
        y = -120 + i * 10.5
        box(deck, -10, y, 0.5, 180, y + 9.5, 4, DECK)
    box(deck, -10, -128, 0.5, 180, -120, 4, DECK)  # front nosing board

with b.add_component_definition("Site") as site:
    # At ground level (z=0) so a viewer's own ground grid sits under it, not
    # on top of it.
    face(site, [(-110, -250, 0), (600, -250, 0), (600, 330, 0), (-110, 330, 0)], GRASS)
    box(site, 186, -250, 0, 234, -128, 1.0, CONCRETE)  # path to the door

# ---- placements ----------------------------------------------------------------
b.add_instance(site, name="Site", layer=L_SITE)
b.add_instance(living, name="Living block", layer=L_WALLS)
b.add_instance(door, name="Front door", translation=(190, 4.5, 3), layer=L_WALLS)
b.add_instance(roof_a, name="Living roof", layer=L_ROOF)
b.add_instance(studio, name="Studio", translation=(340, 40, 0), layer=L_WALLS)
b.add_instance(roof_b, name="Studio roof", translation=(340, 40, 0), layer=L_ROOF)
b.add_instance(deck, name="Deck", layer=L_DECK)

out = os.path.join(HERE, "modern_house.skp")
b.save(out)
print("wrote", out, os.path.getsize(out), "bytes")
