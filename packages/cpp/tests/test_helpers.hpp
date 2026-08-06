#pragma once

#include <cstring>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <string>
#include <vector>

#include "internal.hpp"

namespace openskp::test {

inline std::filesystem::path fixture(const char* name) {
  return std::filesystem::path(OPENSKP_FIXTURES) / name;
}

inline ByteBuffer read_fixture(const char* name) {
  std::ifstream stream(fixture(name), std::ios::binary | std::ios::ate);
  const auto size = stream.tellg();
  ByteBuffer data(static_cast<std::size_t>(size));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(data.data()), size);
  return data;
}

inline ByteBuffer concat(std::initializer_list<ByteBuffer> arrays) {
  std::size_t size = 0;
  for (const auto& array : arrays) size += array.size();

  ByteBuffer result;
  result.reserve(size);
  for (const auto& array : arrays) result.insert(result.end(), array.begin(), array.end());
  return result;
}

inline ByteBuffer tlv(const char* tag, ByteBuffer payload = {}) {
  const auto hex = [](char value) -> std::uint8_t {
    return value >= 'A' ? static_cast<std::uint8_t>(value - 'A' + 10)
                        : static_cast<std::uint8_t>(value - '0');
  };

  ByteBuffer result{
      static_cast<std::uint8_t>((hex(tag[0]) << 4) | hex(tag[1])),
      static_cast<std::uint8_t>((hex(tag[2]) << 4) | hex(tag[3])),
  };
  const auto size = static_cast<std::uint32_t>(payload.size());
  for (int i = 0; i < 4; ++i) result.push_back(static_cast<std::uint8_t>(size >> (i * 8)));
  result.insert(result.end(), payload.begin(), payload.end());
  return result;
}

inline ByteBuffer f64s(std::initializer_list<double> values) {
  ByteBuffer result(values.size() * sizeof(double));
  std::size_t offset = 0;
  for (double value : values) {
    std::uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (int i = 0; i < 8; ++i) result[offset++] = static_cast<std::uint8_t>(bits >> (i * 8));
  }
  return result;
}

inline ByteBuffer f64s(const std::vector<double>& values) {
  ByteBuffer result(values.size() * sizeof(double));
  std::size_t offset = 0;
  for (double value : values) {
    std::uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (int i = 0; i < 8; ++i) result[offset++] = static_cast<std::uint8_t>(bits >> (i * 8));
  }
  return result;
}

inline ByteBuffer bytes(std::string value) { return ByteBuffer(value.begin(), value.end()); }

}  // namespace openskp::test
