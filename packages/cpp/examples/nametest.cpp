#include <functional>
#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: openskp_nametest model.skp\n";
    return 2;
  }
  try {
    auto file = openskp::SkpFile::open(argv[1]);
    auto scene = file.build_instanced_scene();

    int real_named = 0, generated = 0, with_guid = 0;
    std::function<void(const openskp::InstancedNode&, int)> walk;
    walk = [&](const openskp::InstancedNode& node, int depth) {
      if (node.name_is_generated) {
        ++generated;
      } else {
        ++real_named;
        if (depth > 0 && depth < 4) {
          std::cout << std::string(static_cast<std::size_t>(depth) * 2, ' ') << node.name
                    << "  guid=" << (node.guid.empty() ? "(none)" : node.guid)
                    << "  children=" << node.children.size() << "\n";
        }
      }
      if (!node.guid.empty()) ++with_guid;
      for (auto& c : node.children) walk(c, depth + 1);
    };
    walk(scene.scene_hierarchy, 0);

    std::cout << "\nreal_named=" << real_named << " generated=" << generated
              << " with_guid=" << with_guid << "\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
