# stage4_gen_oracle.rb - SketchUp 2025 minimal pairs for text, dimensions,
# guides and section planes, plus the stage-4 acceptance target.
DIR = "C:/Temp/vff6/"
Z = Geom::Vector3d.new(0,0,1)
BOX = [[0,0,0],[10,0,0],[10,10,0],[0,10,0]]

def reset(m)
  m.entities.clear!
  m.definitions.purge_unused
  m.materials.purge_unused
  m.layers.purge_unused
  m
end

def face(m, pts = BOX)
  m.entities.add_face(*pts.map { |p| Geom::Point3d.new(*p) })
end

m = Sketchup.active_model
report = []

# 1. Leader text at a box corner, and on a face.
reset(m)
f = face(m)
v = f.vertices.first
t1 = m.entities.add_text("Corner label", v.position, Geom::Vector3d.new(10,10,10))
t1.display_leader = true
t2 = m.entities.add_text("Face label", Geom::Point3d.new(5,5,0), Geom::Vector3d.new(8,-4,6))
t2.display_leader = true
m.save(DIR + "text_leader.skp")
report << "text leaders #{t1.has_leader?}/#{t2.has_leader?}"

# 2. Screen text.
reset(m)
face(m)
ts = m.entities.add_text("Screen label", Geom::Point3d.new(5,5,0), Geom::Vector3d.new(30,20,0))
ts.display_leader = false
m.save(DIR + "text_screen.skp")
report << "screen leader? #{ts.has_leader?}"

# 3. Free dimension.
reset(m)
face(m)
d = m.entities.add_dimension_linear(Geom::Point3d.new(0,0,0), Geom::Point3d.new(10,0,0), Geom::Vector3d.new(0,5,0))
m.save(DIR + "dim_free.skp")
report << "free dim #{d.text}"

# 4. Dimension anchored to two vertices.
reset(m)
f = face(m)
va = f.vertices[0]; vb = f.vertices[1]
d = m.entities.add_dimension_linear(va, vb, Geom::Vector3d.new(0,0,8))
m.save(DIR + "dim_vertices.skp")
report << "vertex dim #{d.text}"

# 5. Dimension inside a component, placed twice.
reset(m)
defn = m.definitions.add("DimComp")
df = defn.entities.add_face(*BOX.map { |p| Geom::Point3d.new(*p) })
dv1 = df.vertices[0]; dv2 = df.vertices[1]
defn.entities.add_dimension_linear(dv1, dv2, Geom::Vector3d.new(0,0,8))
m.entities.add_instance(defn, Geom::Transformation.new([0,20,0]))
m.entities.add_instance(defn, Geom::Transformation.new([30,20,0]) * Geom::Transformation.rotation([0,0,0], Z, Math::PI/6))
m.save(DIR + "dim_component.skp")

# 6. Guides: bounded, unbounded and a guide point.
reset(m)
m.entities.add_cline(Geom::Point3d.new(0,0,0), Geom::Point3d.new(20,10,5))
m.entities.add_cline(Geom::Point3d.new(0,0,10), Geom::Vector3d.new(0,1,1))
m.entities.add_cpoint(Geom::Point3d.new(5,5,5))
m.save(DIR + "guides.skp")

# 7. Two section planes, named/symboled, the first active.
reset(m)
f = face(m)
sp1 = m.entities.add_section_plane(Geom::Point3d.new(0,0,5), Z)
sp1.name = "Section A"
sp1.symbol = "A"
sp2 = m.entities.add_section_plane(Geom::Point3d.new(5,0,0), Geom::Vector3d.new(1,0,0))
sp2.name = "Section B"
sp2.symbol = "B"
m.entities.active_section_plane = sp1
m.save(DIR + "section_planes.skp")
report << "sections #{sp1.name}/#{sp1.symbol}/#{sp1.active?} #{sp2.name}/#{sp2.symbol}/#{sp2.active?}"

# 8. Combined stage-4 target.
reset(m)
f = face(m)
v = f.vertices.first
t1 = m.entities.add_text("Corner label", v.position, Geom::Vector3d.new(10,10,10))
t1.display_leader = true
t2 = m.entities.add_text("Face label", Geom::Point3d.new(5,5,0), Geom::Vector3d.new(8,-4,6))
t2.display_leader = true
ts = m.entities.add_text("Screen label", Geom::Point3d.new(15,15,0), Geom::Vector3d.new(30,20,0))
ts.display_leader = false
v0 = f.vertices[0]; v1 = f.vertices[1]
m.entities.add_dimension_linear(v0, v1, Geom::Vector3d.new(0,0,8))
m.entities.add_cline(Geom::Point3d.new(0,0,0), Geom::Point3d.new(20,10,5))
m.entities.add_cline(Geom::Point3d.new(0,0,10), Geom::Vector3d.new(0,1,1))
m.entities.add_cpoint(Geom::Point3d.new(5,5,5))
sp1 = m.entities.add_section_plane(Geom::Point3d.new(0,0,5), Z)
sp1.name = "Section A"; sp1.symbol = "A"
sp2 = m.entities.add_section_plane(Geom::Point3d.new(5,0,0), Geom::Vector3d.new(1,0,0))
sp2.name = "Section B"; sp2.symbol = "B"
m.entities.active_section_plane = sp1
# component with an anchored dimension, placed twice
defn = m.definitions.add("Stage4Comp")
df = defn.entities.add_face(*BOX.map { |p| Geom::Point3d.new(*p) })
defn.entities.add_dimension_linear(df.vertices[0], df.vertices[1], Geom::Vector3d.new(0,0,8))
m.entities.add_instance(defn, Geom::Transformation.new([0,30,0]))
m.entities.add_instance(defn, Geom::Transformation.new([30,30,0]) * Geom::Transformation.rotation([0,0,0], Z, Math::PI/6))
m.save(DIR + "stage4_target.skp")
report << "target saved"
report.join("\n")
