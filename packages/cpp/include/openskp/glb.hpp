#pragma once

#include <filesystem>
#include <functional>
#include <string_view>

#include <openskp/export.hpp>
#include <openskp/model.hpp>
#include <openskp/observability.hpp>
#include <openskp/scene.hpp>

namespace openskp {

/// Options for to_glb()/export_glb().
struct GlbOptions {
  /// Embed the scene's texture images in the GLB and point each textured
  /// material's baseColorTexture at them. Off by default, matching every
  /// other language's exporter: photographic textures can multiply the
  /// file size, and the geometry alone is what most callers are after.
  bool textures{false};
  /// Optional log callback receiving warnings (for example, when a
  /// non-PNG/JPEG texture kept via ParseOptions::original_textures is omitted
  /// from the GLB).
  std::function<void(LogLevel, std::string_view)> log;
};

/// Serialize a baked scene as a binary glTF 2.0 (GLB) asset.
OPENSKP_EXPORT ByteBuffer to_glb(const Scene& scene, const GlbOptions& options = {});

/// Serialize a baked scene and write the resulting bytes to a file.
OPENSKP_EXPORT void export_glb(const Scene& scene, const std::filesystem::path& output_path,
                               const GlbOptions& options = {});

}  // namespace openskp
