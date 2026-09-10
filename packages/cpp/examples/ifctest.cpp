#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: openskp_ifctest model.skp output.ifc\n";
    return 2;
  }
  try {
    auto file = openskp::SkpFile::open(argv[1]);
    auto scene = file.build_scene();
    openskp::export_ifc(scene, argv[2]);
    std::cout << "ok: " << scene.mesh_index.size() << " mesh entries, "
              << scene.glb_primitives.size() << " primitives, " << scene.layer_hidden.size()
              << " layer_hidden entries\n";
  } catch (const std::exception& e) {
    std::cerr << "EXC: " << e.what() << '\n';
    return 1;
  }
}
