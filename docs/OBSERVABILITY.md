# Observability: progress and structured errors

Every OpenSKP port — Python, TypeScript, .NET, Dart — exposes the same two
things about a parse or scene-bake in progress:

1. **Progress** — how far through the file the walk has gotten.
2. **Structured errors** — exactly where a failure happened, if one does.

Neither is turned on by default. OpenSKP is a library, not an application:
it never decides for you how (or whether) to log, and it never prints or
writes anything on its own. You opt in, in whichever form is idiomatic for
your language, and wire it into whatever logging/monitoring your own
application already uses.

This document is the detailed reference for that feature. For a quick
"how do I call this" per language, see the
[Developer Guide](DEVELOPER_GUIDE.md#observability); this page covers the
design, the full field/stage vocabulary, and how to actually get useful
output out of it in a production pipeline.

---

## Why this exists

A `.skp` file can be a few KB or many hundred MB, and a production pipeline
that ingests them from untrusted uploads needs to answer two operational
questions that a bare try/catch can't:

- **Is it still working, or is it stuck?** A parse that's been running for
  ten minutes on a 600 MB file might be perfectly normal, or it might be
  wedged. Without progress, you can't tell the difference from the outside.
- **Where, exactly, did it fail?** "It threw" is not actionable at 2 AM.
  "It failed in the TLV walk, on top-level record 48,113 of 210,004, tag
  `7C15`" is something you can act on: check whether that record is
  corrupt, whether it's a new/unhandled tag, or whether it's a genuine bug.

Both were built as a single feature, once, in Python, then ported to
TypeScript, .NET, and Dart in each language's own idiomatic form — not a
shared library, not a lowest-common-denominator callback shape bolted onto
four languages. The *semantics* are identical everywhere (same stages, same
500-unit progress granularity, same error fields); the *mechanism* isn't.

## Design principles

- **Silent by default.** No handler, no logger, no output of any kind
  unless the caller explicitly asks for it. Matches how mature libraries in
  each ecosystem behave (Python's `requests`/`urllib3`, for instance).
- **Two independent channels.** Progress (a periodic "N of M" signal) and
  logging (start/stage/completion messages, at two severities) are separate
  hooks. A caller who only wants a progress bar doesn't have to parse log
  strings for it, and a caller who only wants log lines doesn't get spammed
  by a numeric callback firing 500 times.
- **Errors carry location, not just a message.** Every exception OpenSKP
  raises out of the parsing/scene-building path is (or wraps) a structured
  error type with a fixed set of optional fields (below) — never a bare
  string.
- **The original failure is never thrown away.** When OpenSKP wraps a
  lower-level error to add location context, the original is always
  reachable: Python's `raise ... from exc` (`__cause__`), TypeScript's
  `cause` property, .NET's `InnerException`, Dart's `cause` field. Adding
  context never means losing the stack trace that actually explains the
  bug.
- **Progress costs nothing until you ask for it.** Reporting is gated by a
  simple modulo check (see [Granularity](#granularity-why-500) below), not
  a timer or a separate thread — a caller who passes no callback pays
  nothing beyond that one integer comparison per record.

## The stage vocabulary

Every structured error and every progress update carries a `stage` (or
`Stage`) string. It's the same fixed set of six values in all four
languages:

| Stage | Meaning | Raised from |
|---|---|---|
| `header` | The file doesn't start with the VFF magic marker (`FF FE FF 0E`) — not a `.skp` file, or a format OpenSKP doesn't recognize at all. | The very first bytes read. |
| `zip_extract` | The file has a valid header but no embedded ZIP could be found or opened, or the ZIP has no `model.dat` entry. | Right after header validation, VFF path only. |
| `tlv_walk` | A failure while walking `model.dat`'s top-level TLV records (the modern VFF/2021+ path). Carries `recordIndex`/`totalRecords`/`tag`. | The main per-record loop. |
| `legacy_walk` | A failure while walking the classic MFC `CArchive` object stream (SketchUp 2013–2020 files). | The legacy archive walker. |
| `legacy_defs` | A failure while converting walked legacy objects into component definitions. Carries `definitionId` (the archive slot index being built). | The legacy definitions loop. |
| `build_scene` | A failure while baking placed instances into a triangulated, world-space scene — almost always a triangulation failure on a malformed face. Carries `definitionId`. | The opt-in `build_scene()`/`buildScene()`/`BuildScene()` call. |

`header` and `zip_extract` only ever apply to the modern VFF/2021+ path (the
legacy MFC format has no ZIP container to extract, so a legacy file that
fails validation goes straight to `legacy_walk`). `tlv_walk` and
`legacy_walk`/`legacy_defs` are mutually exclusive per parse — OpenSKP
detects which container era a file uses before choosing a walker, so you
only ever see one or the other for a given file.

## Error fields

| Field | Type | Set for | Meaning |
|---|---|---|---|
| `stage` | string | always | One of the six values above. |
| `recordIndex` | int | `tlv_walk` | 0-based index of the top-level record being processed. |
| `totalRecords` | int | `tlv_walk` | Total top-level record count — pairs with `recordIndex` for "N of M". |
| `tag` | string | `tlv_walk` | The TLV tag hex string (e.g. `"7C15"`) of the record being processed. |
| `offset` | int | (reserved) | Byte offset into `model.dat`, when known. |
| `definitionId` | int | `legacy_defs`, `build_scene` | The definition (archive slot / TLV entity ID) being built when the failure happened. |
| the original error | — | always, when wrapping | Python: `__cause__` (via `raise ... from exc`). TypeScript: `.cause`. .NET: `.InnerException`. Dart: `.cause`. |

Only the fields relevant to the failure's stage are set; the rest are
`null`/`None`/absent. The error's string representation includes every
field that's set, e.g.:

```
Failed while processing top-level record: ... | stage=tlv_walk | record=48113/210004 | tag=7C15
```

## Granularity: why 500

Progress fires every **500** units (top-level records, legacy component
definitions, or placed instances, depending on stage) — plus always once
more at the very last unit, so a caller watching for "100%" actually sees
it. This number is intentionally coarse:

- It's cheap enough to cost nothing on files with 100,000+ top-level
  records or component definitions (real production files in this range
  exist — see the [Developer Guide](DEVELOPER_GUIDE.md#performance) for
  measured examples) — you're not paying for a callback per vertex or per
  face, only per few-hundred records.
- It's frequent enough that a caller watching for a stuck pipeline gets a
  useful signal well before a human would give up waiting.

There's no configuration knob for this interval by design — if you need a
different cadence, derive it from the `current`/`total` values you already
get (e.g., only act every Nth callback yourself).

## Per-language mechanism

| Language | Progress | Logging | Error type |
|---|---|---|---|
| Python | n/a — see below | `logging.getLogger("openskp")` (+ `"openskp.legacy"`, `"openskp.scene"` children) | `openskp.SkpParseError(Exception)` |
| TypeScript | `options.onProgress?: (info: ProgressInfo) => void` | `options.onLog?: (level, message) => void` | `SkpParseError extends Error` |
| .NET | `SkpParseOptions.Progress: IProgress<SkpParseProgress>?` | `SkpParseOptions.OnLog: Action<SkpLogLevel, string>?` | `SkpParseException : Exception` |
| Dart | `ParseOptions.onProgress: void Function(ParseProgress)?` | `ParseOptions.onLog: void Function(SkpLogLevel, String)?` | `SkpParseException implements Exception` |

Python doesn't have a separate progress callback: the standard-library
`logging` module is the single channel, and progress is reported as
`DEBUG`-level log records (`"Processed %d/%d top-level records"`) rather
than a second parallel mechanism — that's the idiomatic choice for a
language whose ecosystem already standardizes on `logging` for exactly this
kind of "opt-in, leveled, handler-agnostic" reporting. The other three
languages don't have an equivalent stdlib-blessed logging façade, so they
use an explicit options object with two independent callbacks instead.

### Python

```python
import logging
from openskp import SkpFile, SkpParseError

# Silent by default. To see progress and stage messages:
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("openskp")
logger.setLevel(logging.DEBUG)

try:
    model = SkpFile.open("model.skp").parse()
except SkpParseError as e:
    print(f"parse failed: {e}")   # includes stage=... record=.../... etc.
    print(f"caused by: {e.__cause__}")
```

Set the level on `"openskp"` alone to get every sub-logger's output too
(Python's logger hierarchy propagates upward by default); set it on
`"openskp.legacy"` or `"openskp.scene"` individually to isolate one stage
of the pipeline. `logging.INFO` gets you start/complete summaries only;
`logging.DEBUG` adds the per-500-record progress lines and detail
(materials/styles counts, detected version, etc.).

### TypeScript

```typescript
import { SkpFile, SkpParseError, ParseOptions } from 'openskp';

const options: ParseOptions = {
  onProgress: (info) => console.log(`${info.stage}: ${info.current}/${info.total}`),
  onLog: (level, message) => console[level === 'debug' ? 'debug' : 'info'](message),
};

try {
  const model = SkpFile.open('model.skp').parse(options);
  const scene = SkpFile.open('model.skp').buildScene(options); // separate call, same options shape
} catch (e) {
  if (e instanceof SkpParseError) {
    console.error(e.message);      // includes stage=... etc.
    console.error('caused by', e.cause);
  }
}
```

`ParseOptions` is a plain object — no class to instantiate, no Node-only
APIs — so it works identically in the browser and in Node (this is the same
package the [web viewer](../examples/web-viewer/) runs in a browser tab).

### .NET

```csharp
using OpenSkp;

var options = new SkpParseOptions
{
    Progress = new Progress<SkpParseProgress>(p =>
        Console.WriteLine($"{p.Stage}: {p.Current}/{p.Total}")),
    OnLog = (level, message) => Console.WriteLine($"[{level}] {message}"),
};

try
{
    var model = SkpFile.Open("model.skp", options);
}
catch (SkpParseException e)
{
    Console.Error.WriteLine(e.Message);       // includes stage=... etc.
    Console.Error.WriteLine($"caused by: {e.InnerException}");
}
```

`IProgress<T>` is the BCL's own progress-reporting convention (the same
interface `HttpClient` and file-copy APIs use), so it composes with
whatever the rest of your .NET code already does with progress — including
marshalling onto a UI thread via `Progress<T>`'s captured
`SynchronizationContext`, if called from one.

### Dart

```dart
import 'package:openskp/openskp.dart';

final options = ParseOptions(
  onProgress: (info) => print('${info.stage}: ${info.current}/${info.total}'),
  onLog: (level, message) => print('[$level] $message'),
);

try {
  final model = SkpFile.open('model.skp').parse(options);
} on SkpParseException catch (e) {
  print(e.toString());   // includes stage=... etc.
  print('caused by: ${e.cause}');
}
```

## What "silent by default" actually guarantees

With no options passed (Python: no handler configured on the `"openskp"`
logger tree; TypeScript/.NET/Dart: no options object, or one with both
callbacks left `null`), OpenSKP:

- Never writes to stdout/stderr/console.
- Never calls `logging.basicConfig()` or installs a handler of its own
  (Python) — the standard-library convention: a library configures its
  *logger*, never the *logging system*.
- Never allocates the progress-info object more than once per interval (no
  per-record allocation cost when nobody's listening — the check that gates
  reporting happens before anything is constructed).

This is verified by a dedicated test in each language's suite (e.g.
Python's `TestObservability.test_silent_by_default`, using pytest's
`caplog` fixture to assert zero log records with no configuration).
