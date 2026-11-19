//! Structured parse errors (`errors.hpp` / `errors.cpp`).

use std::error::Error;
use std::fmt;
use std::sync::Arc;

use crate::observability::{stage_name, ParseStage};

/// The underlying error an [`SkpParseError`] wraps (C++'s `std::exception_ptr`).
pub type ErrorCause = Arc<dyn Error + Send + Sync + 'static>;

/// A parse failure with optional context: the pipeline stage, the record being
/// processed, its tag/offset, the definition, and the underlying cause.
///
/// `Display` renders the same text as the C++ `what()`:
/// `message | stage=... | record=r/t | tag=... | offset=0x... | definitionId=...`.
#[derive(Clone)]
pub struct SkpParseError(Box<Inner>);

#[derive(Clone)]
struct Inner {
    message: String,
    stage: Option<ParseStage>,
    record_index: Option<u64>,
    total_records: Option<u64>,
    tag: Option<String>,
    offset: Option<u64>,
    definition_id: Option<i64>,
    cause: Option<ErrorCause>,
}

impl SkpParseError {
    /// An error with just a message.
    pub fn new(message: impl Into<String>) -> Self {
        Self::with_context(message, None, None, None, None, None, None, None)
    }

    /// An error with a message and the stage it happened in.
    pub fn at_stage(message: impl Into<String>, stage: ParseStage) -> Self {
        Self::new(message).with_stage(stage)
    }

    /// Every field at once, in the C++ constructor's order.
    #[allow(clippy::too_many_arguments)]
    pub fn with_context(
        message: impl Into<String>,
        stage: Option<ParseStage>,
        record_index: Option<u64>,
        total_records: Option<u64>,
        tag: Option<String>,
        offset: Option<u64>,
        definition_id: Option<i64>,
        cause: Option<ErrorCause>,
    ) -> Self {
        Self(Box::new(Inner {
            message: message.into(),
            stage,
            record_index,
            total_records,
            tag,
            offset,
            definition_id,
            cause,
        }))
    }

    pub fn with_stage(mut self, stage: ParseStage) -> Self {
        self.0.stage = Some(stage);
        self
    }

    pub fn with_record(mut self, index: u64, total: u64) -> Self {
        self.0.record_index = Some(index);
        self.0.total_records = Some(total);
        self
    }

    pub fn with_tag(mut self, tag: impl Into<String>) -> Self {
        self.0.tag = Some(tag.into());
        self
    }

    pub fn with_offset(mut self, offset: u64) -> Self {
        self.0.offset = Some(offset);
        self
    }

    pub fn with_definition_id(mut self, id: i64) -> Self {
        self.0.definition_id = Some(id);
        self
    }

    pub fn with_cause<E: Error + Send + Sync + 'static>(mut self, cause: E) -> Self {
        self.0.cause = Some(Arc::new(cause));
        self
    }

    pub fn with_cause_arc(mut self, cause: ErrorCause) -> Self {
        self.0.cause = Some(cause);
        self
    }

    /// The bare message, without the formatted context.
    pub fn message(&self) -> &str {
        &self.0.message
    }

    pub fn stage(&self) -> Option<ParseStage> {
        self.0.stage
    }

    pub fn record_index(&self) -> Option<u64> {
        self.0.record_index
    }

    pub fn total_records(&self) -> Option<u64> {
        self.0.total_records
    }

    pub fn tag(&self) -> Option<&str> {
        self.0.tag.as_deref()
    }

    pub fn offset(&self) -> Option<u64> {
        self.0.offset
    }

    pub fn definition_id(&self) -> Option<i64> {
        self.0.definition_id
    }

    /// The wrapped underlying error, if any.
    pub fn cause(&self) -> Option<&ErrorCause> {
        self.0.cause.as_ref()
    }

    /// The full formatted text (C++ `what()`).
    pub fn what(&self) -> String {
        self.to_string()
    }
}

impl fmt::Display for SkpParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let e = &self.0;
        f.write_str(&e.message)?;
        if let Some(s) = e.stage {
            write!(f, " | stage={}", stage_name(s))?;
        }
        if let (Some(r), Some(t)) = (e.record_index, e.total_records) {
            write!(f, " | record={r}/{t}")?;
        }
        if let Some(tag) = &e.tag {
            write!(f, " | tag={tag}")?;
        }
        if let Some(o) = e.offset {
            write!(f, " | offset=0x{o:x}")?;
        }
        if let Some(d) = e.definition_id {
            write!(f, " | definitionId={d}")?;
        }
        Ok(())
    }
}

impl fmt::Debug for SkpParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut d = f.debug_struct("SkpParseError");
        d.field("what", &self.to_string());
        if let Some(c) = &self.0.cause {
            d.field("cause", &c.to_string());
        }
        d.finish()
    }
}

impl Error for SkpParseError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        self.0
            .cause
            .as_ref()
            .map(|c| c.as_ref() as &(dyn Error + 'static))
    }
}

/// Crate-wide result alias.
pub type Result<T, E = SkpParseError> = std::result::Result<T, E>;
