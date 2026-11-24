m = Sketchup.active_model
report = []
report << "title=#{m.title} path=#{m.path}"
texts = m.entities.grep(Sketchup::Text)
texts.each_with_index do |t, i|
  report << "text#{i}=" + [t.text, t.point.to_a, t.vector.to_a, t.has_leader?,
                           t.display_leader?, t.leader_type,
                           t.attached_to.inspect].inspect
end
m.entities.grep(Sketchup::DimensionLinear).each_with_index do |d, i|
  start = d.start
  stop = d.end
  report << "dim#{i}=" + [
    [start[0].class.to_s, start[1].to_a],
    [stop[0].class.to_s, stop[1].to_a],
    d.offset_vector.to_a, d.text,
    d.start_attached_to.inspect, d.end_attached_to.inspect,
  ].inspect
end
m.entities.grep(Sketchup::ConstructionLine).each_with_index do |c, i|
  report << "cline#{i}=" + [c.start.nil? ? nil : c.start.to_a,
                            c.end.nil? ? nil : c.end.to_a,
                            c.direction.to_a, c.position.to_a].inspect
end
m.entities.grep(Sketchup::ConstructionPoint).each_with_index do |c, i|
  report << "cpoint#{i}=" + [c.position.to_a].inspect
end
sp = m.entities.grep(Sketchup::SectionPlane)
sp.each_with_index do |s, i|
  report << "section#{i}=" + [s.get_plane.to_a, s.name, s.symbol, s.active?,
                              s.get_plane.to_a == m.entities.active_section_plane&.get_plane&.to_a].inspect
end
report << "active_section=" + (m.entities.active_section_plane ? [m.entities.active_section_plane.name, m.entities.active_section_plane.symbol].inspect : "nil")
m.definitions.each do |d|
  report << "definition=" + [d.name, d.group?,
                             d.entities.grep(Sketchup::Text).length,
                             d.entities.grep(Sketchup::DimensionLinear).length,
                             d.entities.grep(Sketchup::ConstructionLine).length,
                             d.entities.grep(Sketchup::ConstructionPoint).length,
                             d.entities.grep(Sketchup::SectionPlane).length].inspect
end
report.join("\n")
