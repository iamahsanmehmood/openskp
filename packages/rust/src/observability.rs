//! Parse progress and log callbacks (`observability.hpp`).

use std::fmt;
use std::sync::Arc;

/// Severity of a log message emitted through [`ParseOptions::log`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LogLevel {
    Debug,
    Information,
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

/// Optional progress and logging callbacks. Both are invoked synchronously on
/// the calling thread.
#[derive(Clone, Default)]
pub struct ParseOptions {
    pub progress: Option<Arc<ProgressFn>>,
    pub log: Option<Arc<LogFn>>,
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
}

impl fmt::Debug for ParseOptions {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ParseOptions")
            .field("progress", &self.progress.is_some())
            .field("log", &self.log.is_some())
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
