#include "glb_textures.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>
#include <miniz.h>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>

#include "face_groups.hpp"

#define STB_IMAGE_IMPLEMENTATION
#define STB_IMAGE_STATIC
#define STBI_NO_STDIO
#define STBI_ONLY_JPEG
#define STBI_ONLY_PNG
#define STBI_FAILURE_USERMSG

#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STB_IMAGE_WRITE_STATIC
#define STBI_WRITE_NO_STDIO

#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-function"
#pragma clang diagnostic ignored "-Wmissing-field-initializers"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#elif defined(_MSC_VER)
#pragma warning(push)
#pragma warning(disable : 4505)
#endif

#include <stb_image.h>
#include <stb_image_write.h>

#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#elif defined(_MSC_VER)
#pragma warning(pop)
#endif

namespace openskp {

void StbiImageDeleter::operator()(unsigned char* ptr) const noexcept {
  if (ptr) stbi_image_free(ptr);
}

namespace {

struct Hls64 {
  double h{};
  double l{};
  double s{};
};

Hls64 rgb_to_hls_f64(double r, double g, double b) {
  const double maxc = std::max({r, g, b});
  const double minc = std::min({r, g, b});
  const double sumc = maxc + minc;
  const double rangec = maxc - minc;
  const double l = sumc / 2.0;
  if (minc == maxc) return {0.0, l, 0.0};
  const double s = (l <= 0.5) ? (rangec / sumc) : (rangec / (2.0 - maxc - minc));
  const double rc = (maxc - r) / rangec;
  const double gc = (maxc - g) / rangec;
  const double bc = (maxc - b) / rangec;
  double h = 0.0;
  if (r == maxc) {
    h = bc - gc;
  } else if (g == maxc) {
    h = 2.0 + rc - bc;
  } else {
    h = 4.0 + gc - rc;
  }
  h = std::fmod(h / 6.0, 1.0);
  if (h < 0.0) h += 1.0;
  return {h, l, s};
}

void colorize_pixels_impl(const std::uint8_t* rgba, std::uint8_t* dst, int width, int height,
                          Color3 target_rgb, int colorize_type, bool keep_alpha,
                          std::size_t max_block_pixels) {
  if (width <= 0 || height <= 0) return;
  const std::size_t w_px = static_cast<std::size_t>(width);
  const std::size_t h_px = static_cast<std::size_t>(height);
  const std::size_t n_px = w_px * h_px;
  const std::size_t out_channels = keep_alpha ? 4u : 3u;

  // SUTextureGetAverageColor truncates each channel's mean over all pixels to uint8.
  std::uint64_t sum_r = 0;
  std::uint64_t sum_g = 0;
  std::uint64_t sum_b = 0;
  for (std::size_t i = 0; i < n_px; ++i) {
    sum_r += rgba[i * 4 + 0];
    sum_g += rgba[i * 4 + 1];
    sum_b += rgba[i * 4 + 2];
  }
  const double avg_r = static_cast<double>(sum_r / n_px) / 255.0;
  const double avg_g = static_cast<double>(sum_g / n_px) / 255.0;
  const double avg_b = static_cast<double>(sum_b / n_px) / 255.0;
  const Hls64 avg = rgb_to_hls_f64(avg_r, avg_g, avg_b);
  const Hls64 tgt =
      rgb_to_hls_f64(target_rgb[0] / 255.0, target_rgb[1] / 255.0, target_rgb[2] / 255.0);

  const bool is_shift = (colorize_type == 0);
  const float delta_h = static_cast<float>(tgt.h - avg.h);
  const float delta_l = static_cast<float>(tgt.l - avg.l);
  // SUMaterialGetColorizeDeltas sets delta_s = -1.0 when desaturating a chromatic
  // texture toward a greyscale target (tgt_s == 0 and avg_s > 0).
  const float delta_s = static_cast<float>((tgt.s == 0.0 && avg.s > 0.0) ? -1.0 : (tgt.s - avg.s));
  const float tgt_h_f = static_cast<float>(tgt.h);

  constexpr float kOneSixth = 1.0f / 6.0f;
  constexpr float kOneThird = 1.0f / 3.0f;
  constexpr float kHalf = 0.5f;
  constexpr float kOne = 1.0f;
  constexpr float kTwo = 2.0f;
  constexpr float kFour = 4.0f;
  constexpr float kSix = 6.0f;
  constexpr float kMinL = 0.01f;
  constexpr float kUndefH = -1.0f / 360.0f;
  constexpr float kEps = 1e-6f;
  constexpr float kScale255 = 255.0f;
  constexpr float kZero = 0.0f;
  constexpr std::array<float, 3> kOffsets{kOneThird, kZero, -kOneThird};

  const std::size_t eff_block = std::max<std::size_t>(1, max_block_pixels);
  const std::size_t block_rows = std::min(h_px, std::max<std::size_t>(1, eff_block / w_px));

  for (std::size_t y0 = 0; y0 < h_px; y0 += block_rows) {
    const std::size_t y1 = std::min(h_px, y0 + block_rows);
    const std::size_t p_start = y0 * w_px;
    const std::size_t p_end = y1 * w_px;
    for (std::size_t p = p_start; p < p_end; ++p) {
      const std::uint8_t src_r = rgba[p * 4 + 0];
      const std::uint8_t src_g = rgba[p * 4 + 1];
      const std::uint8_t src_b = rgba[p * 4 + 2];
      const std::uint8_t src_a = rgba[p * 4 + 3];

      const float r = static_cast<float>(src_r) / kScale255;
      const float g = static_cast<float>(src_g) / kScale255;
      const float b = static_cast<float>(src_b) / kScale255;

      const float cm = std::max(std::max(r, g), b);
      const float cn = std::min(std::min(r, g), b);
      const float rg = cm - cn;
      float lt = (cm + cn) * kHalf;
      const bool nz = rg > kEps;
      float st = kZero;
      if (nz) {
        const float denom = (lt <= kHalf) ? (cm + cn) : (kTwo - (cm + cn));
        st = rg / denom;
      }

      float nh = tgt_h_f;
      if (is_shift) {
        // SketchUp's RGB_to_HLS assigns H = -1 deg (-1/360 turn) to achromatic pixels.
        if (!nz) {
          nh = kUndefH;
        } else {
          if (r == cm) {
            nh = (g - b) / rg;
          } else if (g == cm) {
            nh = (b - r) / rg + kTwo;
          } else {
            nh = (r - g) / rg + kFour;
          }
          nh *= kOneSixth;
          nh -= std::floor(nh);
        }
        nh += delta_h;
        nh -= std::floor(nh);
      }

      lt = std::clamp(lt + delta_l, kMinL, kOne);
      st = std::clamp(st + delta_s, kZero, kOne);

      // Use lt + st * (1 - lt) for lt > 0.5 so 1 - lt is exact in float32 (Sterbenz's lemma).
      const float m2 = (lt > kHalf) ? (lt + st * (kOne - lt)) : (lt + lt * st);
      const float m1 = lt * kTwo - m2;
      const float span = m2 - m1;
      const bool achromatic = st <= kEps;

      std::uint8_t* out_px = dst + p * out_channels;
      for (std::size_t ch = 0; ch < 3; ++ch) {
        float t1 = nh;
        if (kOffsets[ch] != kZero) {
          t1 += kOffsets[ch];
          t1 -= std::floor(t1);
        }
        t1 = t1 * kSix - kTwo;
        t1 = kTwo - std::abs(t1);
        t1 = std::clamp(t1, kZero, kOne);
        float val = lt;
        if (!achromatic) {
          val = (t1 == kOne) ? m2 : (t1 * span + m1);
        }
        val = std::clamp(val * kScale255, kZero, kScale255);
        out_px[ch] = static_cast<std::uint8_t>(val);
      }
      if (keep_alpha) {
        out_px[3] = src_a;
      }
    }
  }
}

void append_be32(ByteBuffer& out, std::uint32_t v) {
  out.push_back(static_cast<std::uint8_t>(v >> 24));
  out.push_back(static_cast<std::uint8_t>(v >> 16));
  out.push_back(static_cast<std::uint8_t>(v >> 8));
  out.push_back(static_cast<std::uint8_t>(v));
}

void write_png_chunk(ByteBuffer& out, const char tag[4], const std::uint8_t* payload,
                     std::size_t len) {
  append_be32(out, static_cast<std::uint32_t>(len));
  const std::size_t crc_start = out.size();
  out.insert(out.end(), reinterpret_cast<const std::uint8_t*>(tag),
             reinterpret_cast<const std::uint8_t*>(tag) + 4);
  if (len > 0) {
    out.insert(out.end(), payload, payload + len);
  }
  const mz_ulong crc = mz_crc32(MZ_CRC32_INIT, out.data() + crc_start, 4 + len);
  append_be32(out, static_cast<std::uint32_t>(crc));
}

// Unsigned bit writer for baseline 4:4:4 JPEG encoding (avoids signed left-shift UB
// in stbiw__jpg_writeBits under -fsanitize=undefined while always emitting 4:4:4).
void jpeg_write_bits(ByteBuffer& out, std::uint32_t& bit_buf, int& bit_cnt,
                     const unsigned short bs[2]) {
  bit_cnt += static_cast<int>(bs[1]);
  bit_buf |= static_cast<std::uint32_t>(bs[0]) << (24 - bit_cnt);
  while (bit_cnt >= 8) {
    const auto c = static_cast<std::uint8_t>((bit_buf >> 16) & 0xFFu);
    out.push_back(c);
    if (c == 0xFFu) out.push_back(0x00u);
    bit_buf = (bit_buf << 8) & 0xFFFFFFu;
    bit_cnt -= 8;
  }
}

void jpeg_calc_bits(int val, unsigned short bits[2]) {
  unsigned int mag = static_cast<unsigned int>(val < 0 ? -val : val);
  const unsigned int encoded = static_cast<unsigned int>(val < 0 ? (val - 1) : val);
  unsigned short len = 1;
  while ((mag >>= 1u) != 0u) ++len;
  bits[1] = len;
  bits[0] = static_cast<unsigned short>(encoded & ((1u << len) - 1u));
}

int jpeg_process_du(ByteBuffer& out, std::uint32_t& bit_buf, int& bit_cnt, float* cdu,
                    const float* fdtbl, int dc, const unsigned short htdc[256][2],
                    const unsigned short htac[256][2]) {
  const unsigned short eob[2] = {htac[0x00][0], htac[0x00][1]};
  const unsigned short m16_zeroes[2] = {htac[0xF0][0], htac[0xF0][1]};
  int du[64];

  for (int off = 0; off < 64; off += 8) {
    stbiw__jpg_DCT(&cdu[off + 0], &cdu[off + 1], &cdu[off + 2], &cdu[off + 3], &cdu[off + 4],
                   &cdu[off + 5], &cdu[off + 6], &cdu[off + 7]);
  }
  for (int off = 0; off < 8; ++off) {
    stbiw__jpg_DCT(&cdu[off + 0], &cdu[off + 8], &cdu[off + 16], &cdu[off + 24], &cdu[off + 32],
                   &cdu[off + 40], &cdu[off + 48], &cdu[off + 56]);
  }
  for (int j = 0; j < 64; ++j) {
    const float v = cdu[j] * fdtbl[j];
    du[stbiw__jpg_ZigZag[j]] = static_cast<int>(v < 0.0f ? (v - 0.5f) : (v + 0.5f));
  }

  const int diff = du[0] - dc;
  if (diff == 0) {
    jpeg_write_bits(out, bit_buf, bit_cnt, htdc[0]);
  } else {
    unsigned short bits[2];
    jpeg_calc_bits(diff, bits);
    jpeg_write_bits(out, bit_buf, bit_cnt, htdc[bits[1]]);
    jpeg_write_bits(out, bit_buf, bit_cnt, bits);
  }

  int end0pos = 63;
  while (end0pos > 0 && du[end0pos] == 0) --end0pos;
  if (end0pos == 0) {
    jpeg_write_bits(out, bit_buf, bit_cnt, eob);
    return du[0];
  }
  for (int i = 1; i <= end0pos; ++i) {
    const int startpos = i;
    while (du[i] == 0 && i <= end0pos) ++i;
    int nrzeroes = i - startpos;
    if (nrzeroes >= 16) {
      const int lng = nrzeroes >> 4;
      for (int m = 0; m < lng; ++m) jpeg_write_bits(out, bit_buf, bit_cnt, m16_zeroes);
      nrzeroes &= 15;
    }
    unsigned short bits[2];
    jpeg_calc_bits(du[i], bits);
    jpeg_write_bits(out, bit_buf, bit_cnt, htac[(nrzeroes << 4) + bits[1]]);
    jpeg_write_bits(out, bit_buf, bit_cnt, bits);
  }
  if (end0pos != 63) {
    jpeg_write_bits(out, bit_buf, bit_cnt, eob);
  }
  return du[0];
}

std::string basename_only(const std::string& filename) {
  const auto pos = filename.find_last_of("/\\");
  return pos == std::string::npos ? filename : filename.substr(pos + 1);
}

std::string format_label_for_warning(const ByteBuffer& data, const std::string& filename) {
  if (const auto mime = original_image_mime(data)) {
    if (*mime == "image/tiff") return "TIFF";
    if (*mime == "image/gif") return "GIF";
    if (*mime == "image/bmp") return "BMP";
    if (*mime == "image/x-tga") return "TGA";
    if (*mime == "image/webp") return "WebP";
    if (*mime == "image/vnd.adobe.photoshop") return "PSD";
    if (*mime == "image/vnd.ms-dds") return "DDS";
    if (*mime == "image/jp2") return "JPEG 2000";
  }
  const auto base = basename_only(filename);
  const auto dot = base.rfind('.');
  if (dot != std::string::npos) {
    auto ext = base.substr(dot + 1);
    if (!ext.empty() && ext.size() <= 5) {
      for (auto& ch : ext) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
      }
      return ext;
    }
  }
  return "an unrecognized format";
}

}  // namespace

