#include <algorithm>

#include "internal.hpp"

namespace openskp {
bool valid_header(const ByteBuffer& d) {
  return d.size() >= 4 && d[0] == 0xff && d[1] == 0xfe && d[2] == 0xff && d[3] == 0x0e;
}

std::string extract_version(const ByteBuffer& d) {
  std::size_t marker = std::string::npos;
  for (std::size_t i = 4; i + 2 < d.size() && i < 512; ++i)
    if (d[i] == 0xff && d[i + 1] == 0xfe && d[i + 2] == 0xff) {
      marker = i;
      break;
    }
  if (marker == std::string::npos) return "unknown";
  std::string s;
  for (std::size_t i = marker + 4; i + 1 < d.size() && i < 512; i += 2)
    if (d[i]) s.push_back(char(d[i]));
  auto a = s.find('{'), b = s.find('}', a);
  return a != std::string::npos && b != std::string::npos ? s.substr(a, b - a + 1) : "unknown";
}

namespace {
// meta/meta.dat uses the exact same low-level TLV framing as model.dat
// (2-byte tag + 4-byte little-endian length + payload), but as one flat,
// non-recursive record list wrapped in a single outer record (tag
// 0x6400/"6400"). Tag 0x6D00/"6D00" carries the model's unit-system
// string ("Millimeter" etc.) as plain text. Confirmed byte-for-byte
// against a real fixture - never opened by any parser in this codebase
// before.
constexpr const char* kMetaWrapperTag = "6400";
constexpr const char* kMetaUnitsTag = "6D00";
}  // namespace

// Extract the model's unit-system string from a VFF file's meta/meta.dat
// contents, or nullopt if the expected tags aren't found.
std::optional<std::string> read_meta_units(const ByteBuffer& meta_bytes) {
  // Real files have one wrapper record around the units. Wrappers are
  // followed in a loop over (start, end) offsets, not by recursing on copies
  // of the body: a file with thousands of nested wrappers would otherwise
  // overflow the stack and copy almost the whole payload at every level.
  // More than kMaxTlvDepth wrappers is not a real file; the units are then
  // unknown, which is not an error.
  std::size_t start = 0, end = meta_bytes.size();
  for (int level = 0; level <= kMaxTlvDepth; ++level) {
    std::size_t pos = start;
    bool descended = false;
    while (pos + 6 <= end) {
      auto size = read_u32(meta_bytes, pos + 2);
      if (size > end - pos - 6) break;
      auto tag = tag_at(meta_bytes, pos);
      if (tag == kMetaWrapperTag) {
        start = pos + 6;
        end = pos + 6 + size;
        descended = true;
        break;
      }
      if (tag == kMetaUnitsTag)
        return std::string(meta_bytes.begin() + static_cast<std::ptrdiff_t>(pos + 6),
                           meta_bytes.begin() + static_cast<std::ptrdiff_t>(pos + 6 + size));
      pos += 6 + size;
    }
    if (!descended) return std::nullopt;
  }
  return std::nullopt;
}

bool is_legacy(const ByteBuffer& d) {
  if (!valid_header(d)) return false;
  auto v = extract_version(d);
  auto dot = v.find('.');
  if (v.size() < 2 || dot == std::string::npos) return false;
  try {
    return std::stoi(v.substr(1, dot - 1)) <= 20;
  } catch (...) {
    return false;
  }
}
}  // namespace openskp
