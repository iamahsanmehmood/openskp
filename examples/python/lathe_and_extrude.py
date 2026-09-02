#!/usr/bin/env python3
"""
OpenSKP — Lathe (Revolve) and Extrude Helpers

Two small, dependency-free helpers on top of openskp.create()'s raw
add_face() API: revolve() builds a solid of revolution around Z from a
2D (radius, z) profile (columns, vases, balusters, fountain tiers),
and extrude() sweeps a 2D outline into a straight prism.

Why these exist: an AI agent (or a human) writing openskp.create() code
by hand for a turned/revolved shape ends up hand-rolling ~40 lines of
ring-of-quads math per part, with no shared, tested code path — easy to
get wrong (degenerate faces, missing end caps, faceted/unsmoothed
shading) and expensive to redo for every similar part. These two
functions collapse that to one call, e.g.:

    revolve(target, [(0.3, 0.0), (0.2, 0.5), (0.35, 0.8)])   # a baluster

This does not change OpenSKP's own writer API — it is example code
built entirely on the existing public add_face()/add_group() surface,
included here as a starting point for anyone (agent or human) modeling
turned/extruded shapes. See docs/AI_MODELING.md for the broader context.

Originally written by IngeTrazo (github.com/iamahsanmehmood — the
downstream .skp-editing app that uses this project's writer) as a CC0
gift after finding the same ring-of-quads pattern recurring across
AI-generated recipes in their own AI Assistant; adapted here as an
OpenSKP example.

Profiles are (radius, z) in METERS, bottom to top, converted to the
inches openskp.create() expects. Run this file directly to produce
fountain.skp — a three-tier garden fountain in seven revolve()/extrude()
calls, modeled after a real reference photo.
"""

import math

import openskp

INCH_PER_METER = 39.3701


def revolve(target, profile, segments=32, scallop=None, closed=False, material=None):
    """Build a solid of revolution around Z on ``target`` (an SkpBuilder
    or a group/component-definition builder — anything with add_face()).

    ``profile`` is a list of (radius, z) points in meters, bottom to top.
    An open profile (the common case) gets flat end caps at its first
    and last ring, unless that ring's radius is ~0 (a point, needing no
    cap). ``closed=True`` instead revolves the profile as a closed
    cross-section ring with no caps at all — for a shape like a basin
    wall, whose profile already returns to its own start.

    ``scallop=(depth, lobes)`` carves festoons into the rim: each ring's
    radius is perturbed by a cosine wave with ``lobes`` repetitions and
    ``depth`` meters of amplitude, scaled by that ring's own radius
    relative to the profile's largest radius — so the effect is
    strongest at the widest ring and fades toward the axis, rather than
    displacing every ring by the same amount regardless of size.

    Side faces are marked soft+smooth so the curved surface shades
    smoothly in SketchUp instead of showing each individual segment.
    """
    amp, lobes = scallop or (0.0, 0)
    r_max = max(r for r, _z in profile) or 1.0
    rings = []
    for r, z in profile:
        ring = []
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            rr = r
            if lobes:
                rr += amp * 0.5 * (math.cos(lobes * theta) - 1.0) * (r / r_max)
            ring.append((
                rr * math.cos(theta) * INCH_PER_METER,
                rr * math.sin(theta) * INCH_PER_METER,
                z * INCH_PER_METER,
            ))
        rings.append(ring)

    row_count = len(rings)
    for j in range(row_count if closed else row_count - 1):
        lower, upper = rings[j], rings[(j + 1) % row_count]
        for i in range(segments):
            i2 = (i + 1) % segments
            quad = [lower[i], lower[i2], upper[i2], upper[i]]
            if _is_degenerate(quad):
                continue
            target.add_face(quad, material=material, soft_edges=True, smooth_edges=True)

    if not closed:
        if profile[0][0] > 1e-6:
            target.add_face(list(reversed(rings[0])), material=material)
        if profile[-1][0] > 1e-6:
            target.add_face(rings[-1], material=material)


def extrude(target, outline, z0, z1, material=None):
    """Build a straight prism on ``target`` by sweeping a 2D (x, y)
    ``outline`` (meters) from height ``z0`` up to ``z1`` (also meters)."""
    lower = [(x * INCH_PER_METER, y * INCH_PER_METER, z0 * INCH_PER_METER) for x, y in outline]
    upper = [(x * INCH_PER_METER, y * INCH_PER_METER, z1 * INCH_PER_METER) for x, y in outline]
    n = len(lower)
    for i in range(n):
        j = (i + 1) % n
        target.add_face([lower[i], lower[j], upper[j], upper[i]], material=material)
    target.add_face(list(reversed(lower)), material=material)
    target.add_face(upper, material=material)


def _is_degenerate(quad):
    def close(p, q):
        return abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) < 1e-6
    return close(quad[0], quad[3]) and close(quad[1], quad[2])


if __name__ == "__main__":
    builder = openskp.create()
    stone = builder.add_material("Stone", (140, 138, 133))

    octagon = [
        (0.39 * math.cos(a), 0.39 * math.sin(a))
        for a in (math.pi / 8 + i * math.pi / 4 for i in range(8))
    ]
    with builder.add_group("Pedestal") as g:
        extrude(g, octagon, 0.0, 0.45, material=stone)
    with builder.add_group("Baluster") as g:
        revolve(g, [(0.34, 0.45), (0.26, 0.52), (0.29, 0.62), (0.19, 0.86),
                    (0.21, 1.00), (0.15, 1.05)], material=stone)
    with builder.add_group("Big plate") as g:
        revolve(g, [(0.12, 1.05), (0.66, 1.20), (0.75, 1.25), (0.75, 1.30),
                    (0.45, 1.24), (0.12, 1.18)],
                closed=True, scallop=(0.03, 12), material=stone)
    with builder.add_group("Upper shaft") as g:
        revolve(g, [(0.16, 1.17), (0.13, 1.24), (0.15, 1.34), (0.10, 1.58),
                    (0.13, 1.64), (0.09, 1.72)], material=stone)
    with builder.add_group("Small plate") as g:
        revolve(g, [(0.09, 1.72), (0.44, 1.83), (0.50, 1.865), (0.50, 1.90),
                    (0.28, 1.85), (0.09, 1.81)],
                closed=True, scallop=(0.02, 10), material=stone)
    with builder.add_group("Finial") as g:
        revolve(g, [(0.09, 1.80), (0.16, 1.98), (0.17, 2.08), (0.14, 2.18),
                    (0.025, 2.30)], scallop=(0.015, 8), material=stone)
    with builder.add_group("Basin") as g:
        revolve(g, [(1.50, 0.00), (1.80, 0.00), (1.88, 0.16), (1.82, 0.44),
                    (1.94, 0.53), (1.96, 0.60), (1.88, 0.64), (1.50, 0.64)],
                closed=True, scallop=(0.025, 16), material=stone)

    builder.save("fountain.skp")
    print("wrote fountain.skp")