ByteBuffer strip_jpeg_exif_app1(const ByteBuffer& data) {
  const std::size_t n = data.size();
  if (n < 4 || data[0] != 0xFF || data[1] != 0xD8) return data;

  std::vector<std::pair<std::size_t, std::size_t>> drop_ranges;
  std::size_t pos = 2;
  while (pos < n) {
    if (data[pos] != 0xFF) break;
    const std::size_t marker_start = pos;
    while (pos < n && data[pos] == 0xFF) ++pos;
    if (pos >= n) break;
    const std::uint8_t marker = data[pos++];
    if (marker == 0x00 || marker == 0xD9) break;
    if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD8)) continue;
    if (pos + 2 > n) break;
    const std::size_t seg_len =
        (static_cast<std::size_t>(data[pos]) << 8) | static_cast<std::size_t>(data[pos + 1]);
    if (seg_len < 2 || pos + seg_len > n) break;
    const std::size_t seg_end = pos + seg_len;
    if (marker == 0xDA) break;  // Start of Scan: compressed entropy stream follows
    if (marker == 0xE1) {       // APP1 (Exif / XMP)
      drop_ranges.emplace_back(marker_start, seg_end);
    }
    pos = seg_end;
  }

  if (drop_ranges.empty()) return data;

  std::size_t dropped = 0;
  for (const auto& r : drop_ranges) dropped += (r.second - r.first);
  ByteBuffer out;
  out.reserve(n - dropped);
  std::size_t cursor = 0;
  for (const auto& [start, end] : drop_ranges) {
    if (start > cursor) {
      out.insert(out.end(), data.begin() + static_cast<std::ptrdiff_t>(cursor),
                 data.begin() + static_cast<std::ptrdiff_t>(start));
    }
    cursor = end;
  }
  if (cursor < n) {
    out.insert(out.end(), data.begin() + static_cast<std::ptrdiff_t>(cursor), data.end());
  }
  return out;
}

