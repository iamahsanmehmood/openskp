---
name: Bug Report
about: Report a parsing or export issue
title: "[BUG] "
labels: bug
---

## Description
A clear description of the bug.

## SKP File Details
- SketchUp version used to create the file:
- Approximate file size:
- Number of components (if known):

## Steps to Reproduce
```python
from openskp import SkpFile
skp = SkpFile.open("model.skp")
model = skp.parse()
# What you did...
```

## Expected Behavior
What you expected to happen.

## Actual Behavior
What actually happened. Include error messages if any.

## Environment
- OS: [e.g., Windows 11, Ubuntu 22.04]
- Python version: [e.g., 3.12]
- openskp version: [e.g., 0.1.0]
