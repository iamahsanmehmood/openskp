m = Sketchup.active_model
faces = m.entities.grep(Sketchup::Face)
edges = m.entities.grep(Sketchup::Edge)
insts = m.entities.grep(Sketchup::ComponentInstance)
defn = m.definitions.first
report = []
report << "title=#{m.title} path=#{m.path}"
report << "faces=#{faces.length} edges=#{edges.length} instances=#{insts.length} defs=#{m.definitions.length}"
report << "materials=" + m.materials.map { |x| [x.name, x.color.red, x.color.green, x.color.blue, x.alpha].inspect }.join(',')
report << "layers=" + m.layers.map { |l| [l.name, l.color.red, l.color.green, l.color.blue].inspect }.join(',')
report << "face_materials=" + faces.map { |f| f.material ? f.material.name : 'none' }.inspect
report << "face_layers=" + faces.map { |f| f.layer.name }.uniq.inspect
report << "edge_layers=" + edges.map { |e| e.layer.name }.uniq.inspect
report << "face_normals=" + faces.map { |f| f.normal.to_a.map { |v| v.round(6) } }.inspect
report << "face_areas=" + faces.map { |f| f.area.round(4) }.inspect
report << "vertices=" + m.entities.grep(Sketchup::Edge).flat_map { |e| [e.start.position.to_a, e.end.position.to_a] }.uniq.length.to_s
report << "definition=" + [defn.name, defn.guid, defn.entities.grep(Sketchup::Face).length, defn.entities.grep(Sketchup::Edge).length, defn.entities.grep(Sketchup::Vertex).length, defn.bounds.min.to_a, defn.bounds.max.to_a].inspect
insts.each_with_index do |i, k|
  report << "instance#{k}=" + [i.definition.name, i.name, i.layer.name, i.material ? i.material.name : nil, i.transformation.to_a.map { |v| v.round(9) }].inspect
end
report << "model_bounds_min=#{m.bounds.min.to_a.inspect} max=#{m.bounds.max.to_a.inspect}"
report << "units=#{m.options['UnitsOptions']['LengthUnit']}"
report.join("\n")
