---
name: Webhook transaction boundaries
description: Preserve retry safety when extending call event processing.
---
Keep insertion of the deduplication record and all event processing in one database transaction.

**Why:** Committing the event before processing would cause a failed attempt to look like an already-handled duplicate on the provider's retry, losing the update permanently.

**How to apply:** Processing errors must roll back the event insert as well as call, milestone and opt-out updates. Preserve terminal call state if a webhook arrives before the outbound API response.
