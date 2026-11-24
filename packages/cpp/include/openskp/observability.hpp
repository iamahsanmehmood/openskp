#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>

namespace openskp {

enum class LogLevel { debug, information, warning };
enum class ParseStage { header, zip_extract, tlv_walk, legacy_walk, legacy_defs, build_scene };

constexpr std::string_view stage_name(ParseStage s) noexcept {
  switch (s) {
    case ParseStage::header:
      return "header";
    case ParseStage::zip_extract:
      return "zip_extract";
    case ParseStage::tlv_walk:
      return "tlv_walk";
    case ParseStage::legacy_walk:
      return "legacy_walk";
    case ParseStage::legacy_defs:
      return "legacy_defs";
    case ParseStage::build_scene:
      return "build_scene";
  }
  return {};
}

struct ParseProgress {
  ParseStage stage;
  std::uint64_t current{};
  std::uint64_t total{};
};

struct ParseOptions {
  std::function<void(const ParseProgress&)> progress;
  std::function<void(LogLevel, std::string_view)> log;
  /// "style" (default) gives unpainted faces the model's front and back face
  /// colours; "tag" gives them their tag's colour.
  std::string unpainted_faces{"style"};
  /// Keep a texture that is not PNG or JPEG in Scene::textures and
  /// InstancedScene::textures in its own format (image/tiff, image/gif,
  /// image/bmp, image/x-tga, image/webp, image/vnd.adobe.photoshop,
  /// image/vnd.ms-dds, image/jp2) instead of leaving it out with a warning.
  /// Off by default because glTF carries only PNG and JPEG.
  bool original_textures{false};
  /// Bake loose edges (edges no face uses) into Scene::curve_sets and
  /// InstancedScene::curve_resources. Defaults to true.
  bool include_curve_sets{true};
};

inline constexpr std::uint64_t progress_interval = 500;
}  // namespace openskp
