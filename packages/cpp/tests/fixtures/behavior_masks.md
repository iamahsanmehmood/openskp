# Native scale-mask fixtures

Generated with the SketchUp 2026 C API, not with OpenSKP. Each file contains one empty definition named `MaskProbe` and one root instance to retain that definition.

- `behavior_mask_2017.skp`: legacy SU2017, definition mask 127, root mask 0.
- `behavior_mask_2021.skp`: versionless VFF, definition mask 127, root mask 0.
- `root_behavior_mask_2013.skp`: legacy SU2013, root mask 2, definition mask 0.
- `root_behavior_mask_2017.skp`: legacy SU2017, root mask 2, definition mask 0.
- `root_behavior_mask_2021.skp`: versionless VFF, root mask 127, definition mask 0.

Regenerate using `tests/generate_behavior_masks.py --library <local SketchUpAPI library> --output <fixture directory>`. No SDK library is distributed or linked into OpenSKP.

Differential SDK samples with masks 0, 1, 2, 4, 8, 16, 32, 64 and 127 identify the definition field as the little-endian u32 eight bytes before CThumbnail. In VFF it is field 5A1B in block 581B. Root VFF behavior is under 0E02 > 581B. Legacy root masks follow the UTF-16 End-Of-Version-Map terminator by 28 bytes in SU7 through SU2016, and 37 bytes in SU2017 through SU2020. SDK down-saves to SU3 through SU6 omit the mask.

Independent validation also loaded OpenSKP writer output through SUModelGetBehavior and SUComponentDefinitionGetBehavior for all seven individual bits, zero, 127 and 0x12345678. Both root and definition values matched, and neighboring face-camera/shadow flags remained intact.
