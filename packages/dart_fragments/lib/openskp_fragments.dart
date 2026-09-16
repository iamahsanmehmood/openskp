/// Direct SketchUp (.skp) -> ThatOpen Fragments (.frag) export for OpenSkp.
///
/// See [toFragments]/[exportFragments] for the entry points, and
/// `src/fragments_export.dart`'s own header comment for the full
/// rationale, scope, and verification status.
library openskp_fragments;

export 'src/fragments_export.dart'
    show toFragments, exportFragments, FragmentExportOptions, Trs, BakedGeometry, decomposeTrs, bakePrimitive, scaleCacheKey;
