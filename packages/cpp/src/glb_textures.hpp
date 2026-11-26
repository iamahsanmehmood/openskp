#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <openskp/model.hpp>
#include <openskp/observability.hpp>
#include <openskp/scene.hpp>

namespace openskp {

inline constexpr std::size_t kMaxColorizeBlockPixels = 262144;

struct StbiImageDeleter {
  void operator()(unsigned char* ptr) const noexcept;
};

struct DecodedRgbaImage {
  int width{};
  int height{};
  std::unique_ptr<unsigned char[], StbiImageDeleter> pixels;
};

struct ResolvedMaterialTexture {
  std::optional<int> gltf_texture_index;
  bool colorize_baked{};
};

struct ResolvedGltfImage {
  ByteBuffer data;
  std::string mime_type;
};

struct GltfTextureBakeStats {
  std::size_t decode_calls{};
  std::size_t colorize_calls{};
};

struct ResolvedGltfTextures {
  std::vector<ResolvedGltfImage> images;
  std::vector<ResolvedMaterialTexture> by_material;
};

/// Drop JPEG APP1 (0xFFE1, Exif / XMP orientation) marker segments before SOS
/// without re-encoding.
ByteBuffer strip_jpeg_exif_app1(const ByteBuffer& data);

/// Decode PNG or JPEG bytes to 8-bit RGBA pixels using TinyGLTF's bundled
/// stb_image implementation.
std::optional<DecodedRgbaImage> decode_texture_rgba(const ByteBuffer& data,
                                                    std::string* error_out = nullptr);

/// Apply SketchUp's HLS colorize rules (colorize_type 0 = shift, 1 = tint) in
/// float32 row blocks of at most max_block_pixels, matching
/// SUTextureGetColorizedImageRep within 1/255.
ByteBuffer colorize_image_u8(const std::uint8_t* rgba, int width, int height, Color3 target_rgb,
                             int colorize_type, bool keep_alpha = true,
                             std::size_t max_block_pixels = kMaxColorizeBlockPixels);

/// Encode 8-bit RGB (channels = 3) or RGBA (channels = 4) pixels to PNG bytes.
ByteBuffer encode_png_u8(const std::uint8_t* pixels, int width, int height, int channels);

/// Encode 8-bit RGB (channels = 3) or RGBA (channels = 4, alpha ignored) pixels
/// to baseline JPEG without chroma subsampling (4:4:4).
ByteBuffer encode_jpeg_444(const std::uint8_t* pixels, int width, int height, int channels,
                           int quality = 95);

/// Resolve embedded GLB images and per-material texture indices for to_glb()
/// and to_instanced_glb(), baking colorized textures once per unique
/// (texture_index, colorize_type, target_rgb).
ResolvedGltfTextures resolve_gltf_textures(
    const std::vector<SceneTexture>& raw_textures, const std::vector<GltfMaterial>& materials,
    bool embed, const std::function<void(LogLevel, std::string_view)>& log,
    GltfTextureBakeStats* stats = nullptr);

}  // namespace openskp
