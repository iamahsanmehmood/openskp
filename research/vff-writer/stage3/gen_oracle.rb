# stage3_gen_oracle.rb - SketchUp 2025 minimal pairs for arcs/circles/
# polylines and attribute dictionaries, plus the stage-3 acceptance target.
DIR = "C:/Temp/vff5/"
BOX = [[0,0,0],[10,0,0],[10,10,0],[0,10,0]]
Z = Geom::Vector3d.new(0,0,1)
X = Geom::Vector3d.new(1,0,0)

def reset(m)
  m.entities.clear!
  m.definitions.purge_unused
  m.materials.purge_unused
  m.layers.purge_unused
  m
end

def circle_face(ents, center, radius, segments = 8)
  edges = ents.add_circle(center, Z, radius, segments)
  face = ents.add_face(edges)
  [edges, face]
end

def punch_hole(ents, center, outer_radius, hole_radius, segments = 12)
  outer, face = circle_face(ents, center, outer_radius, segments)
  ents.add_circle(center, Z, hole_radius, segments - 4)
  # A loop drawn on a face makes an inner disk face as well; delete that
  # face so the outer face keeps the hole (the edges stay).
  inner = ents.grep(Sketchup::Face).find { |f| f != face }
  ents.erase_entities(inner) if inner
  [outer, face]
end

def attrs(entity)
  entity.set_attribute("stage3", "string", "hello")
  entity.set_attribute("stage3", "int", 42)
  entity.set_attribute("stage3", "float", 1.5)
  entity.set_attribute("stage3", "bool_true", true)
  entity.set_attribute("stage3", "bool_false", false)
  entity.set_attribute("stage3", "point", Geom::Point3d.new(1, 2, 3))
  entity.set_attribute("stage3", "vector", Geom::Vector3d.new(4, 5, 6))
  entity.set_attribute("stage3", "length", 5.feet)
  entity.set_attribute("stage3", "timestamp", Time.at(1700000000))
  entity.set_attribute("stage3", "array", [1, "two", 3.5, true])
  entity.set_attribute("stage3", "nested", [1, [2, [3, 4]]])
  entity.set_attribute("stage3", "nothing", nil)
  entity.set_attribute("stage3_other", "key", "value")
end

m = Sketchup.active_model
report = []

# 1. Open arc.
reset(m)
e = m.entities.add_arc(Geom::Point3d.new(0,0,0), X, Z, 10, 0, Math::PI/2, 6)
m.save(DIR + "arc_open.skp")
report << "arc edges=#{e.length} curve=#{e.first.curve.class} radius=#{e.first.curve.radius}"

# 2. Circle with its face.
reset(m)
c, f = circle_face(m.entities, Geom::Point3d.new(0,0,0), 10, 8)
m.save(DIR + "circle_face.skp")
report << "circle edges=#{c.length} loops=#{f.loops.length} area=#{f.area.round(3)}"

# 3. Circle as a hole in a face.
reset(m)
o, hf = punch_hole(m.entities, Geom::Point3d.new(0,0,0), 20, 10, 12)
m.save(DIR + "circle_hole.skp")
report << "hole loops=#{hf.loops.length} facecount=#{m.entities.grep(Sketchup::Face).length} area=#{hf.area.round(3)}"

# 4. Polyline.
reset(m)
p = m.entities.add_curve([Geom::Point3d.new(0,0,0), Geom::Point3d.new(10,10,0),
                          Geom::Point3d.new(20,0,0), Geom::Point3d.new(30,10,0)])
m.save(DIR + "polyline.skp")
report << "polyline edges=#{p.length} curve=#{p.first.curve.class}"

# 5. Curves inside a component, placed twice.
reset(m)
d = m.definitions.add("CurveComp")
cc, cf = circle_face(d.entities, Geom::Point3d.new(0,0,0), 8, 8)
d.entities.add_arc(Geom::Point3d.new(20,0,0), X, Z, 8, 0, Math::PI, 6)
d.entities.add_curve([Geom::Point3d.new(0,20,0), Geom::Point3d.new(8,24,0), Geom::Point3d.new(16,20,0)])
t1 = Geom::Transformation.new([0, 40, 0])
t2 = Geom::Transformation.new([40, 40, 0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI/6)
m.entities.add_instance(d, t1)
m.entities.add_instance(d, t2)
m.save(DIR + "curve_component.skp")
report << "component defs=#{m.definitions.length}"

# 6. Attributes of every type on face, definition, instance, group and model.
reset(m)
face = m.entities.add_face(BOX)
attrs(face)
cdef = m.definitions.add("AttrComp")
cdef.entities.add_face(BOX)
attrs(cdef)
inst = m.entities.add_instance(cdef, Geom::Transformation.new([20,0,0]))
attrs(inst)
g = m.entities.add_group
g.entities.add_face(BOX)
attrs(g)
attrs(m)
m.save(DIR + "attributes.skp")
report << "attrs saved"

# 7. Texture plus attributes on one face (record ordering inside 0x05dd).
reset(m)
mat = m.materials.add("TexPNG")
mat.texture = "C:/Temp/vff2/tex.png"
mat.texture.size = [10, 5]
tf = m.entities.add_face(BOX)
tf.material = mat
pins = [[Geom::Point3d.new(0,0,0), Geom::Point3d.new(0,0,0)],
        [Geom::Point3d.new(10,0,0), Geom::Point3d.new(10,0,0)],
        [Geom::Point3d.new(0,10,0), Geom::Point3d.new(0,5,0)]]
tf.position_material(mat, pins.flatten, true)
attrs(tf)
m.save(DIR + "attrs_texture.skp")

# 8. Combined stage-3 target.
reset(m)
attrs(m)
tex = m.materials.add("StageTex")
tex.texture = "C:/Temp/vff2/tex.png"
tex.texture.size = [10, 5]
texf = m.entities.add_face([[-40,0,0],[-30,0,0],[-30,10,0],[-40,10,0]])
texf.material = tex
# open arc
m.entities.add_arc(Geom::Point3d.new(0,0,0), X, Z, 10, 0, Math::PI/2, 6)
# circle face with a circular hole
oh, hf = punch_hole(m.entities, Geom::Point3d.new(30,0,0), 12, 6, 12)
attrs(hf)
# polyline
pl = m.entities.add_curve([Geom::Point3d.new(0,20,0), Geom::Point3d.new(10,30,0),
                           Geom::Point3d.new(20,20,0), Geom::Point3d.new(30,30,0)])
# component with a curve inside, placed twice
outer = m.definitions.add("StageCurveComp")
cc, cf = circle_face(outer.entities, Geom::Point3d.new(0,0,0), 8, 8)
outer.entities.add_arc(Geom::Point3d.new(20,0,0), X, Z, 8, 0, Math::PI, 6)
outer.entities.add_curve([Geom::Point3d.new(0,20,0), Geom::Point3d.new(8,24,0), Geom::Point3d.new(16,20,0)])
attrs(outer)
i1 = m.entities.add_instance(outer, Geom::Transformation.new([0,40,0]))
attrs(i1)
i2 = m.entities.add_instance(outer, Geom::Transformation.new([40,40,0]) * Geom::Transformation.rotation([0,0,0], [0,0,1], Math::PI/6))
# group with attributes
g = m.entities.add_group
g.entities.add_face(BOX)
attrs(g)
m.save(DIR + "stage3_target.skp")
report << "target saved"
report.join("\n")
