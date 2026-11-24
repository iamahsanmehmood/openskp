m = Sketchup.active_model

def dict_dump(obj)
  ds = obj.attribute_dictionaries
  return [] unless ds
  ds.map do |d|
    [d.name, d.keys.map { |k| [k, d[k].class.to_s, d[k].inspect] }]
  end
end

def curve_dump(edges)
  grouped = {}
  edges.each do |e|
    c = e.curve
    next unless c
    key = c.entityID
    entry = grouped[key] ||= { "class" => c.class.to_s, "edges" => 0,
                               "arc" => c.respond_to?(:radius) }
    entry["edges"] += 1
    if entry["arc"]
      entry["center"] = c.center.to_a.map { |v| v.round(6) }
      entry["radius"] = c.radius
      entry["normal"] = c.normal.to_a.map { |v| v.round(6) }
      entry["start_angle"] = c.start_angle
      entry["end_angle"] = c.end_angle
      entry["is_polygon"] = (c.respond_to?(:is_polygon?) ? c.is_polygon? : nil)
    end
  end
  grouped
end

report = []
report << "title=#{m.title} path=#{m.path}"
report << "materials=" + m.materials.map { |x|
  t = x.texture
  [x.name, t ? [t.filename, t.width, t.height, t.image_width, t.image_height] : nil]
}.inspect
report << "faces=#{m.entities.grep(Sketchup::Face).length} edges=#{m.entities.grep(Sketchup::Edge).length} groups=#{m.entities.grep(Sketchup::Group).length} comps=#{m.entities.grep(Sketchup::ComponentInstance).length}"
report << "root_curves=" + curve_dump(m.entities.grep(Sketchup::Edge)).inspect
faces = m.entities.grep(Sketchup::Face)
faces.each_with_index do |f, i|
  report << "face#{i}=" + [f.loops.length,
                            f.loops.map { |l| [l.class.to_s, l.edges.length] },
                            f.area.round(6), f.material ? f.material.name : nil,
                            f.layer.name, dict_dump(f)].inspect
end
insts = m.entities.grep(Sketchup::ComponentInstance)
insts.each_with_index do |c, i|
  report << "instance#{i}=" + [c.definition.name, c.definition.group?,
                               c.transformation.to_a.map { |v| v.round(9) },
                               dict_dump(c),
                               curve_dump(c.definition.entities.grep(Sketchup::Edge))].inspect
end
groups = m.entities.grep(Sketchup::Group)
groups.each_with_index do |g, i|
  report << "group#{i}=" + [g.name, g.definition.name, g.definition.group?,
                            g.layer.name, g.material ? g.material.name : nil,
                            dict_dump(g)].inspect
end
m.definitions.each do |d|
  report << "definition=" + [d.name, d.group?,
                             d.entities.grep(Sketchup::Face).length,
                             d.entities.grep(Sketchup::Edge).length,
                             dict_dump(d),
                             curve_dump(d.entities.grep(Sketchup::Edge))].inspect
end
report << "model_dicts=" + dict_dump(m).inspect
report.join("\n")
