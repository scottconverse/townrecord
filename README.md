# TownRecord

**Keep your community's public meeting record within reach.**

TownRecord collects meeting documents and available transcripts, lets an operator
review and publish a snapshot, and gives residents a searchable record with links
back to the source. It is free, open-source community infrastructure in the
[Townlight](https://github.com/townlight) family.

[Website](https://scottconverse.github.io/townrecord/) · [Windows installation](docs/WINDOWS-COMMUNITY-OPERATIONS.md)

## Start here

- [Download TownRecord 0.2.0 for Windows](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TownRecord-0.2.0-windows-x64-setup.exe) · [Release notes](https://github.com/scottconverse/townrecord/releases/tag/v0.2.0)
- [User manual — operators and residents](docs/USER-MANUAL.md)
- [Windows installation, daily operation and recovery](docs/WINDOWS-COMMUNITY-OPERATIONS.md)
- [Technical manual — architecture, Mermaid diagrams and HTTP API](docs/TECHNICAL-MANUAL.md)
- [Supported connector capabilities and limits](docs/CONNECTORS.md)
- [Exact acceptance and evidence](docs/COMMUNITY-ACCEPTANCE.md)

## Windows application and delivery

The 0.2.0 Windows installer bundles Python, FFmpeg and a pinned, signed
TextFlowKit 0.1.19 runtime without model weights. Install it for
your account, open **TownRecord** from Start Menu and complete the browser
checklist: timezone/daily time/sample recording, community/body, source/coverage
and managed tools. Sign-in is automatic. Settings, status/retry, verified backup,
restore, restart and stop use the workspace. Closing the browser leaves retrieval
running. Public hosting remains separate.

TextFlowKit setup verifies and copies the bundled runtime into managed storage;
its initial bootstrap can work offline. Model downloads, yt-dlp setup/checks and
meeting-source access still need internet access. Existing online update checks
retain a working runtime if a newer version fails installation or its check.

See [Windows installation and recovery](docs/WINDOWS-COMMUNITY-OPERATIONS.md) and
[the delivery handoff](docs/COMMUNITY-COMPLETION-HANDOFF.md) for exact build,
signature, installed acceptance and delivery status. Clean installation, first-run,
real acquisition, managed transcription and review/publication passed on the signed
candidate. Remaining lifecycle checks and public delivery remain pending. The download
links above are prepared for publication; availability is not yet claimed. Verify
the **Scott Converse** signature before running an installer.

The earlier [0.1.0 preview](https://github.com/scottconverse/townrecord/releases/tag/v0.1.0)
remains a published Python wheel with historical two-body acquisition,
review/publication, transcription, TownReporter handoff, schedule and recovery
evidence. That evidence does not establish the new installer or every provider.

## How it works

1. Collect documents and available text from configured, supported meeting sources.
2. Check metadata, associations, source links and gaps before publishing.
3. Let residents browse, filter, search and follow recording timestamps.
4. Review and republish corrections; withdraw public access when needed.
5. Export published evidence JSON to TownReporter as an unpublished lead.

Private acquisition and public snapshots are separate. Machine transcripts remain
provisional. Source access and captions depend on the publisher and network;
a home connection is not a guarantee of access. Local transcription uses Whisper
small through TextFlowKit; see the [comparison](docs/TRANSCRIPTION-COMPARISON.md).
TownReporter is a separate product, not a bundled service or automatic publisher.

## Development and website

Python service, strict TypeScript browser interface, SQLite and local artifacts.
No Tauri runtime. Operators do not need Node; contributors use Node to compile UI.

```powershell
npm ci
npm run build:ui
npm run build:site
npm run preview:site
```

Preview the built landing page at `http://127.0.0.1:18320/townrecord/`.
The [website guide](docs/SITE.md) describes its design, source files and manual-only
GitHub Pages deployment. The public site is at https://scottconverse.github.io/townrecord/.
See the technical manual for Python setup, tests, package builds and API access.

The [broader specification](docs/SPEC.md) remains a reference and backlog;
[decisions](docs/DECISIONS.md) preserve architecture context. Report reproducible
problems or propose bounded improvements in [Issues](https://github.com/scottconverse/townrecord/issues).

## License

[Apache-2.0](LICENSE).
