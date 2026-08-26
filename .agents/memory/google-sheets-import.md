---
name: Google Sheets import boundary
description: Privacy and access rule for importing Google Sheets into variance analysis.
---

Google Sheets imports accept shareable or published Google Sheets URLs and download their CSV export temporarily for the existing mapping workflow. Do not request or store Google credentials to import a sheet.

**Why:** Public-link import keeps the analyst in control of sharing, avoids collecting credentials, and works without a third-party account connection.

**How to apply:** If private Google Sheets access is requested, use a Replit-managed Google Sheets OAuth connection and preserve the same mapping/analysis contract. Continue to delete imported temporary source files after successful processing.