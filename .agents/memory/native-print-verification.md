---
name: Native print verification
description: Testing limitation for browser-native print-to-PDF flows.
---

Browser automation in this workspace can confirm that a report's print action is wired, that the page remains stable, and that the report content is present before and after the action. It cannot observe or complete the operating system/browser native print dialog.

**Why:** The print dialog is outside the page context, so an automated click may remain pending even when `window.print()` was called successfully.

**How to apply:** Treat native print-to-PDF verification as a manual browser check; use automated tests for the trigger, URL stability, content preservation, and absence of server errors.

For reproducible rendered-PDF checks, WebDriver can generate PDFs directly through
the Chromium and Firefox engines without opening that native dialog. Inspect the
resulting document's A4 page bounds and retained ledger text rather than relying
only on CSS-source assertions.

**Why:** Print-media emulation and static style checks cannot catch engine-specific
pagination or wrapping faults. Firefox PDF text extraction can omit a visibly
rendered ledger category label, so validate full row notes and amounts as well.

**How to apply:** Use engine-generated PDFs to test print layout when Chromium or
Firefox are installed. Keep Safari/WebKit as an explicit manual verification unless
that engine is available in the workspace.