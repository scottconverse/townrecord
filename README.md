# TownRecord

TownRecord is a free, open-source desktop app for a citizen or a reporter.
It watches the city, county and state governments for one area.
It captures meeting video transcripts and public records on your own computer.
It links them together.
It answers your questions with citations to the exact record page or video timestamp.

## Status

Pre-alpha. The Python core implements a local SQLite-backed service, source and
job management, record capture and an authenticated local API. The desktop
shell and the full product described in the specification remain in progress.
The current connector integration is a development candidate; provider and
installed-workflow acceptance are not established by its source code alone.

- [QuorumRelay connector configuration and limits](docs/CONNECTORS.md)
- [Product specification](docs/SPEC.md)
- [Decisions](docs/DECISIONS.md)

## Why it runs on your computer

YouTube blocks data-center access to meeting captions.
A home connection works, so TownRecord runs on your own computer.

## Stack

- A Python core.
- A planned thin Tauri desktop shell.
- SQLite for the database.
- Windows first. macOS later.

## Relation to TownReporter

TownReporter is a separate product.
TownRecord offers a public local API.
TownReporter, or anyone else, can use it.

## License

Apache-2.0. See [LICENSE](LICENSE).
