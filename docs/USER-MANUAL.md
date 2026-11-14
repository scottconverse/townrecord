# TownRecord user manual

TownRecord helps a community keep a searchable record of public meetings. An
operator collects source documents and transcripts, checks them, and publishes
a reviewed snapshot. Residents can read that snapshot without an account.
TownRecord is part of the Townlight umbrella. This manual covers the tested
Windows community-record service, within the acceptance scope linked below.

**Start here:** [Windows installation and recovery](WINDOWS-COMMUNITY-OPERATIONS.md)
for the person running the service; [resident instructions](#for-residents) for
readers; [technical manual](TECHNICAL-MANUAL.md) for developers and IT.
[Download the 0.2.0 Windows installer](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TownRecord-0.2.0-windows-x64-setup.exe) and read the
[release notes](https://github.com/scottconverse/townrecord/releases/tag/v0.2.0). These release links are prepared for publication;
public availability remains pending in the [delivery handoff](COMMUNITY-COMPLETION-HANDOFF.md).
The [acceptance record](COMMUNITY-ACCEPTANCE.md) distinguishes current Windows
observations from historical evidence.
The [historical 0.1.0 Python-wheel preview](https://github.com/scottconverse/townrecord/releases/tag/v0.1.0)
remains available for advanced CLI use; it is not the new Windows installer.

## Before the first meeting

Use the Windows installer and open **TownRecord** from Start Menu. It opens the
workspace signed in. Confirm your community timezone, daily time and accessible
sample recording. **Save settings and apply** restarts automatically, then the
numbered checklist guides community/body, source and tool setup. Python/FFmpeg
are bundled; Node is only for contributors. After reload or sign out, reopen
from Start Menu. Closing the browser leaves retrieval running; **Stop TownRecord**
stops it. Windows sign-in startup, where enabled, uses the same account and does
not wake the PC. Advanced nonpackaged CLI mode retains manual write-token entry.
The handoff records the exact signed installer and its remaining acceptance work;
the earlier 0.1.0 observations retain their own scope.

For other computers, IT must provide a reachable community URL and HTTPS for
operator access. A localhost address always points to the reader's own computer.
There is no automatic public hosting or domain setup. This is not a machine-wide
Windows service.

1. Open TownRecord from Start Menu. Its one-use launch signs this tab in; access
   stays in memory. Sign out on a shared machine, then reopen from Start Menu.
2. Create the community and public bodies with names matching their source.
   The accepted pilot used Longmont City Council and Planning and Zoning Commission;
   it does not imply a city endorsement or partnership.
3. Add the supported source URL, provider and coverage dates. Document downloads
   may use a separate storage origin; enter only the exact verified origin.
   For the observed Longmont PrimeGov source these were
   `https://longmont.primegov.com` and `https://pgwest.blob.core.windows.net`.
4. Validate and save the source, then retrieve the selected coverage. Check the
   result, not just the fact that the job was queued. Bodies outside the selected
   coverage are skipped rather than silently treated as imported.

See [connector capabilities and limits](CONNECTORS.md) before choosing another
provider. A listed format is not a guarantee of live access to every customer site.

## A daily operator session

Check Processing for failed or paused work. Review each meeting's title, body,
date, source links, documents and recording. Open the original document when
extracted text is missing or looks wrong. A document named “Minutes” is not proof
that the body approved it. Scanned or incomplete source material can remain a gap.

| What you see | What to do |
|---|---|
| Work queued or running | Wait for its terminal result before claiming acquisition. |
| Paused or failed work | Read the reason, resolve that cause, then use retry. |
| Wrong title, date, body or cancellation/continuation information | Correct the meeting and record a reason. Check its history. |
| Document or recording attached to the wrong meeting | Choose the correct destination and give a reason. A moved recording becomes the destination's primary recording. |
| Missing recording, captions or document text | Keep the gap visible; check the publisher and source details. Do not substitute invented text. |
| New source material after publication | Review it and explicitly republish; retrieval alone does not change the public snapshot. |

Daily retrieval runs at the configured local time while the service is running.
The scheduled window is 30 days back and 60 days ahead; explicit manual retrieval
uses the chosen coverage dates. The service does not wake a sleeping computer.

## Captions and local transcription

Source captions are evidence supplied by the source, not certified minutes.
When they are missing or unsuitable, the operator can create a local transcript
for the recording. Under Processing, set up/check yt-dlp and TextFlowKit. The
Windows installer includes FFmpeg and a pinned, signed TextFlowKit
0.1.19 runtime without model weights. TextFlowKit setup verifies and copies that
local runtime into managed storage, so its initial bootstrap can work offline.
Model downloads, yt-dlp setup/checks and source recordings still need internet
access and storage. Online update checks retain the working runtime if a newer
version fails installation or its check. CLI users follow the separate tool
installation instructions in the Windows guide.

Choose **Create local transcript**, provide a reason, and check the resulting
job. This version uses Whisper small through TextFlowKit on the local machine.
Audio is not sent to a hosted transcription provider by that path. Transcription
can take substantial CPU time; a failed download or unfinished job is not a
transcript. Playback and caption access depend on the publisher and network.

Review names, numbers and decisions against the recording. Machine-generated
text remains provisional and visibly labeled when published. The
[Whistle/Whisper comparison](TRANSCRIPTION-COMPARISON.md) explains the choice;
it is a bounded comparison, not a general accuracy guarantee.

## Publish, correct and withdraw

```mermaid
flowchart LR
  A[Acquire private source material] --> B[Operator checks record and gaps]
  B --> C[Publish with reason]
  C --> D[Resident snapshot]
  A --> E[Later additions or corrections]
  E --> B
  D --> F[Withdraw with reason]
  F --> G[Public access removed; history retained]
```

Publish only the reviewed meeting, with a reason. Publication captures the
meeting, selected document content and transcript. Later private changes do not
silently replace it. To correct public information, review the revised record and
publish again. Withdrawal removes current resident access and document downloads;
it retains internal history and cannot recall files already downloaded elsewhere.

## For residents

1. Open the community's TownRecord URL. No resident account is required.
2. Browse meetings or filter by public body and date. Search a topic to find it
   in available published text. Missing text cannot produce a search hit.
3. Open a meeting to read its documents and available transcript. Use original
   source links to check provenance and download available published documents.
4. Follow a transcript or agenda timestamp to the original recording. An agenda
   item without a recorded time cannot offer a precise recording position.
5. Share the meeting URL (`/?meeting=ID`) using the community's reachable server
   address. A withdrawn record will no longer be available there.

Machine text is not an official account of a vote or a certified transcript.
Consult the original recording and approved records when accuracy matters. Report
an apparent error to the community's operator with the meeting URL, document or
timestamp and proposed correction. This version has no resident correction form.

## Export a meeting to TownReporter

After publication, use the meeting's evidence export. In TownReporter, open
**Sources & scan**, select the TownRecord evidence file and import it. The accepted
integration creates an unpublished **New lead**; it does not write or publish an
article, start a scan or call a model. The evidence preserves source links,
published revision and available full text and timestamps.

Importing the same revision returns the existing lead and preserves newsroom
edits. Changed evidence requires a newly published revision. The receiver accepts
at most 8 MiB of UTF-8 evidence JSON. If rejected, read the validation message;
do not rename arbitrary files to JSON or strip provenance to force acceptance.
TownReporter is separate software, not bundled into the TownRecord service.

## Recovery and troubleshooting

| Problem | Next action |
|---|---|
| Page does not open | Open TownRecord from Start Menu; advanced CLI users check their console and host/port. |
| Operator access rejected | Reopen from Start Menu; CLI users check the correct named write token. |
| A source download is rejected | Verify the source and exact download origin. Do not allow arbitrary hosts. |
| A transcript job fails | Read its reason; check recording access and managed tools/FFmpeg before retrying. |
| Newly retrieved content is absent publicly | Review and republish the meeting. |
| Resident cannot open your link | Replace a loopback URL with the community URL configured by IT. |
| Retrieval did not run overnight | Check service uptime, timezone, daily time and paused causes in Processing. |

Back up before changes and on the community's chosen schedule. Use **Create verified backup** in the workspace; it stops workers, verifies the
backup and restarts. Protect the backup as private information.
It includes unpublished material. Restore into a new directory, retain the old
profile, and check a known meeting and retrieval before using the restored copy.
The [Windows operations guide](WINDOWS-COMMUNITY-OPERATIONS.md#backup-and-recovery)
explains the desktop restore form, advanced CLI commands and exclusions.

## What was actually verified

The exact signed 0.2.0 installer passed clean installation under an ordinary
Windows account, first-run settings, community/body/source setup and both managed
tools. The provider selector matched all 31 installed choices; live acceptance
used Longmont PrimeGov, not every provider. Real retrieval completed 12 downloads,
nine PDF text extractions and three caption jobs. Council and Planning records
were reviewed, published, exported and opened by residents through the installed
interface; sign-out cleared private content and reload offered Start Menu reopen.

A direct managed-tool check transcribed a 120-second public Council audio sample
with Whisper small on CPU in 39.398 seconds: 34 segments, 329 words, final timestamp
119.820 seconds. This checks the installed bundled-Python tool path; it is separate
from the earlier full-meeting transcription. Verified desktop backup/restore also preserved the original profile and reopened
the restored publication. Remaining lifecycle checks and public delivery stay
pending in the acceptance record.

The earlier 0.1.0 candidate's real two-body acquisition, full source captions, operator corrections, resident
navigation, a full 98-minute local transcription, two full TownReporter browser
imports, scheduling and backup/restore passed in isolated Windows acceptance.
Synthetic fixtures cover staged agenda/recording/minutes arrival and republication.
These do not establish every provider, human transcript certification, city adoption
or months of unattended operation. See the linked acceptance record for identities
and distinctions. Scott decides whether any further human trial is required.
