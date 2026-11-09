"""Generate native behavior fixtures with a locally installed SketchUp C API.

Usage: python generate_behavior_masks.py --library /path/to/SketchUpAPI --output /path/to/fixtures
The SDK is used only for fixture creation, never by OpenSKP itself.
"""
import argparse
import ctypes as C
from pathlib import Path

class Ref(C.Structure):
    _fields_ = [("ptr", C.c_void_p)]

class Behavior(C.Structure):
    _fields_ = [("snap", C.c_int), ("cuts", C.c_bool), ("camera", C.c_bool),
                ("sun", C.c_bool), ("mask", C.c_size_t)]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sdk = C.CDLL(args.library)
    def call(name, types, *values):
        f = getattr(sdk, name)
        f.argtypes, f.restype = types, C.c_int
        result = f(*values)
        if result:
            raise RuntimeError(f"{name}: SUResult={result}")
    sdk.SUInitialize()
    try:
        cases = [("behavior_mask_2017.skp", 10, 127, 0),
                 ("behavior_mask_2021.skp", 14, 127, 0),
                 ("root_behavior_mask_2013.skp", 6, 0, 2),
                 ("root_behavior_mask_2017.skp", 10, 0, 2),
                 ("root_behavior_mask_2021.skp", 14, 0, 127)]
        for filename, version, definition_mask, root_mask in cases:
            model = Ref()
            call("SUModelCreate", [C.POINTER(Ref)], C.byref(model))
            try:
                definition = Ref()
                call("SUComponentDefinitionCreate", [C.POINTER(Ref)], C.byref(definition))
                call("SUComponentDefinitionSetName", [Ref, C.c_char_p], definition, b"MaskProbe")
                call("SUModelAddComponentDefinitions", [Ref, C.c_size_t, C.POINTER(Ref)],
                     model, 1, C.byref(definition))
                instance, entities = Ref(), Ref()
                call("SUComponentDefinitionCreateInstance", [Ref, C.POINTER(Ref)], definition, C.byref(instance))
                call("SUModelGetEntities", [Ref, C.POINTER(Ref)], model, C.byref(entities))
                call("SUEntitiesAddInstance", [Ref, Ref, C.c_void_p], entities, instance, None)
                for prefix, owner, mask in [("SUModel", model, root_mask),
                                            ("SUComponentDefinition", definition, definition_mask)]:
                    behavior = Behavior()
                    call(prefix + "GetBehavior", [Ref, C.POINTER(Behavior)], owner, C.byref(behavior))
                    behavior.mask = mask
                    call(prefix + "SetBehavior", [Ref, C.POINTER(Behavior)], owner, C.byref(behavior))
                call("SUModelSaveToFileWithVersion", [Ref, C.c_char_p, C.c_int],
                     model, str((args.output / filename).resolve()).encode(), version)
            finally:
                call("SUModelRelease", [C.POINTER(Ref)], C.byref(model))
    finally:
        sdk.SUTerminate()

if __name__ == "__main__":
    main()