std::optional<DecodedRgbaImage> decode_texture_rgba(const ByteBuffer& data,
                                                    std::string* error_out) {
  if (data.empty() || data.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    if (error_out) *error_out = "empty or oversized image buffer";
    return std::nullopt;
  }
  int width = 0;
  int height = 0;
  int channels_in_file = 0;
  unsigned char* raw = stbi_load_from_memory(data.data(), static_cast<int>(data.size()), &width,
                                             &height, &channels_in_file, 4);
  if (!raw || width <= 0 || height <= 0) {
    if (raw) stbi_image_free(raw);
    if (error_out) {
      const char* reason = stbi_failure_reason();
      *error_out = reason ? reason : "image decode failed";
    }
    return std::nullopt;
  }
  DecodedRgbaImage img;
  img.width = width;
  img.height = height;
  img.pixels.reset(raw);
  return img;
}

ByteBuffer colorize_image_u8(const std::uint8_t* rgba, int width, int height, Color3 target_rgb,
                             int colorize_type, bool keep_alpha, std::size_t max_block_pixels) {
  if (!rgba || width <= 0 || height <= 0) return {};
  const std::size_t out_channels = keep_alpha ? 4u : 3u;
  ByteBuffer out(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * out_channels);
  colorize_pixels_impl(rgba, out.data(), width, height, target_rgb, colorize_type, keep_alpha,
                       max_block_pixels);
  return out;
}

