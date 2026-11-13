# TownRecord

TownRecord is a free, open-source local service and browser interface for community
meeting records. Operators acquire documents and transcripts, review and publish
meeting snapshots, and correct or withdraw them. Residents can browse and search
published records, read documents and transcripts, and follow original sources.

## Status

Pre-alpha. The Python core implements a local SQLite-backed service, source and
job management, record capture and an authenticated local API. The wider product described in the specification is reference/backlog, not the
completion contract for this bounded service and browser product.
The current Windows candidate has been exercised with Longmont Council and
Planning records. It is not a published release. A question-answering assistant is not part of this bounded delivery. See the
[candidate acceptance record](docs/COMMUNITY-ACCEPTANCE.md) for observations,
the exact package identity and observed limits. Scott determines which human
trials are required; they are not additional engineering release gates.

- [QuorumRelay connector configuration and limits](docs/CONNECTORS.md)
- [Product specification](docs/SPEC.md)
- [Decisions](docs/DECISIONS.md)

## Why it runs on your computer

YouTube blocks data-center access to meeting captions.
A home connection works, so TownRecord runs on your own computer.

## Stack

- A Python core.
- A TypeScript browser interface served by the Python core.
- SQLite for the database.
- Windows first. macOS later.

## Relation to TownReporter

TownReporter is a separate product.
TownRecord offers a public local API.
TownReporter, or anyone else, can use it.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Community record workflow

The [community workflow](docs/COMMUNITY-WORKFLOW.md) adds operator review and
explicit publication to the existing acquisition service. Resident pages expose
reviewed snapshots; private acquisition data still requires authorization.
See [implementation state](docs/COMMUNITY-RECORD-STATE.md) for passed evidence
and remaining acceptance work. This branch is not a published release.

Windows operator installation, scheduling and verified backup/restore instructions
are in [Windows community operations](docs/WINDOWS-COMMUNITY-OPERATIONS.md).
The [transcription comparison](docs/TRANSCRIPTION-COMPARISON.md) records the
Whistle/Whisper results and why this candidate explicitly selects Whisper small.

Interface design and stack: [Community interface design](docs/COMMUNITY-INTERFACE-DESIGN.md).
Developers run `npm ci` and `npm run build:ui` before building the Python wheel.
Operators use the packaged browser asset and do not need Node.
