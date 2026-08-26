---
name: Native print verification
description: Testing limitation for browser-native print-to-PDF flows.
---

Browser automation in this workspace can confirm that a report's print action is wired, that the page remains stable, and that the report content is present before and after the action. It cannot observe or complete the operating system/browser native print dialog.

**Why:** The print dialog is outside the page context, so an automated click may remain pending even when `window.print()` was called successfully.

**How to apply:** Treat native print-to-PDF verification as a manual browser check; use automated tests for the trigger, URL stability, content preservation, and absence of server errors.