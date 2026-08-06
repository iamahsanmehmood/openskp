#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: openskp_info model.skp\n";
    return 2;
  }
  try {
    auto file = openskp::SkpFile::open(argv[1]);
    auto model = file.parse();
    std::cout << model.version << "\n"
              << model.layers.size() << " layers\n"
              << model.materials.size() << " materials\n"
              << model.definitions.size() << " definitions\n";
    auto scene = file.build_scene();
    std::cout << scene.glb_primitives.size() << " primitives\n"
              << scene.mesh_index.size() << " meshes\n"
              << scene.gltf_materials.size() << " glTF materials\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
