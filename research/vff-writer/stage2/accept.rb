m = Sketchup.active_model
faces = m.entities.grep(Sketchup::Face)
groups = m.entities.grep(Sketchup::Group)
insts = m.entities.grep(Sketchup::ComponentInstance)
report = []
report << "title=#{m.title} path=#{m.path}"
report << "materials=" + m.materials.map { |x|
  t = x.texture
  [x.name, x.color.to_a, t ? [t.filename, t.width, t.height, t.image_width, t.image_height] : nil]
}.inspect
report << "layers=" + m.layers.map { |l| [l.name, l.color.to_a] }.inspect
report << "faces=#{faces.length} groups=#{groups.length} component_instances=#{insts.length}"
faces.each_with_index do |f, i|
  tex = f.material && f.material.texture
  report << "face#{i}=" + [
    f.material ? f.material.name : nil,
    f.layer.name,
    tex ? tex.filename : nil,
    f.texture_positioned?(true),
    f.uv_tile_at(f.vertices.first.position, true).map { |p| [p.x.round(6), p.y.round(6), p.z.round(6)] },
  ].inspect
end
groups.each_with_index do |g, i|
  report << "group#{i}=" + [g.name, g.definition.name, g.definition.group?, g.layer.name,
                            g.material ? g.material.name : nil,
                            g.transformation.to_a.map { |v| v.round(9) },
                            g.definition.entities.grep(Sketchup::Face).length].inspect
end
insts.each_with_index do |c, i|
  nested = c.definition.entities.grep(Sketchup::Group)
  report << "instance#{i}=" + [c.definition.name, c.definition.group?, c.layer.name,
                               c.material ? c.material.name : nil,
                               c.transformation.to_a.map { |v| v.round(9) },
                               nested.map { |g| [g.name, g.definition.group?, g.layer.name, g.material ? g.material.name : nil] }].inspect
end
report << "definitions=" + m.definitions.map { |d| [d.name, d.group?, d.entities.grep(Sketchup::Face).length, d.entities.grep(Sketchup::Group).length, d.entities.grep(Sketchup::ComponentInstance).length] }.inspect
report.join("\n")
