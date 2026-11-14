# Windows first-run interface design

Scope: Windows 0.2.0 installer candidate. This design extends the existing browser
interface and Python service; it adds no Tauri runtime or provider integration.
Publication, signing and installed acceptance require separate candidate receipts.

## Operator journey and contract

1. Start Menu TownRecord starts or reuses the desktop service and opens the operator
   page with a short-lived, one-use `#launch` code. JavaScript removes the fragment
   before its first request and exchanges it for an in-memory write token through
   `POST /v1/desktop/exchange`. The token is never written to browser storage.
2. The desktop workspace shows a numbered setup checklist: save timezone, daily
   retrieval time and an accessible sample video; add community and public body;
   validate/save a source with explicit coverage; set up managed tools and inspect
   their jobs. Existing body/source/tool endpoints remain authoritative.
3. Settings save uses `PUT /v1/desktop/settings`, followed by the explicit restart
   endpoint. An announced restarting state explains the wait. Poll authenticated
   settings until applied, then refresh processing status. Errors remain visible
   with a retry path. Tool jobs show queued/running/failed/completed states and
   existing failed/paused Retry actions; Refresh status checks progress.
4. Desktop controls show the active data folder and backup location. Back up
   creates a verified timestamped backup and restarts. Restore accepts a backup
   folder path in a labeled GUI form, validates into a separate profile and asks
   the operator to reopen TownRecord from Start Menu after switching. Stop explains
   that Start Menu opens the application again. Neither path requires a shell.
5. A reload or expired launch code asks the desktop user to reopen Start Menu.
   Advanced nonpackaged CLI mode retains explicit manual token sign-in. Residents
   and existing publication/review/association/export flows remain unchanged.

## States and accessibility

All new desktop controls start hidden. Settings/setup appear only after successful
exchange and authenticated settings. Status and errors use the existing live
region; restart/backup/restore progress also has persistent text in the desktop
panel. Buttons disable during their own request; restart polling is bounded.
Labels identify timezone as an IANA name and sample video as a public recording.
Native required/time/url validation complements server validation. Existing
responsive controls, visible focus and semantic headings remain in use.

## Acceptance to verify

Compile strict TypeScript and regenerate the committed asset. Check immediate
fragment removal, one-use exchange, absence of persisted credentials, CLI token
fallback, saved schedule/sample recording, restart recovery, tool setup/retry,
community/source forms, backup path and restore/stop messages. Root owns packaged
Windows installation, signature, installed behavior and delivery evidence.
