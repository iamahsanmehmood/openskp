#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: openskp_parsetest model.skp\n";
    return 2;
  }
  try {
    auto file = openskp::SkpFile::open(argv[1]);
    auto model = file.parse();
    std::cout << "ok: " << model.definitions.size() << " definitions, " << model.pages.size()
              << " pages\n";
    for (auto& pg : model.pages) {
      std::cout << "  page '" << pg.name << "' hidden_layers=" << pg.hidden_layers.size() << "\n";
    }
    size_t total_clines = 0, total_cpoints = 0;
    auto scan_defn = [&](auto&& self, const openskp::Definition& d) -> void {
      total_clines += d.construction_lines.size();
      total_cpoints += d.construction_points.size();
      for (auto& cl : d.construction_lines) {
        std::cout << "  cline point=(" << cl.point[0] << "," << cl.point[1] << "," << cl.point[2]
                  << ") dir=(" << cl.direction[0] << "," << cl.direction[1] << ","
                  << cl.direction[2] << ") bounded_start=" << cl.start.has_value()
                  << " bounded_end=" << cl.end.has_value() << "\n";
      }
      for (auto& cp : d.construction_points) {
        std::cout << "  cpoint pos=(" << cp.position[0] << "," << cp.position[1] << ","
                  << cp.position[2] << ")\n";
      }
    };
    scan_defn(scan_defn, model.root());
    for (auto& [id, d] : model.definitions) scan_defn(scan_defn, d);
    std::cout << "total construction_lines=" << total_clines
              << " construction_points=" << total_cpoints << "\n";
  } catch (const std::exception& e) {
    std::cerr << "EXC: " << e.what() << '\n';
    return 1;
  }
}
