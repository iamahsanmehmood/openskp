# stage6_gen_source.rb - a genuine SketchUp 2025 document with a component,
# texture, scene and attributes, used as the source for open_existing().
DIR = "C:/Temp/vff8/"
BOX = [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]]
FACES = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]

m = Sketchup.active_model
m.entities.clear!
m.definitions.purge_unused
m.materials.purge_unused
while m.pages.size > 0
  m.pages.erase(m.pages[0])
end

def add_box(ents, origin = [0,0,0])
  ox, oy, oz = origin
  pts = BOX.map { |p| Geom::Point3d.new(p[0]+ox, p[1]+oy, p[2]+oz) }
  FACES.each do |idx|
    f = ents.add_face(idx.map { |i| pts[i] })
    yield f if block_given?
  end
end

# Component.
comp = m.definitions.add("SourceComp")
add_box(comp.entities)
comp.set_attribute("source", "kind", "component")
inst = m.entities.add_instance(comp, Geom::Transformation.new([20,0,0]))
inst.set_attribute("source", "kind", "instance")

# Textured face.
mat = m.materials.add("SourceTex")
mat.texture = "C:/Temp/vff2/tex.png"
mat.texture.size = [10,5]
face = m.entities.add_face([[-20,0,0],[-10,0,0],[-10,10,0],[-20,10,0]])
face.material = mat
face.set_attribute("source", "kind", "face")
face.set_attribute("source", "note", "kept by open_existing")

# Scene.
cam = Sketchup::Camera.new(Geom::Point3d.new(40,-40,30), Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1))
cam.perspective = true
cam.fov = 45.0
m.active_view.camera = cam
m.pages.add("SourceScene")

m.save(DIR + "source.skp")
[Sketchup.version, m.definitions.length, m.entities.length, m.materials.map(&:name), m.pages.map(&:name)].inspect
