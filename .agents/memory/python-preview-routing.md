---
name: Python preview routing
description: Why a healthy standalone Python listener can still show a starting screen.
---
Use the application's routed preview URL without a port suffix for the Python app.

**Why:** A standalone Uvicorn listener was healthy on 0.0.0.0:8000, but the external :8000 preview returned 502. Explicit port mappings did not resolve it; registering the Python service with the application router did.

**How to apply:** Preserve managed service routing when changing run commands. Artifact commands run from their artifact directory, not the repository root. Use exec after changing directories, and check for an orphaned listener when replacing a standalone workflow.
