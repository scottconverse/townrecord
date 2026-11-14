# TownRecord website: design, operation and publication

## Positioning and design decisions

TownRecord is a local community meeting-record service for the person maintaining
a community's public record and the residents who need to find it. The practical
promise is to collect source material, review it, then publish a searchable
snapshot with original sources. It does not create certified minutes or answer
questions with AI. Townlight is the umbrella; TownReporter is a separate consumer.

The primary action is **Download for Windows**, followed by the user manual and
Windows setup guide. The 0.2.0 Windows installer is the normal-user path; 0.1.0
remains a historical wheel. The installer bundles Python, FFmpeg and a pinned, signed
TextFlowKit 0.1.19 runtime without model weights. TextFlowKit setup verifies and
copies the local runtime and can bootstrap offline. Model downloads, yt-dlp
setup/checks and source access still need internet access. Online update checks
retain a working runtime if a newer version fails installation or its check.
Signing and installed acceptance are separate receipts. Engineering links lead
to the technical manual, source and exact acceptance record.

The static site extends the application's existing palette: ink `#153344`, paper
`#f5f7f8`, accent `#165c70`, line `#c5d0d5`, and gold `#b58b2c` for decoration only.
System sans-serif keeps documentation and application visually related. Headings
use a 2–4rem responsive scale; body text is at least 1rem with 1.6 line spacing.
Content is capped at 72rem, prose at 65 characters, with 1/2/3/5rem spacing.
The visual language is a civic record: large title, numbered workflow, source
caption, and a clear publication boundary. No invented logo, testimonials or
customer claims. Existing acceptance screenshots show the actual interface.

Navigation wraps naturally without a JavaScript menu. At 48rem, columns become
one column; keyboard focus is outlined. No animation, tracking, remote font,
cookie, runtime framework or product service is needed. The static build uses
Node already required for the TypeScript UI, with no additional dependencies.

## Files and commands

`site/` contains HTML and CSS. `scripts/site.mjs` builds and serves the site.
The build copies the existing committed resident screenshot into the output,
checks local link targets and anchors, and writes `dist/site/`.

```powershell
npm run build:site
npm run preview:site
# Open http://127.0.0.1:18320/townrecord/
# Ctrl+C stops this preview; it does not run TownRecord or open a data profile.
```

`npm run dev:site` builds then serves the same output. Relative asset URLs work
at `/townrecord/` and at a domain root. There are no client-side routes. The
manuals link to maintained GitHub Markdown, where Mermaid renders natively.
The technical manual includes architecture, publication and recovery diagrams.

## Public deployment

Public URL: https://scottconverse.github.io/townrecord/
Prepared 0.2.0 release: https://github.com/scottconverse/townrecord/releases/tag/v0.2.0
Prepared direct download: https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TownRecord-0.2.0-windows-x64-setup.exe

The signed 0.2.0 installer completed bounded installed acceptance. Publication,
Pages deployment and post-delivery verification remain pending; this file does
not claim they occurred. The existing
[v0.1.0 release](https://github.com/scottconverse/townrecord/releases/tag/v0.1.0)
and historical November13 publication/site evidence remain unchanged.

The `Publish website` Actions workflow remains **manual only**. GitHub Pages uses
GitHub Actions. Run that workflow from an approved main commit for future site
updates. It builds `dist/site/` and deploys through the Pages environment.
Relative asset paths work under `/townrecord/`. It does not tag or publish the
Windows installer or Python package. Release assets retain the exact tested
installer, wheel, corresponding source, dependency source/license assets and receipts.

## Verification record

Build, local link/anchor validation and browser review at desktop, tablet and
mobile widths are required for this requested website delivery. Their observed
results and merge identity are recorded in the completion handoff. Existing
installed-product evidence is reused; site work does not change the wheel.
