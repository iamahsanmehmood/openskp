m = Sketchup.active_model
report = []
report << "title=#{m.title}"
cam = m.active_view.camera
report << "camera=" + [cam.eye.to_a, cam.target.to_a, cam.up.to_a, cam.perspective?, cam.fov, cam.height].inspect
report << "counts=" + [m.entities.grep(Sketchup::Face).length,
                      m.entities.grep(Sketchup::ComponentInstance).length,
                      m.entities.grep(Sketchup::Group).length,
                      m.entities.grep(Sketchup::Text).length,
                      m.entities.grep(Sketchup::DimensionLinear).length,
                      m.entities.grep(Sketchup::ConstructionLine).length,
                      m.entities.grep(Sketchup::ConstructionPoint).length,
                      m.entities.grep(Sketchup::SectionPlane).length,
                      m.definitions.length, m.materials.length,
                      m.layers.length, m.pages.length].inspect
report << "defs=" + m.definitions.map { |d|
  [d.name, d.group?, d.entities.grep(Sketchup::Face).length,
   d.entities.grep(Sketchup::ComponentInstance).length,
   d.entities.grep(Sketchup::Group).length]
}.inspect
report << "materials=" + m.materials.map { |x|
  [x.name, x.color.to_a,
   x.texture ? [x.texture.filename, x.texture.width, x.texture.height,
                x.texture.image_width, x.texture.image_height] : nil]
}.inspect
report << "pages=" + m.pages.map { |p|
  c = p.camera
  [p.name, c.eye.to_a, c.target.to_a, c.perspective?, c.fov, c.height,
   p.layers.map { |l| [l.name, l.visible?] }]
}.inspect
report << "texts=" + m.entities.grep(Sketchup::Text).map { |t|
  [t.text, t.point.to_a, t.vector.to_a, t.has_leader?, t.display_leader?]
}.inspect
report << "dims=" + m.entities.grep(Sketchup::DimensionLinear).map { |d|
  [d.text, d.start[1].to_a, d.end[1].to_a, d.offset_vector.to_a]
}.inspect
report << "guides=" + [m.entities.grep(Sketchup::ConstructionLine).map { |c|
  [c.start.nil? ? nil : c.start.to_a, c.end.nil? ? nil : c.end.to_a, c.direction.to_a, c.position.to_a]
}, m.entities.grep(Sketchup::ConstructionPoint).map { |c| c.position.to_a }].inspect
report << "sections=" + m.entities.grep(Sketchup::SectionPlane).map { |s|
  [s.get_plane.to_a, s.name, s.symbol, s.active?]
}.inspect
report << "root_instances=" + m.entities.grep(Sketchup::ComponentInstance).map { |i|
  [i.definition.name, i.transformation.to_a.map { |v| v.round(9) }]
}.inspect
report.join("\n")
