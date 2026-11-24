m = Sketchup.active_model

def dict_dump(obj)
  ds = obj.attribute_dictionaries
  return [] unless ds
  ds.map { |d| [d.name, d.keys.map { |k| [k, d[k].class.to_s, d[k].inspect] }] }
end

report = []
report << "title=#{m.title} path=#{m.path}"
report << "counts=" + [m.entities.grep(Sketchup::Face).length,
                      m.entities.grep(Sketchup::ComponentInstance).length,
                      m.entities.grep(Sketchup::Group).length,
                      m.definitions.length, m.materials.length,
                      m.layers.length, m.pages.length].inspect
report << "materials=" + m.materials.map { |x|
  t = x.texture
  [x.name, x.color.to_a, t ? [t.filename, t.width, t.height, t.image_width, t.image_height] : nil]
}.inspect
report << "layers=" + m.layers.map { |l| [l.name, l.color.to_a, l.visible?] }.inspect
report << "pages=" + m.pages.map { |p|
  c = p.camera
  [p.name, c.eye.to_a, c.target.to_a, c.perspective?, c.fov, c.height,
   p.layers.map { |l| [l.name, l.visible?] }]
}.inspect
report << "selected=" + [m.pages.selected_page&.name].inspect
m.definitions.each do |d|
  report << "definition=" + [d.name, d.group?, d.guid,
                             d.entities.grep(Sketchup::Face).length,
                             d.entities.grep(Sketchup::Edge).length,
                             d.entities.grep(Sketchup::ComponentInstance).length,
                             dict_dump(d)].inspect
end
m.entities.grep(Sketchup::ComponentInstance).each_with_index do |i, n|
  report << "instance#{n}=" + [i.definition.name, i.name, i.layer.name,
                               i.transformation.to_a.map { |v| v.round(9) },
                               dict_dump(i)].inspect
end
m.entities.grep(Sketchup::Group).each_with_index do |g, n|
  report << "group#{n}=" + [g.name, g.definition.name, g.layer.name,
                            g.material ? g.material.name : nil,
                            dict_dump(g)].inspect
end
m.entities.grep(Sketchup::Face).each_with_index do |f, n|
  report << "face#{n}=" + [f.material ? f.material.name : nil, f.layer.name,
                           dict_dump(f)].inspect
end
report << "model_dicts=" + dict_dump(m).inspect
report.join("\n")
