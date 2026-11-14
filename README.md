# TownRecord

**Keep your community's public meeting record within reach.**

TownRecord collects meeting documents and available transcripts, lets an operator
review and publish a snapshot, and gives residents a searchable record with links
back to the source. It is free, open-source community infrastructure in the
[Townlight](https://github.com/townlight) family.

## Start here

- [User manual — operators and residents](docs/USER-MANUAL.md)
- [Windows installation, daily operation and recovery](docs/WINDOWS-COMMUNITY-OPERATIONS.md)
- [Technical manual — architecture, Mermaid diagrams and HTTP API](docs/TECHNICAL-MANUAL.md)
- [Supported connector capabilities and limits](docs/CONNECTORS.md)
- [Exact candidate acceptance and evidence](docs/COMMUNITY-ACCEPTANCE.md)

## Current delivery

The bounded Windows community-record candidate passed acquisition from two public
bodies, operator/resident workflows, full local transcription, TownReporter browser
handoff, scheduling and backup/restore. These observations do not establish all
providers, certified transcript accuracy or the broader specification's backlog.
The [completion handoff](docs/COMMUNITY-COMPLETION-HANDOFF.md) records exact identities.
**Tagging, release publication and website deployment await Scott's approval.**
There is no published MSI or hosted TownRecord service claimed here.

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
GitHub Pages deployment. The site is prepared, not yet publicly deployed.
See the technical manual for Python setup, tests, package builds and API access.

The [broader specification](docs/SPEC.md) remains a reference and backlog;
[decisions](docs/DECISIONS.md) preserve architecture context. Report reproducible
problems or propose bounded improvements in [Issues](https://github.com/scottconverse/townrecord/issues).

## License

[Apache-2.0](LICENSE).