ByteBuffer encode_png_u8(const std::uint8_t* pixels, int width, int height, int channels) {
  if (!pixels || width <= 0 || height <= 0 || (channels != 3 && channels != 4)) {
    throw std::invalid_argument("encode_png_u8 requires non-empty RGB or RGBA pixels");
  }

  // Stream filtered scanlines row-by-row through stb_image_write's filter encoder
  // and miniz's streaming deflate so 4096x4096 PNGs never allocate a second
  // full-image filter buffer.
  const int stride_bytes = width * channels;
  const std::size_t row_bytes = static_cast<std::size_t>(stride_bytes);

  mz_stream zs{};
  if (mz_deflateInit(&zs, MZ_DEFAULT_COMPRESSION) != MZ_OK) {
    throw std::runtime_error("miniz deflateInit failed for PNG encoding");
  }

  ByteBuffer idat;
  idat.reserve(std::min<std::size_t>(row_bytes * static_cast<std::size_t>(height) / 4 + 256,
                                     4u * 1024u * 1024u));
  std::array<std::uint8_t, 65536> zchunk{};
  std::vector<signed char> cand_line(row_bytes);
  std::vector<signed char> best_line(row_bytes);
  std::vector<std::uint8_t> raw_scanline(1 + row_bytes);

  auto pump = [&](int flush) {
    for (;;) {
      zs.next_out = zchunk.data();
      zs.avail_out = static_cast<unsigned int>(zchunk.size());
      const int rc = mz_deflate(&zs, flush);
      if (rc != MZ_OK && rc != MZ_STREAM_END) {
        mz_deflateEnd(&zs);
        throw std::runtime_error("miniz deflate failed during PNG encoding");
      }
      const std::size_t have = zchunk.size() - zs.avail_out;
      if (have > 0) idat.insert(idat.end(), zchunk.data(), zchunk.data() + have);
      if (rc == MZ_STREAM_END || zs.avail_out != 0) break;
    }
  };

  for (int y = 0; y < height; ++y) {
    int best_filter = 0;
    int best_score = std::numeric_limits<int>::max();
    for (int f = 0; f < 5; ++f) {
      stbiw__encode_png_line(const_cast<unsigned char*>(pixels), stride_bytes, width, height, y,
                             channels, f, cand_line.data());
      int score = 0;
      for (std::size_t i = 0; i < row_bytes; ++i) {
        score += std::abs(static_cast<int>(cand_line[i]));
      }
      if (score < best_score) {
        best_score = score;
        best_filter = f;
        std::memcpy(best_line.data(), cand_line.data(), row_bytes);
      }
    }
    raw_scanline[0] = static_cast<std::uint8_t>(best_filter);
    std::memcpy(raw_scanline.data() + 1, best_line.data(), row_bytes);
    zs.next_in = raw_scanline.data();
    zs.avail_in = static_cast<unsigned int>(raw_scanline.size());
    pump(MZ_NO_FLUSH);
  }
  zs.next_in = nullptr;
  zs.avail_in = 0;
  pump(MZ_FINISH);
  mz_deflateEnd(&zs);

  static constexpr std::array<std::uint8_t, 8> kPngSig{137, 80, 78, 71, 13, 10, 26, 10};
  ByteBuffer out;
  out.reserve(8 + 25 + 12 + idat.size() + 12);
  out.insert(out.end(), kPngSig.begin(), kPngSig.end());

  std::array<std::uint8_t, 13> ihdr{};
  ihdr[0] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(width) >> 24) & 0xFFu);
  ihdr[1] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(width) >> 16) & 0xFFu);
  ihdr[2] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(width) >> 8) & 0xFFu);
  ihdr[3] = static_cast<std::uint8_t>(static_cast<std::uint32_t>(width) & 0xFFu);
  ihdr[4] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(height) >> 24) & 0xFFu);
  ihdr[5] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(height) >> 16) & 0xFFu);
  ihdr[6] = static_cast<std::uint8_t>((static_cast<std::uint32_t>(height) >> 8) & 0xFFu);
  ihdr[7] = static_cast<std::uint8_t>(static_cast<std::uint32_t>(height) & 0xFFu);
  ihdr[8] = 8;                                                 // bit depth
  ihdr[9] = static_cast<std::uint8_t>(channels == 4 ? 6 : 2);  // RGBA = 6, RGB = 2
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  write_png_chunk(out, "IHDR", ihdr.data(), ihdr.size());
  write_png_chunk(out, "IDAT", idat.data(), idat.size());
  write_png_chunk(out, "IEND", nullptr, 0);
  return out;
}

