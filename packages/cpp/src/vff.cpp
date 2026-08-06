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
