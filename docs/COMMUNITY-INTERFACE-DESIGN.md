# TownRecord community interface design

Status: implementation specification, November 13, 2026. Owner: Scott.
This specifies the already authorized two-body community-record product. It does
not add modules, providers, accounts, a desktop shell or a publishing service.

## Decision and rationale

The interface is a browser application written in strict TypeScript, served with
its HTML/CSS by the existing Python service. There is no Tauri dependency or planned
Tauri step in this delivery. Python owns acquisition, storage, authorization,
publication, transcription and scheduling. The browser calls the same API as
other clients; it does not reimplement domain logic.

The first community UI was implemented as handwritten JavaScript with semantic
HTML, but without this dedicated interaction design. That was an implementation
shortcut, not an approved change to Scott's TypeScript/Python stack. This document
establishes the bounded behavior before conversion. Compilation produces the JS
asset that browsers execute; operators do not need Node or a TypeScript compiler.
No framework rewrite is necessary to type the existing DOM interface.

## Users, authority and information boundaries

- Resident/reporter: anonymous access to published snapshots only; no unpublished
  records, operator history, tokens or private source credentials.
- Operator: existing named write token; review sources/jobs, correct associations,
  publish/withdraw, and export reviewed evidence. No new authentication system.
- Administrator: documented Windows installation, token issuance and start script;
  CLI backup/restore. The UI must not imply it installs a Windows service.
- Publication is an operator decision, separate from successful acquisition.
  Machine transcription never becomes official minutes through publication.
- TownReporter retains its existing owner/editor authority. Transfer creates an
  unpublished lead; no automatic model call, article creation or publication.

## Navigation and page structure

Two routes: `/` (resident) and `/operator` (operator). A public deep link
`/?meeting=ID` opens a reviewed meeting. Header contains product identity and
Public meetings / Operator workspace links. Main content has one page heading,
a skip link, a polite status region, and a footer explaining source provenance.
Do not add an unimplemented navigation item or placeholder action.

Resident list: title/introduction, topic/body/date filters, results with title,
body, scheduled date and cancellation status, then Open meeting. Dates refer to
the community's local meeting schedule. Blank filters mean no restriction.
No matches explains how to broaden the query. A network error is an error, not
an empty result. Published-only bodies populate the public filter.

Resident detail, in order:
1. Back to meetings; title, body/date, cancellation status and publication note.
2. Documents: retained file download, original source link, expandable extracted
   text with page numbers. Name/extension must match the actual content type.
3. Recordings: original recording links and availability/processing state.
4. Agenda: available items and source-backed timestamp links; existing vote
   evidence keeps its source label. Do not infer decisions from an AI summary.
5. Transcript: explicit origin/provisional label, timestamp links and text.
   Missing transcript states the known reason. Missing documents/recording are
   explained separately. Full transcript text is retained, not silently truncated.

Operator sign-in: named token input, submit and clear error. Token lives only in
this tab's memory. Input clears after submission; failed authentication leaves
no unlocked workspace. Sign-out clears private DOM data and form state.

Operator workspace contains:
- Coverage setup: community/body form; validated source URL/provider, exact allowed
  document origins and explicit manual coverage dates; current sources and status.
  Saving the same source updates its configuration through the existing API.
- Processing: installed tool versions and Set up/check actions; daily retrieval
  time/zone or an explicit pause reason; Refresh status; job state, errors, retry
  and expandable coverage results. A queued job is never displayed as completion.
- Meeting list/detail uses the same core presentation as residents, with private
  acquisitions visible only after authorization.
- Review/correction controls follow meeting content, not the public navigation.

## Operator actions and transitions

| Action | Input / validation | Successful result | Failure behavior |
|---|---|---|---|
| Add public body | Community and body names, required | Body appears in workspace and correction choices | Explain validation/server error; retain form input |
| Validate/save source | Existing community, HTTP(S) source, provider, dates in order, exact HTTPS origins | Validated configuration and coverage shown | Invalid configuration does not leave a partial source |
| Retrieve coverage | Configured source | Queued job visible; refresh shows eventual result | Distinguish source error, unavailable attachment, outside coverage and zero results |
| Tool setup/check | Supported tool only; known video required for yt-dlp check | Existing managed runtime job; duplicate request reuses active job | Show job cause and supported retry; no fake ready state |
| Local transcript | Selected recording and explicit operator reason | Heavy worker job; saved local origin/provenance after completion | Failed/paused state visible, retry after cause addressed |
| Correct meeting | Title/date/body/cancelled/continued plus reason | Private record and history updated | Current public snapshot remains unchanged |
| Move association | Existing document/recording, destination meeting, reason | Association/history updated; moved recording becomes primary | Invalid destination does not partially mutate data |
| Publish | Reviewed meeting and publication reason | New public snapshot/revision | Failed request does not claim publication |
| Withdraw | Reason | Public detail/download/search access removed; history retained | Error is visible; downloaded external copies are not recalled |
| Export evidence | Published reviewed revision | JSON snapshot with instance/meeting/revision identity and citations | Clear error if not exportable; never export private mutable data as reviewed |
| Retry job | Failed/paused job | New job linked to previous attempt; duplicate request does not enqueue duplicates | Provider writes remain subject to their existing scoped workflow |

Each asynchronous action presents working/error status. Prevent repeated activation
of the same in-flight submit without disabling unrelated navigation. Success is
shown only from a successful response; jobs require their own terminal readback.
Publication/correction history shows actor/reason/time supplied by the API.
No extra approval dialog is required beyond the existing deliberate controls and
required reasons. Fix broken behavior; do not add ceremonial review stages.

