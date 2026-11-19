#include <cstring>
#include <gtest/gtest.h>
#include <miniz.h>
#include <random>
#include <stdexcept>

#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

// A ZIP entry's declared uncompressed size is untrusted central-directory
// metadata - it can be set independently of what the compressed stream
// actually decompresses to, and even when genuine, DEFLATE can expand
// highly compressible data by three orders of magnitude.
// mz_zip_reader_extract_to_heap allocates up to that declared size with no
// ceiling of its own, so core.cpp's Zip::get() validates every entry's
// size before extracting. These tests build real ZIP archives via miniz's
// own writer API (no hand-crafted bytes needed) to exercise that guard
// end-to-end through the public full_parse() entry point, since Zip and
// validate_entry_size are implementation details in an anonymous
// namespace with no separately-testable surface.
//
// Not verified locally - no C++ toolchain is available in this
// environment. Relies on CI (GCC/Clang/MSVC) as the actual correctness
// gate; reviewed carefully against miniz's documented writer API instead.

namespace openskp::test {
namespace {

ByteBuffer build_zip_with_entry(const std::string& name, const ByteBuffer& content, int level) {
  mz_zip_archive zip{};
  if (!mz_zip_writer_init_heap(&zip, 0, 0))
    throw std::runtime_error("mz_zip_writer_init_heap failed");

  const void* data_ptr = content.empty() ? static_cast<const void*>("") : content.data();
  if (!mz_zip_writer_add_mem(&zip, name.c_str(), data_ptr, content.size(),
                             static_cast<mz_uint>(level))) {
    mz_zip_writer_end(&zip);
    throw std::runtime_error("mz_zip_writer_add_mem failed");
  }

  void* buf = nullptr;
  size_t size = 0;
  if (!mz_zip_writer_finalize_heap_archive(&zip, &buf, &size)) {
    mz_zip_writer_end(&zip);
    throw std::runtime_error("mz_zip_writer_finalize_heap_archive failed");
  }

  ByteBuffer result(static_cast<std::uint8_t*>(buf), static_cast<std::uint8_t*>(buf) + size);
  mz_free(buf);
  mz_zip_writer_end(&zip);
  return result;
}

ByteBuffer wrap_vff(const ByteBuffer& zip_bytes) {
  ByteBuffer header(16, 0);
  header[0] = 0xFF;
  header[1] = 0xFE;
  header[2] = 0xFF;
  header[3] = 0x0E;
  return concat({header, zip_bytes});
}

}  // namespace

TEST(ZipEntrySizeCap, RejectsAnImplausibleCompressionRatio) {
  // 8 MB of zeros deflates to a few hundred bytes - a ratio well past what
  // real (binary geometry) model.dat entries show (~10x), the shape of a
  // declared-size decompression bomb: tiny real payload, huge claimed size.
  ByteBuffer zeros(8 * 1024 * 1024, 0);
  auto skp_bytes = wrap_vff(build_zip_with_entry("model.dat", zeros, MZ_BEST_COMPRESSION));

  bool threw = false;
  try {
    full_parse(skp_bytes, ParseOptions{});
  } catch (const SkpParseError& e) {
    threw = true;
    EXPECT_NE(std::string(e.what()).find("compression ratio"), std::string::npos) << e.what();
  }
  EXPECT_TRUE(threw);
}

TEST(ZipEntrySizeCap, AllowsARealisticCompressionRatio) {
  // Pseudo-random content compresses poorly (ratio close to 1x),
  // comfortably under the safety threshold.
  ByteBuffer random(64 * 1024);
  std::mt19937 rng(42);
  std::uniform_int_distribution<int> dist(0, 255);
  for (auto& b : random) b = static_cast<std::uint8_t>(dist(rng));
  // Inside a well-formed top-level record: a model.dat whose own first record
  // overruns the data is a truncated file, which is rejected separately.
  auto skp_bytes =
      wrap_vff(build_zip_with_entry("model.dat", tlv("F401", random), MZ_BEST_COMPRESSION));

  EXPECT_NO_THROW(full_parse(skp_bytes, ParseOptions{}));
}

TEST(ZipEntrySizeCap, AllowsATinyEntryRegardlessOfRatio) {
  // Below the 1 MB ratio-check threshold, even a high ratio is allowed
  // through - the absolute cost is bounded regardless.
  ByteBuffer zeros(1024, 0);
  auto skp_bytes = wrap_vff(build_zip_with_entry("model.dat", zeros, MZ_BEST_COMPRESSION));

  EXPECT_NO_THROW(full_parse(skp_bytes, ParseOptions{}));
}

}  // namespace openskp::test
