#!/usr/bin/env python3
"""Independently validate an exported SKP with a locally installed SketchUp C API.

The SDK is used only for verification, never by the application's exporter.
Pass --library explicitly; no proprietary library is copied into this repository.
"""
import argparse
import ctypes as C
import json
from pathlib import Path


class Ref(C.Structure):
    _fields_ = [("ptr", C.c_void_p)]


class Color(C.Structure):
    _fields_ = [(c, C.c_ubyte) for c in ("r", "g", "b", "a")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--library", required=True)
    parser.add_argument("--unit", choices=["in", "mm", "cm", "m"])
    parser.add_argument("--precision", type=int)
    parser.add_argument("--background", help="Expected RGB as R,G,B")
    parser.add_argument("--textured-material", action="append", default=[], metavar="NAME",
                        help="Require a named material to retain its texture (repeatable)")
    parser.add_argument("--resave", type=Path, help="Also save a new modern SKP (must not exist)")
    args = parser.parse_args()
    if args.resave and args.resave.exists():
        parser.error("resave destination must not exist")
    sdk = C.CDLL(args.library)

    def call(name, types, *values):
        fn = getattr(sdk, name)
        fn.argtypes = types
        fn.restype = C.c_int
        result = fn(*values)
        if result:
            raise RuntimeError(f"{name}: SUResult={result}")

    model, value = Ref(), Ref()
    sdk.SUInitialize()
    try:
        status = C.c_int()
        call("SUModelCreateFromFileWithStatus", [C.POINTER(Ref), C.c_char_p, C.POINTER(C.c_int)],
             C.byref(model), str(args.file.resolve()).encode(), C.byref(status))
        manager, provider, rendering = Ref(), Ref(), Ref()
        call("SUModelGetOptionsManager", [Ref, C.POINTER(Ref)], model, C.byref(manager))
        call("SUOptionsManagerGetOptionsProviderByName", [Ref, C.c_char_p, C.POINTER(Ref)],
             manager, b"UnitsOptions", C.byref(provider))
        call("SUTypedValueCreate", [C.POINTER(Ref)], C.byref(value))
        actual = {"load_status": status.value, "bytes": args.file.stat().st_size}
        for key in ("LengthUnit", "LengthFormat", "LengthPrecision"):
            call("SUOptionsProviderGetValue", [Ref, C.c_char_p, C.POINTER(Ref)],
                 provider, key.encode(), C.byref(value))
            number = C.c_int32()
            call("SUTypedValueGetInt32", [Ref, C.POINTER(C.c_int32)], value, C.byref(number))
            actual[key] = number.value
        call("SUModelGetRenderingOptions", [Ref, C.POINTER(Ref)], model, C.byref(rendering))
        call("SURenderingOptionsGetValue", [Ref, C.c_char_p, C.POINTER(Ref)],
             rendering, b"BackgroundColor", C.byref(value))
        color = Color()
        call("SUTypedValueGetColor", [Ref, C.POINTER(Color)], value, C.byref(color))
        actual["BackgroundColor"] = [color.r, color.g, color.b]
        expected = {}
        if args.unit:
            expected.update(LengthUnit={"in": 0, "mm": 2, "cm": 3, "m": 4}[args.unit], LengthFormat=0)
        if args.precision is not None:
            expected["LengthPrecision"] = args.precision
        if args.background:
            expected["BackgroundColor"] = [int(v) for v in args.background.split(",")]
        print(json.dumps(actual, ensure_ascii=False), flush=True)
        for key, wanted in expected.items():
            if actual[key] != wanted:
                raise RuntimeError(f"{key}: expected {wanted}, got {actual[key]}")
        def refs(owner, count_api, list_api):
            count = C.c_size_t()
            call(count_api, [Ref, C.POINTER(C.c_size_t)], owner, C.byref(count))
            items = (Ref * count.value)()
            if count.value:
                call(list_api, [Ref, C.c_size_t, C.POINTER(Ref), C.POINTER(C.c_size_t)],
                     owner, count, items, C.byref(count))
            return items

        def child(api, owner):
            result = Ref()
            call(api, [Ref, C.POINTER(Ref)], owner, C.byref(result))
            return result

        # CGroup alone is insufficient: its definition must also be native group type.
        seen_definitions = set()
        group_count = 0

        def check_groups(entities):
            nonlocal group_count
            if entities.ptr in seen_definitions:
                return
            seen_definitions.add(entities.ptr)
            for plural, api in (("Groups", "Group"), ("Instances", "ComponentInstance")):
                for instance in refs(entities, "SUEntitiesGetNum" + plural, "SUEntitiesGet" + plural):
                    definition = child("SU" + api + "GetDefinition", instance)
                    if api == "Group":
                        kind = C.c_int()
                        call("SUComponentDefinitionGetType", [Ref, C.POINTER(C.c_int)],
                             definition, C.byref(kind))
                        if kind.value != 1:  # SUComponentType_Group
                            raise RuntimeError(f"Group references native definition type {kind.value}, expected 1")
                        group_count += 1
                    check_groups(child("SUComponentDefinitionGetEntities", definition))

        check_groups(child("SUModelGetEntities", model))
        print(json.dumps({"native_groups_checked": group_count}), flush=True)
        if args.textured_material:
            names = {}
            for material in refs(model, "SUModelGetNumMaterials", "SUModelGetMaterials"):
                name = Ref()
                call("SUStringCreate", [C.POINTER(Ref)], C.byref(name))
                try:
                    call("SUMaterialGetName", [Ref, C.POINTER(Ref)], material, C.byref(name))
                    length = C.c_size_t()
                    call("SUStringGetUTF8Length", [Ref, C.POINTER(C.c_size_t)], name, C.byref(length))
                    buf = C.create_string_buffer(length.value + 1)
                    call("SUStringGetUTF8", [Ref, C.c_size_t, C.c_char_p, C.POINTER(C.c_size_t)],
                         name, len(buf), buf, C.byref(length))
                    names[buf.value.decode()] = material
                finally:
                    call("SUStringRelease", [C.POINTER(Ref)], C.byref(name))
            results = {}
            for name in args.textured_material:
                material = names[name]
                texture = child("SUMaterialGetTexture", material)
                kind, mode = C.c_int(), C.c_int()
                call("SUMaterialGetType", [Ref, C.POINTER(C.c_int)], material, C.byref(kind))
                call("SUMaterialGetColorizeType", [Ref, C.POINTER(C.c_int)], material, C.byref(mode))
                width, height, scale_s, scale_t = C.c_size_t(), C.c_size_t(), C.c_double(), C.c_double()
                call("SUTextureGetDimensions", [Ref, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t),
                     C.POINTER(C.c_double), C.POINTER(C.c_double)], texture, C.byref(width),
                     C.byref(height), C.byref(scale_s), C.byref(scale_t))
                if kind.value not in (1, 2) or not width.value or not height.value:
                    raise RuntimeError(f"{name}: missing native texture")
                results[name] = {"type": kind.value, "colorize_mode": mode.value,
                                 "pixels": [width.value, height.value], "groups": 0, "instances": 0}
            pointers = {names[name].ptr: name for name in results}
            seen = set()

            def walk(entities):
                if entities.ptr in seen:
                    return
                seen.add(entities.ptr)
                for plural, api in (("Groups", "Group"), ("Instances", "ComponentInstance")):
                    for instance in refs(entities, "SUEntitiesGetNum" + plural, "SUEntitiesGet" + plural):
                        cast = getattr(sdk, "SU" + api + "ToDrawingElement")
                        cast.argtypes, cast.restype = [Ref], Ref
                        material = Ref()
                        getter = sdk.SUDrawingElementGetMaterial
                        getter.argtypes, getter.restype = [Ref, C.POINTER(Ref)], C.c_int
                        status = getter(cast(instance), C.byref(material))
                        if status not in (0, 9):  # SU_ERROR_NO_DATA means unpainted/inherited.
                            raise RuntimeError(f"SUDrawingElementGetMaterial: SUResult={status}")
                        if status == 0 and material.ptr in pointers:
                            results[pointers[material.ptr]][plural.lower()] += 1
                        definition = child("SU" + api + "GetDefinition", instance)
                        walk(child("SUComponentDefinitionGetEntities", definition))
            walk(child("SUModelGetEntities", model))
            print(json.dumps({"textured_materials": results}, ensure_ascii=False), flush=True)
        if args.resave:
            call("SUModelSaveToFileWithVersion", [Ref, C.c_char_p, C.c_int],
                 model, str(args.resave.resolve()).encode(), 14)  # SU2021 versionless format
            print(json.dumps({"resaved_bytes": args.resave.stat().st_size}))
    finally:
        if value.ptr:
            sdk.SUTypedValueRelease(C.byref(value))
        if model.ptr:
            sdk.SUModelRelease(C.byref(model))
        sdk.SUTerminate()


if __name__ == "__main__":
    main()
