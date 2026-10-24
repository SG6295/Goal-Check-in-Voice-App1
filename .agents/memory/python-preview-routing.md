---
name: Python preview routing
description: Why a healthy standalone Python listener can still show a starting screen.
---
Use the application's routed preview URL without a port suffix for the Python app.

**Why:** A standalone Uvicorn listener was healthy on 0.0.0.0:8000, but the external :8000 preview returned 502. Explicit port mappings did not resolve it; registering the Python service with the application router did.

**How to apply:** Preserve managed service routing when changing run commands. Artifact commands run from their artifact directory, not the repository root. Use exec after changing directories, and check for an orphaned listener when replacing a standalone workflow.

Development and production service declarations must both be maintained.

**Why:** A working development-only Python service was omitted from publishing, which launched only the Node API and left the root app unavailable.

**How to apply:** Check each service's production run command and health route when diagnosing a preview/production mismatch; a successful publish alone does not prove the intended service launched.

Rely on the publishing dependency-install phase for Python requirements rather than adding a second bare pip install to artifact builds.

**Why:** The artifact build environment's system Python is externally managed; bare pip fails with PEP 668 even after the earlier dependency phase successfully installed into .pythonlibs.

**How to apply:** Keep dependencies in requirements.txt and use an import check to validate availability during the artifact build.
