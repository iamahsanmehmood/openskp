m = Sketchup.active_model
report = []
report << "title=#{m.title} path=#{m.path}"
report << "model_camera=" + [m.active_view.camera.eye.to_a,
                            m.active_view.camera.target.to_a,
                            m.active_view.camera.up.to_a,
                            m.active_view.camera.perspective?,
                            m.active_view.camera.fov,
                            m.active_view.camera.height].inspect
m.pages.each_with_index do |p, i|
  cam = p.camera
  report << "page#{i}=" + [p.name, p.use_camera?, cam.eye.to_a, cam.target.to_a,
                           cam.up.to_a, cam.perspective?, cam.fov, cam.height,
                           p.use_hidden_layers?,
                           p.layers.map { |l| [l.name, l.visible?] },
                           p.use_style?, p.use_shadow_info?, p.use_axes?].inspect
end
report << "selected=" + [m.pages.selected_page&.name, m.pages.selected_page&.camera&.eye&.to_a].inspect
# Switch to each page and record the applied camera + tag visibility.  The
# test forces an instant transition so the camera is readable immediately;
# the stored pages keep the model's default transition time (-1).
m.pages.each do |p|
  p.transition_time = 0.0
  m.pages.selected_page = p
  sleep 0.2
  cam = m.active_view.camera
  report << "applied #{p.name}=" + [m.pages.selected_page.name, cam.eye.to_a,
                                    cam.target.to_a, cam.perspective?, cam.fov,
                                    cam.height,
                                    m.layers.map { |l| [l.name, l.visible?] }].inspect
end
report.join("\n")
