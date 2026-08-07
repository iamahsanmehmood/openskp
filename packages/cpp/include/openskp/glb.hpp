#pragma once

#include <filesystem>

#include <openskp/export.hpp>
#include <openskp/model.hpp>
#include <openskp/scene.hpp>

namespace openskp {

/// Serialize a baked scene as a binary glTF 2.0 (GLB) asset.
OPENSKP_EXPORT ByteBuffer to_glb(const Scene& scene);

/// Serialize a baked scene and write the resulting bytes to a file.
OPENSKP_EXPORT void export_glb(const Scene& scene, const std::filesystem::path& output_path);

}  // namespace openskp
