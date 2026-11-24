//! Parse progress and log callbacks (`observability.hpp`).

use std::fmt;
use std::sync::Arc;

/// Severity of a log message emitted through [`ParseOptions::log`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LogLevel {
    Debug,
    Information,
    Warning,
}

/// The stage of the parse pipeline a progress report or error refers to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ParseStage {
    Header,
    ZipExtract,
    TlvWalk,
    LegacyWalk,
    LegacyDefs,
    BuildScene,
}

impl ParseStage {
    /// The stage's snake_case name, as it appears in error messages.
    pub const fn name(self) -> &'static str {
        stage_name(self)
    }
}

/// The stage's snake_case name (`"header"`, `"zip_extract"`, ...).
pub const fn stage_name(s: ParseStage) -> &'static str {
    match s {
        ParseStage::Header => "header",
        ParseStage::ZipExtract => "zip_extract",
        ParseStage::TlvWalk => "tlv_walk",
        ParseStage::LegacyWalk => "legacy_walk",
        ParseStage::LegacyDefs => "legacy_defs",
        ParseStage::BuildScene => "build_scene",
    }
}

impl fmt::Display for ParseStage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(stage_name(*self))
    }
}

/// One progress report: `current` of `total` units done in `stage`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParseProgress {
    pub stage: ParseStage,
    pub current: u64,
    pub total: u64,
}

/// Progress callback type.
pub type ProgressFn = dyn Fn(&ParseProgress) + Send + Sync;
/// Log callback type.
pub type LogFn = dyn Fn(LogLevel, &str) + Send + Sync;

/// Optional progress and logging callbacks, plus scene-building options.
/// Callbacks are invoked synchronously on the calling thread.
#[derive(Clone)]
pub struct ParseOptions {
    pub progress: Option<Arc<ProgressFn>>,
    pub log: Option<Arc<LogFn>>,
    /// `"style"` (default) gives unpainted faces the model's front and back
    /// face colours; `"tag"` gives them their tag's colour.
    pub unpainted_faces: String,
    /// Keeps a texture that is not PNG or JPEG in `Scene::textures` and
    /// `InstancedScene::textures` in its own format (`image/tiff`, `image/gif`,
    /// `image/bmp`, `image/x-tga`, `image/webp`, `image/vnd.adobe.photoshop`,
    /// `image/vnd.ms-dds`, `image/jp2`) instead of leaving it out with a
    /// warning. Off by default because glTF carries only PNG and JPEG.
    pub original_textures: bool,
}

impl Default for ParseOptions {
    fn default() -> Self {
        Self {
            progress: None,
            log: None,
            unpainted_faces: "style".to_owned(),
            original_textures: false,
        }
    }
}

impl ParseOptions {
    /// Options with no callbacks.
    pub fn new() -> Self {
        Self::default()
    }

    /// Sets the progress callback.
    pub fn with_progress(mut self, f: impl Fn(&ParseProgress) + Send + Sync + 'static) -> Self {
        self.progress = Some(Arc::new(f));
        self
    }

    /// Sets the log callback.
    pub fn with_log(mut self, f: impl Fn(LogLevel, &str) + Send + Sync + 'static) -> Self {
        self.log = Some(Arc::new(f));
        self
    }

    /// Sets how unpainted faces are coloured (`"style"` or `"tag"`).
    pub fn with_unpainted_faces(mut self, mode: impl Into<String>) -> Self {
        self.unpainted_faces = mode.into();
        self
    }

    /// Sets whether non-PNG/JPEG textures are kept in their own format.
    pub fn with_original_textures(mut self, keep: bool) -> Self {
        self.original_textures = keep;
        self
    }
}

impl fmt::Debug for ParseOptions {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ParseOptions")
            .field("progress", &self.progress.is_some())
            .field("log", &self.log.is_some())
            .field("unpainted_faces", &self.unpainted_faces)
            .field("original_textures", &self.original_textures)
            .finish()
    }
}

/// Progress is reported every this many records (and on the last one).
pub const PROGRESS_INTERVAL: u64 = 500;

/// Invokes the log callback, if any (`emit_log`).
pub fn emit_log(o: &ParseOptions, level: LogLevel, message: &str) {
    if let Some(log) = &o.log {
        log(level, message);
    }
}

/// Invokes the progress callback, if any (`emit_progress`).
pub fn emit_progress(o: &ParseOptions, stage: ParseStage, current: u64, total: u64) {
    if let Some(progress) = &o.progress {
        progress(&ParseProgress {
            stage,
            current,
            total,
        });
    }
}
