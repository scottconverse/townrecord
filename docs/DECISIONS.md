# TownRecord decisions

Date: September 27, 2026

Decisions 1 to 9 are the nine decisions in Section 20.1 of the [specification](SPEC.md).
Each of those choices is copied from the specification.
Each of those reasons comes from the section of the specification that is named.
Decision 10 was made later on September 27, 2026.

## 1. Name and repository

**Choice:** TownRecord. Repository at github.com/scottconverse/townrecord for now; it moves to a townrecord organization later.

**Reason:** The specification gives no separate reason. Its header notes that the townrecord organization name was free on September 27, 2026.

## 2. Core language

**Choice:** Python core with a thin Tauri shell (Section 14.2).

**Reason:** yt-dlp, TextFlowKit, LiteLLM and most PDF and OCR tools are Python, so they run inside the core. The shell only draws the interface, and starts and stops the core (Section 14.2).

## 3. Database

**Choice:** SQLite (Section 14.3).

**Reason:** SQLite has FTS5 for full-text search and a vector extension for embeddings. TownReporter's embedded database fallback (PGLite) loses data when the process stops. Its runtime table definitions must match its migrations, which is a recurring cost (Section 14.3).

## 4. TownReporter

**Choice:** TownRecord does not touch TownReporter. TownRecord offers a public API that TownReporter or anyone else can use. Integration comes later (Section 13.5).

**Reason:** TownReporter is a standalone product and keeps its own meeting capture. Integration of the two products is a later decision (Section 13.5).

## 5. YouTube capture

**Choice:** Captions first as specified, plus a "Local transcription only" setting. No lawyer review; an AI legal-risk review with a "not legal advice" warning (Sections 8.10, 15.1).

**Reason:** Captions are small. TownReporter measured about 136 KB of captions for a two-hour meeting, against about 120 MB of audio (Section 8.3). YouTube's terms of service restrict automated access, and there is no lawyer review in the project plan. The user can run the AI legal-risk review before publishing (Section 8.10).

## 6. Public-site transcripts

**Choice:** Excerpts by default; full transcripts can be turned on for each body (Section 15).

**Reason:** The specification gives no separate reason. Section 15.1 lists republished captions as one of the risks that the legal-risk review checks.

## 7. State scope

**Choice:** The legislature by default, plus a checklist of state boards, all off by default (Section 6.1).

**Reason:** The default area keeps the first capture small. A board whose adapter is not built yet shows as "not yet supported" (Section 6.1).

## 8. License

**Choice:** Apache-2.0. TownReporter (MIT) can move to the same license later if needed.

**Reason:** The specification gives no separate reason.

## 9. macOS

**Choice:** Windows first; macOS tested from the first build; signed macOS installer at M6 (Section 14.1).

**Reason:** The code is built and tested on macOS from the first build, in continuous integration, so that nothing Windows-only gets into it (Section 14.1).

## 10. Local API framework

**Date:** September 27, 2026

**Choice:** The local API of Section 13 uses FastAPI. Two rules go with it:

1. Long work (yt-dlp captures, TextFlowKit transcription, big AI calls) runs in the jobs system of Section 16.1, not inside an API request. A request starts a job and returns at once.
2. The API binds to 127.0.0.1 by default. It requires the bearer token of Section 13.1 on every request, including the docs page.

**Reasons:**

- The core is Python (decision 2). FastAPI is a Python web framework, so the API runs inside the core.
- FastAPI makes an OpenAPI description of the API from the code. Milestone M5 needs published API documentation (Section 19).
- Rule 1: the jobs system has claim tokens, heartbeats and checkpoints, so a long job can resume (Section 16.1). A request that waited on a capture or a transcription would have none of these.
- Rule 2: the local API listens on the loopback address by default and needs a token (Sections 13.1 and 17). The docs page is part of the API, so the same rule covers it.
