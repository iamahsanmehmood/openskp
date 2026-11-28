"""Write a parametric bookshelf with OpenSKP's SketchUp writer.

Install with `pip install openskp`, then run `python bookshelf.py` to write
bookshelf.skp in the current directory. All dimensions are in inches.

For example, to make a taller bookshelf with five shelves:

    python bookshelf.py --height 60 --shelves 5 --output tall-bookshelf.skp
"""

import argparse
import math

import openskp


def add_board(component, width, depth, height, material):
    """Add a rectangular board with outward-facing faces at the origin."""
    corners = [
        (0, 0, 0),
        (width, 0, 0),
        (width, depth, 0),
        (0, depth, 0),
        (0, 0, height),
        (width, 0, height),
        (width, depth, height),
        (0, depth, height),
    ]
    faces = [
        (3, 2, 1, 0),  # Bottom
        (4, 5, 6, 7),  # Top
        (0, 1, 5, 4),  # Front
        (1, 2, 6, 5),  # Right
        (2, 3, 7, 6),  # Back
        (3, 0, 4, 7),  # Left
    ]
    for face in faces:
        component.add_face(
            [corners[index] for index in face],
            material=material,
            back_material=material,
        )


def build_bookshelf(width, height, depth, shelves, thickness):
    """Build an open-top bookshelf with evenly spaced shelves."""
    if any(not math.isfinite(value) or value <= 0 for value in (width, height, depth, thickness)):
        raise ValueError("width, height, depth and thickness must be positive and finite")
    if width <= 2 * thickness:
        raise ValueError("width must be greater than twice the board thickness")
    if shelves < 1:
        raise ValueError("shelves must be at least 1")
    shelf_spacing = height / shelves
    if shelf_spacing <= thickness:
        raise ValueError("height must leave clearance above each shelf")

    builder = openskp.create()
    wood = builder.add_material("Wood", (163, 110, 69))
    shelf_tag = builder.add_layer("Shelves")

    # Define each board once before placing any instances in the model.
    with builder.add_component_definition("Side Panel") as side_panel:
        add_board(side_panel, thickness, depth, height, wood)
    with builder.add_component_definition("Shelf") as shelf:
        add_board(shelf, width - 2 * thickness, depth, thickness, wood)

    builder.add_instance(side_panel, name="Left Side")
    builder.add_instance(side_panel, name="Right Side", translation=(width - thickness, 0, 0))
    for index in range(shelves):
        # Tag instances to toggle the shelves together and leave their faces untagged.
        builder.add_instance(
            shelf,
            name=f"Shelf {index + 1}",
            translation=(thickness, 0, index * shelf_spacing),
            layer=shelf_tag,
        )
    return builder


def main():
    parser = argparse.ArgumentParser(description="Write a bookshelf SKP model. Dimensions are in inches.")
    parser.add_argument("--width", type=float, default=30.0, help="outside width (default: 30)")
    parser.add_argument("--height", type=float, default=48.0, help="outside height (default: 48)")
    parser.add_argument("--depth", type=float, default=12.0, help="outside depth (default: 12)")
    parser.add_argument("--shelves", type=int, default=4, help="number of shelves, including the bottom (default: 4)")
    parser.add_argument("--thickness", type=float, default=0.75, help="board thickness (default: 0.75)")
    parser.add_argument("--output", default="bookshelf.skp", help="output file (default: bookshelf.skp)")
    args = parser.parse_args()

    try:
        builder = build_bookshelf(args.width, args.height, args.depth, args.shelves, args.thickness)
    except ValueError as error:
        parser.error(str(error))
    builder.save(args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