## API and TypeScript contracts

Keep endpoint paths and response schemas unchanged. Define explicit interfaces
for bodies, sources/coverage, jobs, overview, meeting summaries/details, documents,
recordings, agenda items, transcript segments/provenance, history and publication.
Represent nullable/missing values honestly. Use a typed JSON request helper and
narrow unknown error payloads before display. Use typed DOM references for forms,
inputs and selects; avoid `any`, unchecked blanket casts and non-null assertions
that conceal absent controls. JSON schema validation remains server authority.

Public requests use `/public/v1/bodies` and `/public/v1/meetings`; operator requests
use `/v1/operator/overview`, bodies, sources, meetings, jobs/retry, tools/setup and
videos/transcribe. Downloads send a bearer token only on private requests.
Source links accept HTTP(S) only; render acquired text with textContent, never
HTML interpolation. API and HTML/CSS assets remain same-origin and offline-capable.

TownReporter import remains its existing TypeScript component/server function.
The 8 MiB file/UTF-8 bound and exact JSON-text storage must agree across browser
and server. Duplicate identity preserves editorial changes; changed evidence under
the same revision is refused; a newly reviewed revision creates separate evidence.
Show validation/authorization/oversize failures and allow corrected retry.

## Visual and accessibility behavior

Keep the current restrained navy/teal/gold palette and readable system font.
Content width is at most1120px, with white bordered record cards, clearly underlined
links and visible focus. Controls have at least44px minimum height. At650px and
below, header/navigation/forms/actions stack; fields fit the viewport. Preserve
normal browser zoom and avoid horizontal scrolling for ordinary content.
Use semantic headings, labelled controls, native buttons/links/selects and details.
Status/errors use the existing live region. On opening a meeting move focus to
its heading; on return restore useful list focus. All workflows must be operable
by keyboard. Do not claim formal accessibility certification from these checks.

## Build and delivery

Maintain TypeScript source separately from the compiled packaged asset. Pin the
compiler with an npm lockfile and strict tsconfig. A reproducible build writes
`core/src/townrecord/web/community.js`. Existing Python wheel packaging includes
the compiled asset. CI typechecks/builds and detects stale generated output before
running its existing Python checks. Node is a development/CI dependency only.
No hosted JS, runtime CDN, new framework or Rust shell is introduced.

## Bounded implementation and acceptance

1. Convert the current interface to strict TypeScript while preserving API behavior.
2. Close only the named UI gaps: keyboard/focus, correct file download, association,
   cancellation/postponement, body/date filters and visible error/retry handling.
3. On the installed candidate, run one full local-transcription workflow and the
   complete TownReporter browser transfer, including duplicate/error recovery.
4. Reconcile the existing representative-case tests; fill missing cases without
   rerunning unaffected proof. Verify documented Windows service/scheduler/restart/
   backup/restore/continued acquisition in an isolated profile.
5. Record exact package/commit identities, observed outcomes and any real limits;
   push/merge after applicable existing checks. Hold tagging/publication for Scott.

Reuse previous evidence for unchanged backend/auth/connector behavior. The changed
UI/build needs its own installed checks. A rebuild is justified by changed packaged
assets, not by documentation alone. No extra OS work, all-provider acceptance,
framework migration or arbitrary meeting-count quota is added.

Scott decides required human work. We may suggest an independent operator/resident
trial after the engineering is done; it is not an agent-imposed gate. A genuinely
new provider publication depends on the publisher, not on a human tester, and must
be distinguished from historical replay. Do not silently call either completed.

## Implemented architecture and user guidance

The [technical manual](TECHNICAL-MANUAL.md) diagrams the implemented architecture,
publication handoff and recovery. The [user manual](USER-MANUAL.md) covers operator
and resident workflows. The separate [website design](SITE.md) extends this
interface palette; it does not change the application or its accepted package.

## Windows desktop runtime contract (2026-11-13)

The Windows Start menu opens the installed browser interface through bundled
`pythonw -m townrecord.windows open`; `start` serves without opening the browser,
and `stop` authenticates to that profile and waits for its service lock to clear.
Only one desktop launcher owns a profile. The existing service, workers,
scheduler, SQLite migrations and serving record remain authoritative.

Private configuration, database, runtimes and storage live under the current
user's LocalAppData/TownRecord, outside installed application files. Updates and
uninstall preserve these files. First run asks for the local IANA timezone, scan
time and known test recording in the browser before managed tool setup. Settings
are validated and persisted; an explicit restart applies them to every worker.

An operator credential is protected by Windows current-user DPAPI and never
printed or put in a URL. Each authenticated desktop launch mints a random,
one-use code expiring after 120 seconds, delivered only in the URL fragment.
The browser clears that fragment before exchanging it. Exchange requires the
exact loopback origin/Host; random websites cannot obtain a credential merely
because the service listens on localhost. Existing developer bearer-token routes
and reviewed public records retain their authorization rules.

Settings expose real data and backup paths, graceful stop/restart, backup and
restore. Backup stops workers and releases the existing service claim before
using the existing verified recovery format; the desktop restarts afterwards.
Restore verifies hashes/database and creates a separate profile, preserving the
current data and backup, then restarts using that profile. Errors are retained
in the desktop log and operation receipt; failures never become success claims.
The bundled tools directory and interpreter precede system PATH, and existing
allow-listed, hidden subprocess execution is reused for managed tools.
