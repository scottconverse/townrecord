# TownRecord

TownRecord is a free, open-source desktop app for a citizen or a reporter.
It watches the city, county and state governments for one area.
It captures meeting video transcripts and public records on your own computer.
It links them together.
It answers your questions with citations to the exact record page or video timestamp.

## Status

Pre-alpha. Specification stage. No working code yet.

- [Product specification](docs/SPEC.md)
- [Decisions](docs/DECISIONS.md)

## Why it runs on your computer

YouTube blocks data-center access to meeting captions.
A home connection works, so TownRecord runs on your own computer.

## Planned stack

- A Python core.
- A thin Tauri desktop shell.
- SQLite for the database.
- Windows first. macOS later.

## Relation to TownReporter

TownReporter is a separate product.
TownRecord offers a public local API.
TownReporter, or anyone else, can use it.

## License

Apache-2.0. See [LICENSE](LICENSE).