ByteBuffer encode_jpeg_444(const std::uint8_t* pixels, int width, int height, int channels,
                           int quality) {
  if (!pixels || width <= 0 || height <= 0 || (channels != 3 && channels != 4) || width > 65535 ||
      height > 65535) {
    throw std::invalid_argument("encode_jpeg_444 requires valid 1..65535 RGB/RGBA dimensions");
  }

  // Standard IJG Annex K luminance / chrominance quantization & Huffman tables,
  // always emitting 1x1 (0x11) sampling factors on Y, Cb, and Cr (4:4:4).
  static const unsigned char kStdDcLumNrCodes[] = {0, 0, 1, 5, 1, 1, 1, 1, 1,
                                                   1, 0, 0, 0, 0, 0, 0, 0};
  static const unsigned char kStdDcLumValues[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
  static const unsigned char kStdAcLumNrCodes[] = {0, 0, 2, 1, 3, 3, 2, 4,   3,
                                                   5, 5, 4, 4, 0, 0, 1, 0x7d};
  static const unsigned char kStdAcLumValues[] = {
      0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61,
      0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52,
      0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25,
      0x26, 0x27, 0x28, 0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45,
      0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64,
      0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83,
      0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99,
      0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
      0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3,
      0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8,
      0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa};
  static const unsigned char kStdDcChromNrCodes[] = {0, 0, 3, 1, 1, 1, 1, 1, 1,
                                                     1, 1, 1, 0, 0, 0, 0, 0};
  static const unsigned char kStdDcChromValues[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
  static const unsigned char kStdAcChromNrCodes[] = {0, 0, 2, 1, 2, 4, 4, 3,   4,
                                                     7, 5, 4, 4, 0, 1, 2, 0x77};
  static const unsigned char kStdAcChromValues[] = {
      0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61,
      0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33,
      0x52, 0xf0, 0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18,
      0x19, 0x1a, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44,
      0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63,
      0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a,
      0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97,
      0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
      0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca,
      0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7,
      0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa};
  static const unsigned short kYDcHt[256][2] = {{0, 2},  {2, 3},   {3, 3},   {4, 3},
                                                {5, 3},  {6, 3},   {14, 4},  {30, 5},
                                                {62, 6}, {126, 7}, {254, 8}, {510, 9}};
  static const unsigned short kUvDcHt[256][2] = {{0, 2},   {1, 2},   {2, 2},     {6, 3},
                                                 {14, 4},  {30, 5},  {62, 6},    {126, 7},
                                                 {254, 8}, {510, 9}, {1022, 10}, {2046, 11}};
  static const unsigned short kYAcHt[256][2] = {
      {10, 4},     {0, 2},      {1, 2},      {4, 3},      {11, 4},     {26, 5},     {120, 7},
      {248, 8},    {1014, 10},  {65410, 16}, {65411, 16}, {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {12, 4},     {27, 5},     {121, 7},    {502, 9},
      {2038, 11},  {65412, 16}, {65413, 16}, {65414, 16}, {65415, 16}, {65416, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {28, 5},     {249, 8},
      {1015, 10},  {4084, 12},  {65417, 16}, {65418, 16}, {65419, 16}, {65420, 16}, {65421, 16},
      {65422, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {58, 6},     {503, 9},    {4085, 12},  {65423, 16}, {65424, 16}, {65425, 16}, {65426, 16},
      {65427, 16}, {65428, 16}, {65429, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {59, 6},     {1016, 10},  {65430, 16}, {65431, 16}, {65432, 16},
      {65433, 16}, {65434, 16}, {65435, 16}, {65436, 16}, {65437, 16}, {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {122, 7},    {2039, 11},  {65438, 16},
      {65439, 16}, {65440, 16}, {65441, 16}, {65442, 16}, {65443, 16}, {65444, 16}, {65445, 16},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {123, 7},
      {4086, 12},  {65446, 16}, {65447, 16}, {65448, 16}, {65449, 16}, {65450, 16}, {65451, 16},
      {65452, 16}, {65453, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {250, 8},    {4087, 12},  {65454, 16}, {65455, 16}, {65456, 16}, {65457, 16},
      {65458, 16}, {65459, 16}, {65460, 16}, {65461, 16}, {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {504, 9},    {32704, 15}, {65462, 16}, {65463, 16},
      {65464, 16}, {65465, 16}, {65466, 16}, {65467, 16}, {65468, 16}, {65469, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {505, 9},    {65470, 16},
      {65471, 16}, {65472, 16}, {65473, 16}, {65474, 16}, {65475, 16}, {65476, 16}, {65477, 16},
      {65478, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {506, 9},    {65479, 16}, {65480, 16}, {65481, 16}, {65482, 16}, {65483, 16}, {65484, 16},
      {65485, 16}, {65486, 16}, {65487, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {1017, 10},  {65488, 16}, {65489, 16}, {65490, 16}, {65491, 16},
      {65492, 16}, {65493, 16}, {65494, 16}, {65495, 16}, {65496, 16}, {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {1018, 10},  {65497, 16}, {65498, 16},
      {65499, 16}, {65500, 16}, {65501, 16}, {65502, 16}, {65503, 16}, {65504, 16}, {65505, 16},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {2040, 11},
      {65506, 16}, {65507, 16}, {65508, 16}, {65509, 16}, {65510, 16}, {65511, 16}, {65512, 16},
      {65513, 16}, {65514, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {65515, 16}, {65516, 16}, {65517, 16}, {65518, 16}, {65519, 16}, {65520, 16},
      {65521, 16}, {65522, 16}, {65523, 16}, {65524, 16}, {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {2041, 11},  {65525, 16}, {65526, 16}, {65527, 16}, {65528, 16},
      {65529, 16}, {65530, 16}, {65531, 16}, {65532, 16}, {65533, 16}, {65534, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0}};
  static const unsigned short kUvAcHt[256][2] = {
      {0, 2},      {1, 2},      {4, 3},      {10, 4},     {24, 5},     {25, 5},     {56, 6},
      {120, 7},    {500, 9},    {1014, 10},  {4084, 12},  {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {11, 4},     {57, 6},     {246, 8},    {501, 9},
      {2038, 11},  {4085, 12},  {65416, 16}, {65417, 16}, {65418, 16}, {65419, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {26, 5},     {247, 8},
      {1015, 10},  {4086, 12},  {32706, 15}, {65420, 16}, {65421, 16}, {65422, 16}, {65423, 16},
      {65424, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {27, 5},     {248, 8},    {1016, 10},  {4087, 12},  {65425, 16}, {65426, 16}, {65427, 16},
      {65428, 16}, {65429, 16}, {65430, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {58, 6},     {502, 9},    {65431, 16}, {65432, 16}, {65433, 16},
      {65434, 16}, {65435, 16}, {65436, 16}, {65437, 16}, {65438, 16}, {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {59, 6},     {1017, 10},  {65439, 16},
      {65440, 16}, {65441, 16}, {65442, 16}, {65443, 16}, {65444, 16}, {65445, 16}, {65446, 16},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {121, 7},
      {2039, 11},  {65447, 16}, {65448, 16}, {65449, 16}, {65450, 16}, {65451, 16}, {65452, 16},
      {65453, 16}, {65454, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {122, 7},    {2040, 11},  {65455, 16}, {65456, 16}, {65457, 16}, {65458, 16},
      {65459, 16}, {65460, 16}, {65461, 16}, {65462, 16}, {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {249, 8},    {65463, 16}, {65464, 16}, {65465, 16},
      {65466, 16}, {65467, 16}, {65468, 16}, {65469, 16}, {65470, 16}, {65471, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {503, 9},    {65472, 16},
      {65473, 16}, {65474, 16}, {65475, 16}, {65476, 16}, {65477, 16}, {65478, 16}, {65479, 16},
      {65480, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {504, 9},    {65481, 16}, {65482, 16}, {65483, 16}, {65484, 16}, {65485, 16}, {65486, 16},
      {65487, 16}, {65488, 16}, {65489, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {505, 9},    {65490, 16}, {65491, 16}, {65492, 16}, {65493, 16},
      {65494, 16}, {65495, 16}, {65496, 16}, {65497, 16}, {65498, 16}, {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {506, 9},    {65499, 16}, {65500, 16},
      {65501, 16}, {65502, 16}, {65503, 16}, {65504, 16}, {65505, 16}, {65506, 16}, {65507, 16},
      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},      {2041, 11},
      {65508, 16}, {65509, 16}, {65510, 16}, {65511, 16}, {65512, 16}, {65513, 16}, {65514, 16},
      {65515, 16}, {65516, 16}, {0, 0},      {0, 0},      {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {16352, 14}, {65517, 16}, {65518, 16}, {65519, 16}, {65520, 16}, {65521, 16},
      {65522, 16}, {65523, 16}, {65524, 16}, {65525, 16}, {0, 0},      {0, 0},      {0, 0},
      {0, 0},      {0, 0},      {1018, 10},  {32707, 15}, {65526, 16}, {65527, 16}, {65528, 16},
      {65529, 16}, {65530, 16}, {65531, 16}, {65532, 16}, {65533, 16}, {65534, 16}, {0, 0},
      {0, 0},      {0, 0},      {0, 0},      {0, 0}};
  static const int kYQt[64] = {16,  11,  10,  16,  24, 40, 51,  61,  12,  12,  14,  19,  26,
                               58,  60,  55,  14,  13, 16, 24,  40,  57,  69,  56,  14,  17,
                               22,  29,  51,  87,  80, 62, 18,  22,  37,  56,  68,  109, 103,
                               77,  24,  35,  55,  64, 81, 104, 113, 92,  49,  64,  78,  87,
                               103, 121, 120, 101, 72, 92, 95,  98,  112, 100, 103, 99};
  static const int kUvQt[64] = {17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
                                24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99,
                                99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
                                99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99};
  static const float kAasf[8] = {1.0f * 2.828427125f,         1.387039845f * 2.828427125f,
                                 1.306562965f * 2.828427125f, 1.175875602f * 2.828427125f,
                                 1.0f * 2.828427125f,         0.785694958f * 2.828427125f,
                                 0.541196100f * 2.828427125f, 0.275899379f * 2.828427125f};

  int q = std::clamp(quality, 1, 100);
  q = (q < 50) ? (5000 / q) : (200 - q * 2);

  std::array<unsigned char, 64> y_table{};
  std::array<unsigned char, 64> uv_table{};
  for (int i = 0; i < 64; ++i) {
    const int yti = std::clamp((kYQt[i] * q + 50) / 100, 1, 255);
    const int uvti = std::clamp((kUvQt[i] * q + 50) / 100, 1, 255);
    y_table[stbiw__jpg_ZigZag[i]] = static_cast<unsigned char>(yti);
    uv_table[stbiw__jpg_ZigZag[i]] = static_cast<unsigned char>(uvti);
  }

  std::array<float, 64> fdtbl_y{};
  std::array<float, 64> fdtbl_uv{};
  for (int row = 0, k = 0; row < 8; ++row) {
    for (int col = 0; col < 8; ++col, ++k) {
      fdtbl_y[k] =
          1.0f / (static_cast<float>(y_table[stbiw__jpg_ZigZag[k]]) * kAasf[row] * kAasf[col]);
      fdtbl_uv[k] =
          1.0f / (static_cast<float>(uv_table[stbiw__jpg_ZigZag[k]]) * kAasf[row] * kAasf[col]);
    }
  }

  ByteBuffer out;
  out.reserve(std::min<std::size_t>(
      640 + static_cast<std::size_t>(width) * static_cast<std::size_t>(height) / 2,
      8u * 1024u * 1024u));

  static const unsigned char kHead0[] = {0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10, 'J', 'F', 'I',
                                         'F',  0,    1,    1,    0, 0,    1,   0,   1,
                                         0,    0,    0xFF, 0xDB, 0, 0x84, 0};
  const unsigned char head1[] = {0xFF,
                                 0xC0,
                                 0,
                                 0x11,
                                 8,
                                 static_cast<unsigned char>((height >> 8) & 0xFF),
                                 static_cast<unsigned char>(height & 0xFF),
                                 static_cast<unsigned char>((width >> 8) & 0xFF),
                                 static_cast<unsigned char>(width & 0xFF),
                                 3,
                                 1,
                                 0x11,  // Y sampling 1x1 (4:4:4)
                                 0,
                                 2,
                                 0x11,  // Cb sampling 1x1 (4:4:4)
                                 1,
                                 3,
                                 0x11,  // Cr sampling 1x1 (4:4:4)
                                 1,
                                 0xFF,
                                 0xC4,
                                 0x01,
                                 0xA2,
                                 0};
  static const unsigned char kHead2[] = {0xFF, 0xDA, 0, 0x0C, 3, 1,    0,
                                         2,    0x11, 3, 0x11, 0, 0x3F, 0};

  auto append_raw = [&](const unsigned char* bytes, std::size_t len) {
    out.insert(out.end(), bytes, bytes + len);
  };

  append_raw(kHead0, sizeof(kHead0));
  append_raw(y_table.data(), y_table.size());
  out.push_back(1);
  append_raw(uv_table.data(), uv_table.size());
  append_raw(head1, sizeof(head1));
  append_raw(kStdDcLumNrCodes + 1, sizeof(kStdDcLumNrCodes) - 1);
  append_raw(kStdDcLumValues, sizeof(kStdDcLumValues));
  out.push_back(0x10);
  append_raw(kStdAcLumNrCodes + 1, sizeof(kStdAcLumNrCodes) - 1);
  append_raw(kStdAcLumValues, sizeof(kStdAcLumValues));
  out.push_back(0x01);
  append_raw(kStdDcChromNrCodes + 1, sizeof(kStdDcChromNrCodes) - 1);
  append_raw(kStdDcChromValues, sizeof(kStdDcChromValues));
  out.push_back(0x11);
  append_raw(kStdAcChromNrCodes + 1, sizeof(kStdAcChromNrCodes) - 1);
  append_raw(kStdAcChromValues, sizeof(kStdAcChromValues));
  append_raw(kHead2, sizeof(kHead2));

  int dc_y = 0;
  int dc_u = 0;
  int dc_v = 0;
  std::uint32_t bit_buf = 0;
  int bit_cnt = 0;
  const std::size_t row_stride =
      static_cast<std::size_t>(width) * static_cast<std::size_t>(channels);

  for (int y = 0; y < height; y += 8) {
    for (int x = 0; x < width; x += 8) {
      float blk_y[64];
      float blk_u[64];
      float blk_v[64];
      for (int row = y, pos = 0; row < y + 8; ++row) {
        const int clamped_row = (row < height) ? row : (height - 1);
        const std::uint8_t* row_ptr = pixels + static_cast<std::size_t>(clamped_row) * row_stride;
        for (int col = x; col < x + 8; ++col, ++pos) {
          const int clamped_col = (col < width) ? col : (width - 1);
          const std::uint8_t* px =
              row_ptr + static_cast<std::size_t>(clamped_col) * static_cast<std::size_t>(channels);
          const float r = static_cast<float>(px[0]);
          const float g = static_cast<float>(px[1]);
          const float b = static_cast<float>(px[2]);
          blk_y[pos] = +0.29900f * r + 0.58700f * g + 0.11400f * b - 128.0f;
          blk_u[pos] = -0.16874f * r - 0.33126f * g + 0.50000f * b;
          blk_v[pos] = +0.50000f * r - 0.41869f * g - 0.08131f * b;
        }
      }
      dc_y = jpeg_process_du(out, bit_buf, bit_cnt, blk_y, fdtbl_y.data(), dc_y, kYDcHt, kYAcHt);
      dc_u = jpeg_process_du(out, bit_buf, bit_cnt, blk_u, fdtbl_uv.data(), dc_u, kUvDcHt, kUvAcHt);
      dc_v = jpeg_process_du(out, bit_buf, bit_cnt, blk_v, fdtbl_uv.data(), dc_v, kUvDcHt, kUvAcHt);
    }
  }

  static const unsigned short kFillBits[2] = {0x7F, 7};
  jpeg_write_bits(out, bit_buf, bit_cnt, kFillBits);
  out.push_back(0xFF);
  out.push_back(0xD9);
  return out;
}

ResolvedGltfTextures resolve_gltf_textures(
    const std::vector<SceneTexture>& raw_textures, const std::vector<GltfMaterial>& materials,
    bool embed, const std::function<void(LogLevel, std::string_view)>& log,
    GltfTextureBakeStats* stats) {
  ResolvedGltfTextures out;
  out.by_material.resize(materials.size());
  if (!embed || raw_textures.empty()) return out;

  // Preserve exact warning behavior for non-PNG/JPEG textures retained via
  // ParseOptions::original_textures.
  std::set<std::pair<std::string, std::string>> warned_unsupported;
  for (const auto& tex : raw_textures) {
    if (tex.mime_type != "image/png" && tex.mime_type != "image/jpeg") {
      const auto base = basename_only(tex.filename);
      const auto fmt = format_label_for_warning(tex.data, tex.filename);
      if (warned_unsupported.emplace(base, fmt).second && log) {
        std::string msg = "Texture";
        if (!base.empty()) msg += " '" + base + "'";
        msg += " is " + fmt +
               ", which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB";
        log(LogLevel::warning, msg);
      }
    }
  }

  using BakeKey = std::tuple<std::size_t, bool, int, Color3>;

  // Count distinct colorized variants per source texture so a large decoded
  // RGBA buffer can be colorized in place and released immediately on its
  // final variant instead of staying live alongside subsequent textures.
  std::set<BakeKey> unique_colorize_keys;
  std::map<std::size_t, std::size_t> remaining_variants;
  for (const auto& mat : materials) {
    const auto& pbr = mat.pbr_metallic_roughness;
    if (!pbr.base_color_texture || *pbr.base_color_texture >= raw_textures.size()) continue;
    const std::size_t tex_idx = *pbr.base_color_texture;
    const auto& tex = raw_textures[tex_idx];
    if (tex.mime_type != "image/png" && tex.mime_type != "image/jpeg") continue;
    if (!mat.colorized) continue;
    const Color3 target_rgb{
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[0] * 255.0)), 0, 255)),
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[1] * 255.0)), 0, 255)),
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[2] * 255.0)), 0, 255)),
    };
    const BakeKey ckey{tex_idx, true, mat.colorize_type, target_rgb};
    if (unique_colorize_keys.insert(ckey).second) {
      ++remaining_variants[tex_idx];
    }
  }

  std::map<BakeKey, std::optional<int>> image_by_key;
  std::map<std::size_t, std::optional<DecodedRgbaImage>> decoded_by_tex;

  auto get_decoded = [&](std::size_t tex_idx) -> DecodedRgbaImage* {
    auto it = decoded_by_tex.find(tex_idx);
    if (it != decoded_by_tex.end()) {
      return it->second ? &*it->second : nullptr;
    }
    if (stats) ++stats->decode_calls;
    std::string err;
    auto decoded = decode_texture_rgba(raw_textures[tex_idx].data, &err);
    if (!decoded && log) {
      std::string msg = "Failed to decode texture " + std::to_string(tex_idx);
      if (!raw_textures[tex_idx].filename.empty()) {
        msg += " ('" + raw_textures[tex_idx].filename + "')";
      }
      msg += " for GLB export: " + err;
      log(LogLevel::warning, msg);
    }
    auto inserted = decoded_by_tex.emplace(tex_idx, std::move(decoded)).first;
    return inserted->second ? &*inserted->second : nullptr;
  };

  for (std::size_t mat_i = 0; mat_i < materials.size(); ++mat_i) {
    const auto& mat = materials[mat_i];
    const auto& pbr = mat.pbr_metallic_roughness;
    if (!pbr.base_color_texture || *pbr.base_color_texture >= raw_textures.size()) continue;
    const std::size_t tex_idx = *pbr.base_color_texture;
    const auto& tex = raw_textures[tex_idx];
    if (tex.mime_type != "image/png" && tex.mime_type != "image/jpeg") continue;

    if (!mat.colorized) {
      const BakeKey key{tex_idx, false, 0, {0, 0, 0}};
      auto hit = image_by_key.find(key);
      if (hit != image_by_key.end()) {
        out.by_material[mat_i] = {hit->second, false};
        continue;
      }
      ByteBuffer bytes =
          (tex.mime_type == "image/jpeg") ? strip_jpeg_exif_app1(tex.data) : tex.data;
      const int gltf_idx = static_cast<int>(out.images.size());
      out.images.push_back(ResolvedGltfImage{std::move(bytes), tex.mime_type});
      image_by_key.emplace(key, gltf_idx);
      out.by_material[mat_i] = {gltf_idx, false};
      continue;
    }

    const Color3 target_rgb{
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[0] * 255.0)), 0, 255)),
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[1] * 255.0)), 0, 255)),
        static_cast<std::uint8_t>(
            std::clamp(static_cast<int>(std::lround(pbr.base_color_factor[2] * 255.0)), 0, 255)),
    };
    const BakeKey ckey{tex_idx, true, mat.colorize_type, target_rgb};
    auto hit = image_by_key.find(ckey);
    if (hit != image_by_key.end()) {
      out.by_material[mat_i] = {hit->second, hit->second.has_value()};
      continue;
    }

    auto& rem = remaining_variants[tex_idx];
    if (rem > 0) --rem;

    DecodedRgbaImage* decoded = get_decoded(tex_idx);
    if (!decoded) {
      image_by_key.emplace(ckey, std::nullopt);
      out.by_material[mat_i] = {std::nullopt, false};
      continue;
    }

    const std::string output_mime = (tex.mime_type == "image/jpeg") ? "image/jpeg" : "image/png";
    const std::size_t n_px =
        static_cast<std::size_t>(decoded->width) * static_cast<std::size_t>(decoded->height);
    bool keep_alpha = false;
    if (output_mime == "image/png") {
      const unsigned char* raw_rgba = decoded->pixels.get();
      for (std::size_t p = 0; p < n_px; ++p) {
        if (raw_rgba[p * 4 + 3] < 255) {
          keep_alpha = true;
          break;
        }
      }
    }

    if (stats) ++stats->colorize_calls;
    const int out_channels = keep_alpha ? 4 : 3;
    ByteBuffer encoded;
    if (rem == 0) {
      // Final colorized variant using this texture: colorize in place inside the
      // decoded stbi buffer (0 extra pixel allocation), encode, and release it.
      colorize_pixels_impl(decoded->pixels.get(), decoded->pixels.get(), decoded->width,
                           decoded->height, target_rgb, mat.colorize_type, keep_alpha,
                           kMaxColorizeBlockPixels);
      if (output_mime == "image/jpeg") {
        encoded = encode_jpeg_444(decoded->pixels.get(), decoded->width, decoded->height,
                                  out_channels, 95);
      } else {
        encoded =
            encode_png_u8(decoded->pixels.get(), decoded->width, decoded->height, out_channels);
      }
      decoded_by_tex.erase(tex_idx);
    } else {
      ByteBuffer baked =
          colorize_image_u8(decoded->pixels.get(), decoded->width, decoded->height, target_rgb,
                            mat.colorize_type, keep_alpha, kMaxColorizeBlockPixels);
      if (output_mime == "image/jpeg") {
        encoded = encode_jpeg_444(baked.data(), decoded->width, decoded->height, out_channels, 95);
      } else {
        encoded = encode_png_u8(baked.data(), decoded->width, decoded->height, out_channels);
      }
    }

    const int gltf_idx = static_cast<int>(out.images.size());
    out.images.push_back(ResolvedGltfImage{std::move(encoded), output_mime});
    image_by_key.emplace(ckey, gltf_idx);
    out.by_material[mat_i] = {gltf_idx, true};
  }

  return out;
}

}  // namespace openskp
