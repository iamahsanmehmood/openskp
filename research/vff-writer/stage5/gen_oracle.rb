# stage5_gen_oracle.rb - SketchUp 2025 minimal pairs for scenes (pages) and
# the model camera, plus the stage-5 acceptance target.
DIR = "C:/Temp/vff7/"

def reset(m)
  m.entities.clear!
  m.definitions.purge_unused
  m.materials.purge_unused
  m.layers.purge_unused
  while m.pages.size > 0
    m.pages.erase(m.pages[0])
  end
  m
end

def box(m, tag = nil, offset = [0,0,0])
  pts = [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]]
  faces = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
  ox, oy, oz = offset
  faces.each do |idx|
    f = m.entities.add_face(idx.map { |i| Geom::Point3d.new(pts[i][0]+ox, pts[i][1]+oy, pts[i][2]+oz) })
    f.layer = tag if tag
  end
end

def persp_camera
  c = Sketchup::Camera.new(Geom::Point3d.new(40,-40,30), Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1))
  c.perspective = true
  c.fov = 45.0
  c
end

def parallel_camera
  c = Sketchup::Camera.new(Geom::Point3d.new(0,-60,0), Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1))
  c.perspective = false
  c.height = 80.0
  c
end

def iso_camera
  c = Sketchup::Camera.new(Geom::Point3d.new(100,-100,100), Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1))
  c.perspective = false
  c.height = 200.0
  c
end

m = Sketchup.active_model
report = []

# 1. Model camera pair: default template camera vs the standard Iso view.
reset(m)
box(m)
m.save(DIR + "camera_default.skp")
reset(m)
box(m)
m.active_view.camera = iso_camera
m.save(DIR + "camera_iso.skp")
report << "cameras saved"

# 2. One perspective scene.
reset(m)
box(m)
m.active_view.camera = persp_camera
p1 = m.pages.add("Perspective")
m.save(DIR + "scene_perspective.skp")
report << "one scene #{p1.name} use_camera=#{p1.use_camera?}"

# 3. Two scenes: parallel then perspective.
reset(m)
box(m)
m.active_view.camera = parallel_camera
pa = m.pages.add("Parallel")
m.active_view.camera = persp_camera
pb = m.pages.add("Perspective")
m.pages.selected_page = pa
m.active_view.camera = iso_camera
m.save(DIR + "scene_two.skp")
report << "two scenes #{m.pages.map(&:name).inspect} selected=#{m.pages.selected_page.name}"

# 4. Scene hiding tag B.
reset(m)
la = m.layers.add("TagA")
lb = m.layers.add("TagB")
box(m, la, [0,0,0])
box(m, lb, [20,0,0])
m.active_view.camera = persp_camera
ph = m.pages.add("Hide B")
ph.set_visibility(lb, false)
m.save(DIR + "scene_hidden.skp")
report << "hidden scene layers=#{ph.layers.map { |l| [l.name, l.visible?] }.inspect}"

# 5. Combined stage-5 target.
reset(m)
la = m.layers.add("TagA")
lb = m.layers.add("TagB")
box(m, la, [0,0,0])
box(m, lb, [20,0,0])
m.active_view.camera = persp_camera
pp = m.pages.add("Perspective")
m.active_view.camera = parallel_camera
pl = m.pages.add("Parallel")
m.active_view.camera = persp_camera
ph = m.pages.add("Hide B")
ph.set_visibility(lb, false)
m.pages.selected_page = pp
m.active_view.camera = iso_camera
m.save(DIR + "stage5_target.skp")
report << "target pages=#{m.pages.map(&:name).inspect} selected=#{m.pages.selected_page.name}"
report.join("\n")
