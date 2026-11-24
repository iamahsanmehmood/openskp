# gen_oracle.rb - minimal pairs and the acceptance target, saved by SketchUp 2025.
DIR = "C:/Temp/vff/"

def reset_model(m)
  m.entities.clear!
  m.definitions.purge_unused
  m.materials.purge_unused
  m.layers.purge_unused
  m
end

BOX8 = [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]]
BOX_FACES = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]

def add_box(ents, origin = [0,0,0], size = 10.0)
  ox, oy, oz = origin
  pts = BOX8.map { |p| Geom::Point3d.new(ox + p[0], oy + p[1], oz + p[2]) }
  faces = BOX_FACES.map { |idx| ents.add_face(idx.map { |i| pts[i] }) }
  faces
end

m = Sketchup.active_model
report = []

# 1. blank
reset_model(m)
m.save(DIR + "blank.skp")
report << "blank ok"

# 2. one face
reset_model(m)
f = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
m.save(DIR + "face.skp")
report << "face: #{f ? 'ok' : 'nil'} entities=#{m.entities.length}"

# 3. one face + material
reset_model(m)
f = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
mat = m.materials.add("SpikeRed")
mat.color = Sketchup::Color.new(220, 30, 40)
mat.alpha = 0.75
f.material = mat
m.save(DIR + "face_material.skp")
report << "face_material: mat=#{mat.name} color=#{mat.color.to_a.inspect} alpha=#{mat.alpha} material_xml_exists=#{File.exist?(DIR+'material_xml_probe')}"

# 4. one face + tag
reset_model(m)
f = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
tag = m.layers.add("SpikeTag")
m.active_layer = tag
f.layer = tag
f.edges.each { |e| e.layer = tag }
m.save(DIR + "face_tag.skp")
report << "face_tag: layer=#{f.layer.name} edges=#{f.edges.length}"

# 5. component definition with a box, two instances
reset_model(m)
cdef = m.definitions.add("SpikeBox")
add_box(cdef.entities)
t1 = Geom::Transformation.new([20, 0, 0])
t2 = Geom::Transformation.new([0, 30, 0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI / 4.0)
i1 = m.entities.add_instance(cdef, t1)
i2 = m.entities.add_instance(cdef, t2)
m.save(DIR + "component.skp")
report << "component: defs=#{m.definitions.length} inst=#{m.entities.length} t2=#{i2.transformation.to_a.inspect}"

# 6. the acceptance target: box + material + tag + component with two instances
reset_model(m)
faces = add_box(m.entities)
mat = m.materials.add("SpikeRed")
mat.color = Sketchup::Color.new(220, 30, 40)
tag = m.layers.add("SpikeTag")
m.active_layer = tag
faces.each_with_index do |ff, i|
  ff.material = mat if i < 4
  ff.layer = tag
  ff.edges.each { |e| e.layer = tag }
end
cdef = m.definitions.add("SpikeBox")
add_box(cdef.entities)
t1 = Geom::Transformation.new([20, 0, 0])
t2 = Geom::Transformation.new([0, 30, 0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI / 4.0)
m.entities.add_instance(cdef, t1)
m.entities.add_instance(cdef, t2)
m.save(DIR + "target.skp")
report << "target: entities=#{m.entities.length} faces=#{m.entities.grep(Sketchup::Face).length} edges=#{m.entities.grep(Sketchup::Edge).length} verts=#{m.entities.grep(Sketchup::Vertex).length} defs=#{m.definitions.length} mats=#{m.materials.map(&:name).inspect} layers=#{m.layers.map(&:name).inspect}"

report.join("\n")
