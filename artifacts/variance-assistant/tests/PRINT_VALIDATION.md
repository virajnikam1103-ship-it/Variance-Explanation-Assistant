# Saved report print validation

Run the browser PDF regression check with:

```sh
pnpm --filter @workspace/variance-assistant run test:print
```

The test creates a saved-report fixture with INR KPI values, driver evidence,
confidence states, and a deliberately long ledger note. It then generates and
inspects A4 PDFs through both Chromium and Firefox.

The check does not open the browser's native print dialog; it uses each
browser engine's WebDriver PDF capability instead. Safari/WebKit is not
available in this workspace, so that engine still requires a manual
verification when it becomes a supported delivery browser.