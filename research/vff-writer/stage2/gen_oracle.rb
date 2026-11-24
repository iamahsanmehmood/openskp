# stage2_gen_oracle.rb - SketchUp 2025 minimal pairs for textured materials,
# groups and nested placements, plus entry-count variants for the integrity
# constant and the combined acceptance target.
DIR = "C:/Temp/vff4/"
BOX8 = [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]]
BOX_FACES = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]

def reset_model(m)
  m.entities.clear!
  m.definitions.purge_unused
  m.materials.purge_unused
  m.layers.purge_unused
  m
end

def add_box(ents, origin = [0,0,0])
  ox, oy, oz = origin
  pts = BOX8.map { |p| Geom::Point3d.new(ox + p[0], oy + p[1], oz + p[2]) }
  BOX_FACES.map { |idx| ents.add_face(idx.map { |i| pts[i] }) }
end

def solid(m, name, rgb)
  mat = m.materials.add(name)
  mat.color = Sketchup::Color.new(*rgb)
  mat
end

def textured(m, name, file, w, h)
  mat = m.materials.add(name)
  mat.texture = file
  mat.texture.size = [w, h]
  mat
end

m = Sketchup.active_model
report = []

# 1. Entry-count variants: blank + N solid materials -> 7 + N archive entries.
# (Every material is painted on a face: SketchUp 2025 hangs while saving a
# model that holds unused materials, so the count variants use them.)
[0, 2, 4, 6, 8].each do |count|
  reset_model(m)
  count.times do |i|
    mat = solid(m, "CountMat#{i}", [10 + i, 20, 30])
    face = m.entities.add_face([i * 20, 0, 0], [i * 20 + 10, 0, 0],
                              [i * 20 + 10, 10, 0], [i * 20, 10, 0])
    face.material = mat
  end
  m.save(DIR + "count_#{count}.skp")
end
report << "counts saved"

# 2. PNG texture, default placement.
reset_model(m)
png = textured(m, "TexPNG", "C:/Temp/vff2/tex.png", 10, 5)
face_png = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
face_png.material = png
m.save(DIR + "tex_png.skp")

# 3. JPEG texture, default placement.
reset_model(m)
jpg = textured(m, "TexJPEG", "C:/Temp/vff2/tex.jpg", 8, 4)
face_jpg = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
face_jpg.material = jpg
m.save(DIR + "tex_jpeg.skp")

# 4. Same PNG texture with an explicitly moved/rotated placement.
reset_model(m)
png = textured(m, "TexPNG", "C:/Temp/vff2/tex.png", 10, 5)
face = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
face.material = png
p0 = Geom::Point3d.new(0,0,0)
p1 = Geom::Point3d.new(10,0,0)
p2 = Geom::Point3d.new(0,10,0)
t0 = Geom::Point3d.new(2,1,0)
t1 = Geom::Point3d.new(2 + 10*Math.cos(Math::PI/4), 1 + 10*Math.sin(Math::PI/4), 0)
t2 = Geom::Point3d.new(2 - 5*Math.sin(Math::PI/4), 1 + 5*Math.cos(Math::PI/4), 0)
face.position_material(png, [p0,t0,p1,t1,p2,t2], true)
m.save(DIR + "tex_uv.skp")
report << "textures saved"

# 5. Group vs component of identical geometry (root level).
reset_model(m)
g = m.entities.add_group
add_box(g.entities)
cdef = m.definitions.add("CompBox")
add_box(cdef.entities)
m.entities.add_instance(cdef, Geom::Transformation.new([20,0,0]))
m.save(DIR + "group_vs_component.skp")

# 6. Root group that is painted and on a tag, named.
reset_model(m)
red = solid(m, "GroupRed", [200, 20, 20])
tag = m.layers.add("GroupTag")
g = m.entities.add_group
add_box(g.entities)
g.name = "RootBox"
g.material = red
g.layer = tag
m.save(DIR + "group_painted.skp")

# 7. Group nested inside a component; the component is placed twice.
reset_model(m)
blue = solid(m, "InnerBlue", [20, 40, 220])
tag = m.layers.add("InnerTag")
outer = m.definitions.add("Outer")
inner = outer.entities.add_group
add_box(inner.entities)
inner.name = "InnerBox"
inner.material = blue
inner.layer = tag
t1 = Geom::Transformation.new([0, 0, 0])
t2 = Geom::Transformation.new([30, 0, 0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI / 6.0)
m.entities.add_instance(outer, t1)
m.entities.add_instance(outer, t2)
m.save(DIR + "group_nested.skp")
report << "groups saved"

# 8. Combined acceptance target.
reset_model(m)
png = textured(m, "TexPNG", "C:/Temp/vff2/tex.png", 10, 5)
jpg = textured(m, "TexJPEG", "C:/Temp/vff2/tex.jpg", 8, 4)
red = solid(m, "GroupRed", [200, 20, 20])
tag = m.layers.add("GroupTag")
inner_tag = m.layers.add("InnerTag")

# two textured faces, the PNG one with a moved/rotated placement
face_a = m.entities.add_face([0,0,0],[10,0,0],[10,10,0],[0,10,0])
face_a.material = png
p0 = Geom::Point3d.new(0,0,0); p1 = Geom::Point3d.new(10,0,0); p2 = Geom::Point3d.new(0,10,0)
t0 = Geom::Point3d.new(2,1,0)
t1 = Geom::Point3d.new(2 + 10*Math.cos(Math::PI/4), 1 + 10*Math.sin(Math::PI/4), 0)
t2 = Geom::Point3d.new(2 - 5*Math.sin(Math::PI/4), 1 + 5*Math.cos(Math::PI/4), 0)
face_a.position_material(png, [p0,t0,p1,t1,p2,t2], true)
face_b = m.entities.add_face([20,0,0],[30,0,0],[30,10,0],[20,10,0])
face_b.material = jpg

# root group, painted and on a tag
root_group = m.entities.add_group
add_box(root_group.entities, [0, 20, 0])
root_group.name = "RootBox"
root_group.material = red
root_group.layer = tag

# component with a nested group, placed twice
outer = m.definitions.add("Outer")
inner = outer.entities.add_group
add_box(inner.entities)
inner.name = "InnerBox"
inner.material = red
inner.layer = inner_tag
m.entities.add_instance(outer, Geom::Transformation.new([0, 40, 0]))
m.entities.add_instance(outer, Geom::Transformation.new([40, 40, 0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI / 6.0))
m.save(DIR + "stage2_target.skp")
report << "target saved"
report << [m.entities.length, m.definitions.length, m.materials.map(&:name).inspect, m.layers.map(&:name).inspect].inspect
report.join("\n")
