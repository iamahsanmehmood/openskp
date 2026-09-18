/// Direct SketchUp (.skp) <-> ThatOpen Fragments (.frag) conversion for
/// OpenSkp.
///
/// See [toFragments]/[exportFragments] for the export entry points,
/// [fromFragments]/[readFragments] for reading a `.frag` file back, and
/// `src/fragments_export.dart`'s own header comment for the full
/// rationale, scope, and verification status.
library openskp_fragments;

export 'src/fragments_export.dart'
    show
        toFragments,
        exportFragments,
        fromFragments,
        readFragments,
        FragmentExportOptions,
        Trs,
        BakedGeometry,
        decomposeTrs,
        bakePrimitive,
        scaleCacheKey;
