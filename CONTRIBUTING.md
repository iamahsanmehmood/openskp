# Contributing to OpenSKP

First off — **thank you** for considering contributing to OpenSKP! 🎉

This project exists because someone decided a proprietary file format shouldn't be locked behind a proprietary SDK. Every contribution — whether it's a bug fix, a new feature, better documentation, or a whole new platform implementation — helps make 3D data more accessible to everyone.

---

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Adding a New Platform](#adding-a-new-platform)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)

---

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). By participating, you agree to uphold this code. Please report unacceptable behavior to the project maintainers.

---

## Getting Started

### Prerequisites

| Platform | Requirements |
|:---------|:------------|
| Python | Python 3.10+, pip |
| TypeScript | Node.js 18+, npm or pnpm |
| Dart | Dart SDK 3.0+ |
| All | Git, a good hex editor (recommended) |

### Understanding the Codebase

Before diving in, we recommend reading:

1. **[Architecture](docs/ARCHITECTURE.md)** — Understand the three-layer design
2. **[Binary Format Spec](docs/BINARY_FORMAT.md)** — Learn how SKP files are structured
3. **[API Design](docs/API_DESIGN.md)** — Understand the cross-platform API contract
4. **[Research Methodology](research/METHODOLOGY.md)** — How we reverse-engineered the format

---

## Development Setup

### Python

```bash
# Clone the repository
git clone https://github.com/iamahsanmehmood/openskp.git
cd openskp

# Create and activate a virtual environment
cd python
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### TypeScript (Coming Soon)

```bash
cd typescript
npm install
```

### Dart (Planned)

```bash
cd dart
dart pub get
```

---

## Running Tests

### Python

```bash
cd python

# Run the full test suite
pytest

# Run with coverage report
pytest --cov=openskp --cov-report=html

# Run a specific test file
pytest tests/test_tlv_parser.py

# Run tests matching a keyword
pytest -k "test_vertex"
```

### Test Fixtures

Test SKP files are stored in `python/tests/fixtures/`. If you need to add new test files:

1. Keep them small (< 1 MB if possible)
2. Document what each test file contains
3. Never commit files you don't have the right to distribute

---

## Making Changes

### Branch Naming Convention

| Type | Format | Example |
|:-----|:-------|:--------|
| Feature | `feat/short-description` | `feat/uv-coordinate-parsing` |
| Bug fix | `fix/short-description` | `fix/face-normal-calculation` |
| Documentation | `docs/short-description` | `docs/add-export-examples` |
| Refactor | `refactor/short-description` | `refactor/tlv-parser-cleanup` |
| Platform | `platform/name` | `platform/typescript-init` |

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

<optional body>

<optional footer>
```

**Examples:**

```
feat(parser): add UV coordinate extraction from face tags

fix(export): correct Y-up coordinate conversion in GLB exporter

docs: update binary format spec with new tag discoveries

test(python): add regression test for nested component instances
```

**Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`

---

## Pull Request Process

1. **Fork** the repository and create your branch from `main`
2. **Write code** and add/update tests as needed
3. **Run the full test suite** and ensure all tests pass
4. **Update documentation** if your change affects the API or behavior
5. **Submit a PR** using the [pull request template](.github/PULL_REQUEST_TEMPLATE.md)
6. **Wait for review** — maintainers aim to review within 48 hours

### PR Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] New code has test coverage
- [ ] Documentation is updated (if applicable)
- [ ] Commit messages follow conventional commits
- [ ] No unrelated changes are included
- [ ] PR description clearly explains the change

### Review Process

- At least **one maintainer approval** is required
- CI must pass (linting, tests, type checking)
- Discussions should be resolved before merge
- Squash merge is preferred for single-feature PRs

---

## Coding Standards

### Python

| Rule | Standard |
|:-----|:---------|
| Formatter | `black` (line length 88) |
| Linter | `ruff` |
| Type checking | `mypy` (strict mode) |
| Docstrings | Google style |
| Imports | `isort` (profile: black) |
| Minimum version | Python 3.10 |

```python
# ✅ Good
def parse_vertex(data: bytes, offset: int = 0) -> tuple[float, float, float]:
    """Parse a 3D vertex from raw bytes.

    Args:
        data: Raw byte buffer containing vertex data.
        offset: Byte offset to start reading from.

    Returns:
        Tuple of (x, y, z) coordinates in inches.

    Raises:
        ParseError: If the buffer is too short for a complete vertex.
    """
    ...
```

### TypeScript

| Rule | Standard |
|:-----|:---------|
| Formatter | `prettier` |
| Linter | `eslint` |
| Type checking | `strict: true` in tsconfig |
| Module system | ESM |
| Minimum version | Node.js 18 |

### General Principles

- **Explicit over implicit** — Name things clearly, avoid magic numbers
- **Parse, don't validate** — Convert raw bytes into typed structures as early as possible
- **Fail loudly** — Throw descriptive errors with byte offsets when parsing fails
- **Zero external dependencies** for the core parser — stdlib only
- **Cross-platform parity** — If you add a feature in one language, document the expected behavior so other platforms can follow

---

## Adding a New Platform

Want to bring OpenSKP to a new language? Fantastic! Here's the process:

### 1. Propose It First

Open an issue with the `platform` label describing:
- The target language and ecosystem
- Your experience with that language
- Estimated timeline

### 2. Follow the Architecture

All platform implementations **must** follow the [three-layer architecture](docs/ARCHITECTURE.md):

```
Layer 1: Binary Parser     → Read raw TLV from model.dat
Layer 2: Structured Model  → Build typed model objects
Layer 3: Export Engines     → GLB, OBJ, JSON export
```

### 3. Implement the API Contract

Follow the [API Design](docs/API_DESIGN.md) specification. The public API should feel natural in the target language while maintaining behavioral equivalence.

### 4. Port the Test Suite

Every platform must pass the same behavioral tests against the same fixture files.

### 5. Directory Structure

```
<language>/
├── README.md          # Platform-specific README
├── src/               # Source code
├── tests/             # Test suite
└── <package-config>   # pyproject.toml / package.json / pubspec.yaml
```

---

## Reporting Bugs

Use the [Bug Report](https://github.com/iamahsanmehmood/openskp/issues/new?template=bug_report.md) issue template. Please include:

- OpenSKP version and platform (Python/TypeScript/Dart)
- Operating system
- SketchUp version that created the `.skp` file
- Minimal reproduction steps
- Expected vs. actual behavior
- Error messages and stack traces

> [!NOTE]
> If you can share the `.skp` file that causes the issue (or a minimal reproduction), it dramatically speeds up debugging.

---

## Suggesting Features

Use the [Feature Request](https://github.com/iamahsanmehmood/openskp/issues/new?template=feature_request.md) issue template. Describe:

- The problem you're trying to solve
- Your proposed solution
- Alternative approaches you've considered

---

## 💙 Thank You

Every contribution, no matter how small, makes OpenSKP better. Whether you're fixing a typo in the docs or implementing an entirely new export format — you're helping make 3D data more accessible to the world.

Welcome aboard! 🚀
