#include <openskp/openskp.hpp>

int main() {
  openskp::Scene scene;
  auto bytes = openskp::to_glb(scene);
  openskp::export_glb(scene, "consumer-output.bin");
  auto file = openskp::SkpFile::from_buffer({});
  (void)file;
  return bytes.empty();
}
