#pragma once

#include <cstdint>
#include <filesystem>
#include <vector>

#include <openskp/export.hpp>
#include <openskp/instanced_scene.hpp>

namespace openskp {

/// Builds a `.frag` (FlatBuffers) buffer from an InstancedScene - a direct
/// port of Python's openskp.export.fragments.to_fragments(), writing
/// against the same vendored ThatOpen Fragments schema
/// (include/openskp/_fragments_fb/index.fbs). See that module's own
/// docstring for the full rationale and the real-loader verification
/// status this port inherits.
///
/// `raw = false` (the default, matching ThatOpen's own IfcImporter
/// convention) deflate-compresses the output (zlib/RFC 1950) - the real
/// Fragments loader auto-detects either form. Pass `raw = true` for the
/// uncompressed FlatBuffers bytes directly.
OPENSKP_EXPORT std::vector<std::uint8_t> to_fragments(const InstancedScene& scene,
                                                      bool raw = false);

/// Exports an instanced scene directly to a `.frag` file.
OPENSKP_EXPORT void export_fragments(const InstancedScene& scene,
                                     const std::filesystem::path& output_path, bool raw = false);

}  // namespace openskp
