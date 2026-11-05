//! Public entry points (`parser.hpp` / `parser.cpp`).

use std::io;
use std::path::Path;

use crate::errors::SkpParseError;
use crate::instanced_scene::InstancedScene;
use crate::instanced_scene_build::build_instanced_scene_raw;
use crate::internal::{build_model, full_parse};
use crate::model::{ByteBuffer, SkpModel};
use crate::observability::ParseOptions;
use crate::scene::Scene;
use crate::scene_build::build_scene_raw;

/// Parses an SKP buffer into its definitions, geometry, materials, layers,
/// and styles. Callbacks in `options` run synchronously on this thread.
pub fn parse_skp(buffer: &[u8], options: &ParseOptions) -> Result<SkpModel, SkpParseError> {
    Ok(build_model(full_parse(buffer, options)?, options))
}

/// Parses an SKP buffer and bakes it into world-space, GLB-ready scene data.
pub fn build_scene(buffer: &[u8], options: &ParseOptions) -> Result<Scene, SkpParseError> {
    build_scene_raw(full_parse(buffer, options)?, options)
}

/// Parses an SKP buffer and builds the placed scene graph with SketchUp's
/// component/group instancing PRESERVED instead of baked into world-space
/// vertex data: output grows with unique geometry + instance transforms
/// rather than definition geometry x placement count.
pub fn build_instanced_scene(
    buffer: &[u8],
    options: &ParseOptions,
) -> Result<InstancedScene, SkpParseError> {
    build_instanced_scene_raw(full_parse(buffer, options)?, options)
}

/// In-memory handle for repeatedly parsing or baking one SKP source. Each call
/// starts from the original bytes and performs an independent parse.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkpFile {
    data: ByteBuffer,
}

impl SkpFile {
    /// Reads an SKP file into memory, checking the (case-insensitive) `.skp`
    /// extension; format validation is deferred to `parse()`.
    ///
    /// Errors: `NotFound` when the path does not exist, `InvalidInput` for
    /// another extension, and the underlying error when reading fails.
    pub fn open(path: impl AsRef<Path>) -> io::Result<SkpFile> {
        let full = std::path::absolute(path.as_ref())?;
        if !full.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("File not found: {}", full.display()),
            ));
        }
        let ext = full
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if ext != ".skp" {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("Expected a .skp file, got: {ext}"),
            ));
        }
        let data = std::fs::read(&full).map_err(|e| {
            io::Error::new(e.kind(), format!("Cannot open: {}: {e}", full.display()))
        })?;
        Ok(SkpFile { data })
    }

    /// A handle owning bytes already in memory.
    pub fn from_buffer(buffer: ByteBuffer) -> SkpFile {
        SkpFile { data: buffer }
    }

    /// The source bytes.
    pub fn data(&self) -> &[u8] {
        &self.data
    }

    /// Parses the stored source into a new model.
    pub fn parse(&self, options: &ParseOptions) -> Result<SkpModel, SkpParseError> {
        parse_skp(&self.data, options)
    }

    /// Parses the stored source and bakes a new world-space scene.
    pub fn build_scene(&self, options: &ParseOptions) -> Result<Scene, SkpParseError> {
        build_scene(&self.data, options)
    }

    /// Parses the stored source and builds a new instanced scene.
    pub fn build_instanced_scene(
        &self,
        options: &ParseOptions,
    ) -> Result<InstancedScene, SkpParseError> {
        build_instanced_scene(&self.data, options)
    }
}
