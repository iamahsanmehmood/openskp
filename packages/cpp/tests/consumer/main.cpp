#include <openskp/openskp.hpp>

int main() {
  auto file = openskp::SkpFile::from_buffer({});
  (void)file;
}
