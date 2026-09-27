# TownRecord: an open-source civic portal for your own computer

**Product specification, draft 0.2**<br>
Date: September 27, 2026<br>
Status: draft. Draft 0.2 records the nine decisions made on September 27, 2026 (Section 20).<br>
Repository: github.com/scottconverse/townrecord for now. It moves to a townrecord GitHub organization later (the organization name was free on September 27, 2026).<br>
License: Apache-2.0.<br>
Reference instance: Longmont, Boulder County and Colorado.

---

## 1. Summary

TownRecord is a free, open-source desktop application. A citizen or a reporter installs it on a Windows or macOS computer and points it at one place: a city, the county it sits in, and the state. TownRecord then does four things:

1. It finds every government body in that area, including school boards, special districts, city boards and commissions, and the federal delegation, and it finds each body's meeting videos and public records.
2. It captures the transcript of every public meeting video, and it downloads the agendas, packets, minutes and other public records.
3. It links the transcripts to the records: which agenda item was under discussion, which packet page a speaker was talking about, what the vote was, and where the sources disagree.
4. It gives the user search, alerts, grounded AI answers, summaries and a fact-check tool, all with citations to the exact record page or video timestamp.

AI models do the reading and the writing. The user chooses the models: local models (Ollama, LM Studio, llama.cpp), cloud APIs through a LiteLLM-style gateway, or subscription sign-ins such as Claude and ChatGPT (Codex).

TownRecord is the non-profit, local-first counterpart of Citizen Portal (citizenportal.ai). It does most of what Citizen Portal does, for one area, on one person's machine, with no account, no subscription and no tracking. It also has a local API, so that TownReporter and other tools can use its transcripts and records.

## 2. Why it runs on the user's computer

This is a design requirement, not a preference. Three facts drive it.

**YouTube blocks data centers.** Most local governments post meeting video on YouTube. On September 27, 2026, a cloud session asked YouTube for the caption track of the Longmont City Council study session of June 2, 2026. YouTube refused every method: the yt-dlp caption download got "HTTP 429" and then "Sign in to confirm you're not a bot", the player API returned "LOGIN_REQUIRED", and the watch page returned a redirect with no content. The same video, opened the same day in a browser on a home connection, loaded normally, and its full transcript (4 hours 43 minutes, with a timestamp on every line) was readable through the "Show transcript" panel. A home connection is what makes transcript capture work. TownReporter also runs its meeting capture on a home machine.

**Some records systems block automated fetch tools, but not a local program.** Longmont's agenda system (PrimeGov) blocks AI web-fetch tools with a robots rule on the whole site. Its public JSON API and its document downloads work normally from an ordinary program. A local application can read the records the way the city's own website reads them.

**Economics.** A citizen or a small newsroom can't pay per-seat or per-hour fees for civic data. Local models are a one-time hardware cost. Cloud and subscription models are optional, and the user controls what they cost.

TownRecord is not built for a data center, and the spec does not try to make it work in one. A hosted mirror of its output is possible (Section 15, optional publishing), but capture always runs on the user's machine.

## 3. Goals and non-goals

### Goals

- G1. Set up a whole area (city, county, state and the bodies inside them) from a short wizard, with AI-assisted discovery and human confirmation.
- G2. Capture the transcript of every public meeting video from the watched bodies, reliably, from a home connection.
- G3. Download and index the public records of the same bodies: agendas, packets, minutes, ordinances, resolutions, budgets.
- G4. Cross-reference transcripts and records, with citations to exact pages and timestamps.
- G5. Give grounded outputs: search, alerts, summaries, answers and fact checks. Every sentence cites its source.
- G6. Let the user pick the AI model for each task: local, cloud API or subscription sign-in.
- G7. Offer a stable local API (HTTP and MCP) that TownReporter and other tools can use.
- G8. Run on Windows and macOS, on modest hardware.
- G9. Optionally publish a static public site, with human approval for each item.

### Non-goals

- N1. No hosted service, no accounts, no subscriptions, no payments, no affiliate program, no advertising.
- N2. No national coverage from one install. One install watches one area. Many installs can share data (Section 16.4) but that is not a v1 feature.
- N3. No live publishing without a person. Nothing leaves the machine unless the user sends it.
- N4. No records requests. The product uses published public records only.
- N5. No login bypasses, CAPTCHA solving, paywall circumvention, or use of the user's personal YouTube login.
- N6. Not a newsroom. TownReporter is the newsroom. TownRecord is the watcher and the archive that TownReporter can read.

## 4. Users and main tasks

| User | Main tasks |
|---|---|
| Citizen | "Tell me when the council talks about my street, the new data center, or the water rates." "What did they decide last night?" "What did my council member say about this?" |
| Local reporter or columnist | Find the meeting and the moment. Pull the packet page. Check a quote. Check a vote. Fact-check a draft before print. |
| TownReporter (software) | Read transcripts, segments, agenda alignments, votes and records through the API, and receive events when new meetings are captured. |
| Civic group or teacher | Follow a topic across the city, the county and the state. Build a timeline. Export a record set. |

## 5. What Citizen Portal does, and what TownRecord does with each feature

Sources: citizenportal.ai pages read on September 27, 2026 (home, about-us, feed pages, an article page, hottopics, today, try.citizenportal.ai/gov) and a third-party review (aichief.com). The site loads most content with JavaScript, so this list comes from the site's own descriptions and page structure, not from full use.

| Citizen Portal feature | What it is | TownRecord |
|---|---|---|
| Coverage of five levels | Federal, state, county, city and school board, all 50 states and territories | Same levels plus special districts and city boards, for ONE area per install |
| Location feeds | State > county > city breadcrumbs, "My State / My County / My City / My School Board" | Same hierarchy, built from the setup wizard |
| AI articles per meeting | Summary, full narrative, AI disclosure, "report an error" | Meeting briefs, grounded, every sentence cited to a timestamp or page. Drafts only, never auto-published |
| Meetings tab | Meeting recordings per location | Meeting page: video link, transcript, agenda items with times, packet, minutes, votes, missing-record notes |
| Search | "Search over 500,000 hours of searchable video content" | Full-text and semantic search over local transcripts and records |
| Transcript clips | "Highlight transcript excerpts to create and save video clips" | Clips as timestamp ranges with a shareable link; optional local video cut with ffmpeg when the user has the file |
| Alerts | "Set up custom alerts so you don't miss a thing" | Local alerts on keywords, topics, people, addresses, agenda items; desktop notifications, RSS, optional email through the user's own mail account |
| AI chat | "Ask Citizen Portal GPT about what Government Officials are saying" | Grounded Q&A over the local archive, citations required, "not found" allowed |
| Hot Topics and topic following | 1,000+ topics, ranked by number of states and locations | Topics across the user's area, ranked by bodies and meetings, with a timeline per topic |
| Spending dashboards | Budget tracking by jurisdiction | Version 2: budget and appropriation tables extracted from packets, with page citations |
| Daily summaries ("Today") | Daily government activity summaries | Daily digest of new meetings, new records, alerts and missing records |
| Government offering | Transcription and summaries for city meetings, "Zoom, Microsoft Teams, YouTube, and more" | Not a service. Any government may install TownRecord itself |
| Business model | Founding member plan, $15/month Pro tier (per the review), affiliate program | None. Open source, free |
| CivIQ "message your representative" links with tracking parameters | Referral to a partner messaging service | Not included. The people pages give the official's public contact details only |
| Social media accounts, share buttons | Distribution | Share links from the optional public site only, with no tracking parameters |

## 6. The area model

### 6.1 Levels

TownRecord watches one "area," which the user defines once:

1. **State.** The legislature (both chambers and committees) is on by default. The setup also shows a checklist of state boards and commissions, all off by default, that the user can turn on. A board whose adapter is not built yet shows as "not yet supported." Colorado candidates, to verify during build: the Public Utilities Commission, the Energy and Carbon Management Commission, the Transportation Commission, the State Board of Education and the Colorado Water Conservation Board.
2. **County.** The county commissioners (board of county commissioners), plus county boards the user selects. A city can be in more than one county. Longmont is mostly in Boulder County, with an eastern part in Weld County. The wizard must allow two or more counties.
3. **City.** The city council, including regular sessions and study sessions, plus city boards and commissions (planning and zoning, advisory boards, authorities such as an urban renewal authority).
4. **School boards.** The school districts that serve the city. A district can cross city and county lines (St. Vrain Valley Schools does).
5. **Special districts.** Water, fire, library, transit, power, parks and metropolitan districts that serve the city. Examples near Longmont: Platte River Power Authority, Northern Water, RTD.
6. **Federal delegation.** The U.S. House member for the district and the two U.S. senators. For these, TownRecord tracks public statements, hearings and votes, not meetings of a local body.

The user can also choose to watch every city in the county, and every county in the state, but the default is the user's own city, its county or counties, and the state. This keeps the first capture small.

### 6.2 Data objects

- **Jurisdiction**: state, county, city, school district, special district. Has a type, a name, an official identifier where one exists (Census FIPS or GEOID, NCES district ID for schools, the state's local-government ID for special districts), a parent, and a list of the jurisdictions it overlaps.
- **Body**: a group that holds public meetings (city council, planning and zoning commission, board of education). Belongs to one jurisdiction.
- **Person**: an elected or appointed official, with the seats they hold and the dates.
- **Source**: a place where a body publishes something. Types: meeting portal, video channel, records archive, website news, legislature system. Each source has a status (suggested, accepted, rejected, broken) and a record of who suggested it and why (TownReporter lesson: suggested sources are a status with reason and origin, and a person accepts or rejects them).
- **Meeting**: one sitting of a body, with date, type (regular, study, special, executive), and links to its video, agenda, packet and minutes.
- **Video**: a recording on a video source, with capture state and artifacts.
- **Record**: a document (agenda, packet, minutes, ordinance, resolution, budget, report), with its file, hash, text and page map.
- **Transcript** and **Segment**: timed text for a video, with the source of the text (publisher captions, auto-captions, or local speech-to-text).
- **Agenda item**: a numbered item of a meeting, with title, identifiers (ordinance and resolution numbers), and a time range in the video when known.
- **Vote**: the result on an item, with its evidence and source precedence (Section 10.4).
- **Citation**: a pointer to evidence (Section 10.5).

## 7. Setup and source discovery

Discovery is the "find all the YouTube pages and records systems" function. It runs once in the setup wizard and again on a schedule to catch changes.

### 7.1 The wizard

1. The user enters a city and state, or an address. An address is used only to find the city, county, school district and congressional district, and it is not stored unless the user asks.
2. TownRecord builds the jurisdiction list from public reference data (candidate sources, to confirm per state during build: Census geography files for cities and counties, NCES for school districts, the state's local-government directory for special districts, and a public dataset of members of Congress for the federal delegation). Colorado's Department of Local Affairs publishes a local government directory that includes special districts.
3. For each jurisdiction, TownRecord runs discovery (7.2) and shows the results as suggestions.
4. The user accepts or rejects each suggestion. Nothing is watched until the user accepts it.
5. TownRecord runs a first capture for the last 30 days (the user can change this) and shows progress per body.

### 7.2 Discovery steps for each jurisdiction

Discovery is deterministic first, and AI second. The AI suggests. Code verifies. A person decides.

1. **Find the official website.** Start from the reference data. If there is no URL, search the web, and have the AI pick the result that is the government's own site. Verify with deterministic checks (domain type such as .gov or the state's usual pattern, the jurisdiction name on the page, contact details).
2. **Find the meeting portal.** Read the website's pages about meetings, agendas and council. Detect the vendor from links and page markers. Known vendors: PrimeGov, Legistar (Granicus), CivicClerk, eScribe, BoardDocs, Granicus video, CivicPlus, Municode Meetings, OnBase public access, Laserfiche, iQM2, Swagit. TownReporter's render-detect.ts lists 7 of these hosts (PrimeGov, Legistar, Granicus, CivicClerk, BoardDocs, CivicPlus, Municode), as sites that need a browser to render JavaScript. It does not detect eScribe, OnBase, Laserfiche, iQM2 or Swagit.
3. **Find the video channels.** Look for YouTube links on the website and on the meeting portal. PrimeGov meeting records have a videoUrl field that points to the exact YouTube video. Resolve each YouTube handle to its channel ID (Section 8.1). Also look for a public media station that records the same meetings. In Longmont, the City of Longmont channel (@CityofLongmont, channel ID UCH5_wkpLrKYb1JuUk6-UdNg) and Longmont Public Media (@LongmontPublicMedia, channel ID UCXFW3IRzfCc6q_XM-uutAmw) both record council meetings. Both IDs were confirmed on September 27, 2026.
4. **Find other video hosts.** Granicus and Swagit video players, Vimeo, Facebook video, Zoom recordings, and state legislature audio or video systems. Each gets a video adapter (Section 8.8).
5. **Find the records archive.** Minutes archives, ordinance and code libraries (Municode, American Legal, eCode360), document archives (OnBase, Laserfiche).
6. **Classify each video.** Use title patterns, learned per channel, to separate meetings from other videos. TownReporter lesson: keep a configurable keyword list with word boundaries and a skip list ("this week in council", "block party" and similar).
7. **Match videos to meetings.** Match on date and body name. When two channels record the same meeting, keep both, and mark one as primary by channel priority. Never rename a video.

### 7.3 Re-discovery

Every week, TownRecord checks each accepted source. A source that fails three times in a row becomes "broken," and the user sees a plain message with the last error. A new channel or portal found during re-discovery becomes a suggestion, not a watched source.

## 8. Video capture

This section follows TownReporter's working meeting-capture design (release 0.6.60 and later), with the changes needed for a desktop product. File references are to the TownReporter repository at version 0.6.76.

### 8.1 Listing a channel's videos

Use these methods in order. Record which method produced each listing, and show it to the user ("Read YouTube with the official API" or "Read the public feed instead").

1. **YouTube Data API v3**, when the user has saved an API key. At most three calls of one quota unit each: channels.list (by id, forHandle or forUsername, part=snippet,contentDetails) to get the uploads playlist; playlistItems.list for the newest 50; videos.list (part=snippet,contentDetails,liveStreamingDetails,status) for details. Never call search.list, which costs 100 units. Send the key in the x-goog-api-key header, never in a URL. Count quota before reading the reply. A quota refusal blocks the API for the rest of the Pacific day, because Google resets quota at Pacific midnight. (TownReporter: youtube-data-api.ts, youtube-data-api.server.ts.)
2. **Channel page and RSS feed.** Read the channel page to get the channel ID (the "externalId" value). A consent cookie may be needed. Then read https://www.youtube.com/feeds/videos.xml?channel_id={id}, which lists recent uploads (15 entries in the September 27 test).
3. **Channel tab HTML** (/streams and /videos). YouTube changes this markup often, so it is a fallback.
4. **yt-dlp flat listing**: python -m yt_dlp --flat-playlist --dump-single-json --playlist-end 50 --js-runtimes node {channel}/streams (or /videos), with a 60-second time limit. TownReporter's reason for this last step: without it, when YouTube changes its markup, the desk "silently discovers zero meetings."

### 8.2 Readiness

Before capture, find out whether a video is upcoming, live or finished. Use the API's liveBroadcastContent and duration if available; else the player endpoint status (LIVE_STREAM_OFFLINE means upcoming); else yt-dlp --dump-single-json and its live_status field. Skip upcoming and live videos, count them, and retry later. Treat "unknown" as "waiting for status metadata (will retry)," not as a failure.

### 8.3 Captions first

Captions are small. TownReporter measured about 136 KB of captions for a two-hour meeting, against about 120 MB of audio. The capture command, from TownReporter's meeting-capture-ytdlp.ts:

```
python -m yt_dlp --skip-download --write-subs --write-auto-subs --write-info-json
  --sub-langs en --sub-format srv3/vtt/best --js-runtimes node
  --sleep-subtitles 2 --sleep-requests 1
  --download-archive <archive file> --paths <dir> [--continue]
  -o <dir>/%(id)s.%(ext)s <watch url>
```

Rules:

- Parse srv3 (and VTT) into timed segments.
- HTTP 429 means "rate limited." It is a paced retry on a later pass. It is NOT a reason to download audio.
- Stop kills the process. A stopped capture resumes with --continue in the same folder.
- Size and length limits: default 8 hours and 500 MB. Larger meetings need the user's confirmation, and a refusal is recorded with its reason.
- Run yt-dlp with an allow-listed environment, never the full user environment, and never through a shell.

### 8.4 Fallbacks when captions fail

In order:

1. **The sister channel.** The same meeting on another watched channel. Mark the transcript "from a configured sister channel."
2. **The "Show transcript" panel** in an embedded browser (Playwright or the desktop app's own web view): open the watch page, expand the description, click "Show transcript," and read the panel. The September 27, 2026 test read a full 4 hour 43 minute transcript this way from a home connection. Note: in the same test, fetching the caption track's baseUrl directly returned an empty file, because YouTube now requires a token that only its player adds. Do not build on direct caption URLs.
3. **Audio and local speech-to-text** (8.5).

### 8.5 Audio fallback with TextFlowKit

Download audio only when captions are missing or unusable, and record the reason (audio_trigger_reason) every time. Command: yt-dlp -x --audio-format opus --audio-quality 5 --write-info-json --js-runtimes node --sleep-requests 1 --download-archive ... -o ....

Transcribe with TextFlowKit (Apache-2.0, on PyPI as textflowkit, needs ffmpeg): textflowkit transcribe {audio} --formats json --output-dir {dir} --model small --language en. TownReporter measured "790 s of council audio in 167 s, about 0.21x realtime" on CPU. Run one transcription at a time. Time limit: 1.5 times the audio length plus 600 seconds, or 3,600 seconds if the length is unknown. Store the output with its provenance (tool version, model, device, audio hash) so a local transcript can never be mistaken for the publisher's captions. TextFlowKit output goes into the same transcript path as captions, with no second code path.

### 8.6 Artifacts and integrity

- The storage root is an absolute path that the user chooses, outside the application folder. TownRecord proves it can write there by writing and deleting a test file when the user saves the setting.
- Artifacts are content-addressed and never change: transcript-{sha256}.srv3, .vtt or .json, and info-{sha256}.json. Write to a temporary file, flush it to disk, hash it again, rename it into place, and check the hash once more. Different bytes at an existing path are an error.
- A missing info.json sidecar is recorded with its reason. It never passes as a silent success.
- On startup, reconcile files on disk with database rows.

### 8.7 Deduplication and revisions

- The database is the source of truth. Regenerate the yt-dlp --download-archive file from the database before each capture, because a stale archive file can hide a meeting that was never captured.
- Detect caption revisions by hash, then caption revision time, then duration. A capture is provisional for 24 hours after the meeting ends. Recheck it every 3 hours during that time. Two unchanged checks, at least 1 hour apart, settle it. A capture made more than 24 hours after the meeting is final at once. At 48 hours after the first capture, settle it anyway; if the transcript was still changing, mark it "settled under churn." (TownReporter meeting-revision.ts.)
- A revision never rewrites anything already exported or published. It raises one review item for the user.

### 8.8 Other video hosts

Each non-YouTube host gets an adapter with the same outputs (listing, readiness, captions or media, artifacts). yt-dlp supports many hosts, so most adapters are a URL pattern plus a listing method. When a host has no captions, TownRecord downloads the audio and uses TextFlowKit. State legislatures often publish committee audio in their own systems. Those need a dedicated adapter per state (Colorado first).

### 8.9 Tools installed with the application

TownReporter's capture depends on python -m yt_dlp, but its setup documents do not say how to install yt-dlp. TownRecord must install and manage its tools itself:

- A private Python runtime with pinned versions of yt-dlp and TextFlowKit.
- A JavaScript runtime for yt-dlp's --js-runtimes option.
- ffmpeg.
- Chromium for Playwright (or the platform web view) for the transcript-panel fallback.

yt-dlp must update often, because YouTube changes often. TownRecord checks for a new yt-dlp release daily, tests it against one known video, and switches to it only if the test passes. The previous version stays available for rollback.

### 8.10 Policy for video capture

- Captions first. Audio only when needed, with a recorded reason. No video download unless the user asks for a clip.
- Pace every request (the sleep options above). Honor HTTP 429.
- Do not use the user's YouTube login or browser cookies. Do not solve bot checks.
- Keep captured media and transcripts on the user's machine. The optional public site publishes excerpts and links to the original video, not copies of the video.
- A setting, "Local transcription only," turns off caption capture. With it on, TownRecord downloads the audio for every meeting and makes every transcript with TextFlowKit on the user's own machine. It costs much more download and computing time (Section 8.5), and the setup explains that.
- YouTube's terms of service restrict automated access. There is no lawyer review in the project plan. TownRecord includes an AI legal-risk review (Section 15.1) that the user can run before publishing. Every page of that review starts with: "This is not legal advice. Talk to your lawyer first."

## 9. Public records

### 9.1 Records adapters

Every records system gets an adapter with one interface:

- list_meetings(body, from, to) returns meetings with their documents.
- get_document(document) returns the file, its content type and its hash.
- list_archive(query) (optional) searches an archive such as OnBase or a code library.
- health() tests the adapter against one known meeting.

### 9.2 PrimeGov (verified September 27, 2026)

- Meeting lists: GET https://{city}.primegov.com/api/v2/PublicPortal/ListArchivedMeetings?year={yyyy} and .../ListUpcomingMeetings. Each meeting has id, dateTime, title, videoUrl and documentList. Each document has templateId, compileOutputType and templateName ("Agenda", "Packet", "HTML Agenda").
- Download: https://{city}.primegov.com/Public/CompiledDocument?meetingTemplateId={templateId}&compileOutputType=1. The server answers with a redirect to a signed storage link on pgwest.blob.core.windows.net that expires after approximately two days. Follow it, store the file, and cite the portal and meeting, never the signed link.
- HTML agenda: https://{city}.primegov.com/Portal/Meeting?meetingTemplateId={templateId} for documents with compileOutputType 3. It contains a data-videolocation value, in seconds, for agenda items (Section 10.2).
- AI web-fetch tools are blocked by PrimeGov's robots rule on the whole site. TownRecord is a user agent that acts for one person. It identifies itself honestly in its User-Agent string, paces its requests and caches everything. (TownReporter sends a full Chrome User-Agent string with "TownReporter/1.0" added. TownRecord uses its own name first.)
- TownReporter's PrimeGov code does not read the videoUrl field. The TownRecord adapter must.

### 9.3 Other adapters

| System | Where it is used (examples) | Status |
|---|---|---|
| PrimeGov | City of Longmont | Verified. Port from TownReporter primegov.ts |
| eScribe | Boulder County (pub-bouldercounty.escribemeetings.com) | To build. Check the public meeting API during build |
| Legistar (Granicus) | Many large cities and counties | To build. Legistar has a public Web API; confirm per client |
| CivicClerk | Many small cities | To build |
| BoardDocs | Many school boards | To build. Check which system St. Vrain Valley Schools uses |
| OnBase public access | Longmont's older records (records.longmontcolorado.gov) | To build. Browser-driven; the search page needs a real browser |
| Code libraries (Municode, American Legal, eCode360) | Municipal codes | To build, read-only |
| State legislature | Colorado General Assembly (leg.colorado.gov): bills, fiscal notes, votes, committee audio | To build |
| Federal | Congress.gov data, House and Senate votes, member press releases | To build (v4) |

TownReporter lesson: it has no adapters beyond PrimeGov, and two files default to Longmont's PrimeGov address (primegov.ts and meeting-agenda-items.ts). TownRecord must never default to one city. Every adapter call takes its origin from a configured source.

### 9.4 Rules about where records are

These are true for Longmont, and similar patterns exist elsewhere. Each adapter must record its own rules.

- Minutes of a regular session are not a separate document. They go into the packet of the next regular session, under "Approval of Minutes." Example: the September 8, 2026 minutes are in the September 22, 2026 packet.
- Study sessions have no written minutes. The video is the only record of what a person said.
- The staff memo and full text of an ordinance are in the packet of the first reading. The packet of the second reading has the amended version.
- The packet page number in the "Page N" footer is the same as the PDF page number.

### 9.5 Missing records

A record that should exist and does not is a finding. TownReporter's rule: flag missing minutes when there is no minutes document, 36 hours or more have passed, the body is a council, commission, board or authority, and the meeting was not cancelled or continued. That is "a catalog note, not a story." TownRecord tracks missing records per body, shows how long each one has been missing, and lets the user set an alert.

### 9.6 Document processing

- Size limits: HTML 5 MB, PDF 25 MB by default (TownReporter body-limit.ts), checked on the declared length and again while the file streams. Large packets can be 60 to 170 MB, so packets get their own higher limit that the user can change.
- Extract text with page boundaries. Scanned pages go to OCR, with a local OCR engine first and a vision model second.
- Build a page map: for each page, its text, its footer page number, and the headings on it (item numbers, "ORDINANCE O-2026-58," "CITY COUNCIL COMMUNICATION").

## 10. Cross-reference

This is where TownRecord does more than Citizen Portal appears to do, and more than TownReporter does today.

### 10.1 Identifiers

Extract identifiers from records and transcripts with deterministic code: ordinance and resolution numbers (O-2026-58, R-2026-52), agenda item numbers (9.B, 12.A), dollar amounts, addresses and parcel numbers, names of people who hold seats, and dates. Auto-captions write identifiers differently ("ordinance um 2026-58", "20 26-58"), so the matcher normalizes spoken forms.

### 10.2 Aligning the transcript with the agenda

Use three methods and record which one produced each boundary:

1. **The HTML agenda's video times.** PrimeGov HTML agendas carry a data-videolocation value for items. The clerk enters these by hand, so some are wrong or are placeholders. On the September 8, 2026 Longmont agenda, several items showed 162:30:21, which is longer than the meeting. A value is used only if it is inside the video's length and the transcript near that time mentions the item. TownReporter does not use these values today. This is new.
2. **Spoken transitions** (TownReporter meeting-story-section5.ts): spoken item numbers ("agenda item 9C", number words one to twenty), O- and R- identifiers in written form together with at least one title keyword, and a verbatim title check (the title has at least two words over four letters, and all of them appear in the segment). TownRecord adds a matcher for spoken identifiers ("ordinance um 2026-58").
3. **No alignment.** If neither method works, the meeting gets one untimed record and a named reason, for example "no spoken transcript transition matched a packet item." Never invent item boundaries. In TownReporter's first live pass, 16 meetings failed alignment and said so.

Store the item for each segment in one place only. TownReporter lesson: a second item column on the segments table "is never written by any code path, so every citation returned item: null on all 47,592 rows." The fix was to resolve the item at read time from one table.

### 10.3 Linking talk to pages

For each aligned item, link the transcript range to the packet pages for that item (from the page map). When a speaker says a number, a name or a section ("Section 2.95.060"), look for it on those pages and link the match. This gives the user "what they said" next to "what the document says."

### 10.4 Votes

Source precedence, from TownReporter:

1. A structured vote record, where a government publishes one.
2. The minutes.
3. The packet (for example, a recorded vote in a later packet).
4. The transcript.

"A transcript-only mention is never a tally." When only the transcript has the vote, TownRecord shows the transcript lines (for example, the September 22, 2026 Longmont transcript: "may I have a motion to pass and adopt ordinance 2026-58" and "And that carries unanimously") and labels the vote "from video; minutes not yet available." When sources disagree, TownRecord shows all of them and does not pick one. Each source's availability is named ("not available: no official minutes document yet; expected in the October 6 packet").

### 10.5 Citations

Every claim TownRecord shows, and every sentence an AI writes, carries at least one citation:

- For video: video ID, start and end seconds, the verbatim excerpt, the SHA-256 of the transcript artifact, and the transcript's origin (publisher captions, auto-captions, sister channel, local speech-to-text).
- For records: the source, meeting, document, page number, the verbatim excerpt, and the document's SHA-256.

Before anything is exported or published, a check compares each citation's hash with the current artifact. A citation to an artifact that has changed or disappeared blocks the export (TownReporter meeting-publish-guard.ts).

### 10.6 Speakers

Captions misspell names. In the June 2, 2026 Longmont transcript, "Council member Marcen" is Council Member Jake Marsing. TownRecord finds the chair's recognition of a speaker ("Council member ...") in the lines before a statement and matches it to the body's list of seated officials with a fuzzy match. A match below a set confidence shows as "an unidentified speaker" until the user confirms it (TownReporter masks unverified speakers the same way).

## 11. AI layer

### 11.1 Providers

One provider registry defines every model the application can use. TownReporter rule: "Anywhere an AI does something, the editor must be able to pick the model," and local models are "config, not code." Provider kinds:

| Kind | How it connects |
|---|---|
| Local | Ollama, LM Studio, llama.cpp, found automatically on the loopback address |
| OpenAI-compatible gateway | Any base URL, including a LiteLLM proxy, which in turn can reach most cloud APIs |
| Anthropic API | API key |
| OpenAI API | API key |
| Claude subscription | The claude command-line program, signed in with the user's Claude account |
| ChatGPT subscription | The codex command-line program, signed in with the user's ChatGPT account |

### 11.2 Subscription sign-in

From TownReporter provider-login.server.ts:

- Claude: claude auth login --claudeai prints a URL and listens on a random loopback port. Allow 10 minutes.
- ChatGPT: codex login --device-auth gives a URL and a code that lasts 15 minutes. Do not use plain codex login, which binds a fixed port (1455).
- TownRecord never reads, stores or logs these credentials, and it removes secrets from any error text (TownReporter's rule).
- It checks for a lapsed sign-in before it starts a job, and it tells the user which program to sign in again.
- New TownRecord rule: it never runs a logout command, because that would sign the user out of their own tools.

### 11.3 Running the command-line models safely

The command-line models get no tools except the ones a task needs. TownReporter lesson (0.6.15): an empty allowed-tools list still showed the model a set of refused tools; it tried to use a shell, was refused, and "wrote sandbox-escape musings into the leads." The fix was to run with no tools and let the application do all fetching. Flags TownReporter uses today:

- codex: --ask-for-approval never --disable shell_tool,computer_use,browser_use,apps,plugins,multi_agent,hooks exec --ignore-user-config --skip-git-repo-check --sandbox read-only --ephemeral --json
- codex, also: --model, --color never, and --enable standalone_web_search only when a task needs web search.
- claude: -p --restricted --strict-mcp-config --safe-mode --disable-slash-commands --permission-mode dontAsk --permission-prompts none --no-session-persistence --no-chrome --setting-sources "", then either --tools "" (no tools) or --allowed-tools limited to WebSearch and WebFetch.

Check these flags against each program's current version during build. They change.

### 11.4 Tasks and model choice

Each task has its own model setting, and each setting can be "Automatic":

| Task | What it does | Typical model |
|---|---|---|
| Classify | Is this video a meeting? Which body? | Small local model |
| Discover | Pick the official site, portal and channels from search results | Local or cloud |
| Align | Help find item boundaries when code can't | Local |
| Summarize | Meeting brief, item summaries | Local or cloud |
| Answer | Grounded Q&A | Cloud or subscription for quality; local allowed |
| Fact-check | Check a user's draft against the archive | Cloud or subscription |
| OCR | Scanned pages | Local OCR, then a vision model |
| Transcribe | Speech-to-text | TextFlowKit (Whisper models), local |
| Embed | Semantic search vectors | Local embedding model |

"Automatic" is an ordered list of providers (a ladder). The user can edit it. The first rung that is reachable runs the task.

### 11.5 Failover

From TownReporter automatic-failover.ts:

- Move one rung down for: an expired sign-in, a time-out or empty output, a quota refusal, an unavailable provider or an unreadable reply.
- Do not move for a content refusal. That is final.
- If the user picked a specific local model, fail closed. Never send that work to a cloud provider without the user's choice.
- Record for each job the model that was requested, the model that ran, and why it was chosen.

### 11.6 Time budgets

Budgets per provider kind, editable per task. TownReporter's defaults: command-line providers 420 seconds per job and 150 per call; APIs 38 and 20; local models 40 minutes and 10 minutes. TownReporter measured that a four-hour council meeting draft takes about 20 minutes across nine local-model calls.

### 11.7 Grounding rules

- The model gets only the evidence the application gives it, with citation handles. It writes with those handles. Code then checks that every sentence has a handle, that every quote appears verbatim in the cited artifact, and that every number appears in the cited evidence. A sentence that fails is removed or sent back once for repair.
- "Not found" is a valid answer and is always better than a guess.
- Code measures first; the model rewrites second; code measures again. "A model never decides what counts as a fault." (TownReporter style check.)
- Quotes, numbers, names and URLs never change in a repair. A repair that changes one is rejected.
- A summary never states a vote tally that Section 10.4 does not support.

### 11.8 Cost controls

- Show the expected model calls for a big job before it starts, for example a first capture of 30 days.
- Keep a per-day and per-month usage ledger per provider.
- Test suites never call paid models unless a person turns that on.
- Never load or unload a model in LM Studio or Ollama on the user's behalf without asking. TownReporter's desk "never loads or unloads a model."

## 12. What the user sees

### 12.1 Home

The daily digest: new meetings captured, upcoming meetings with agendas, alerts that fired, records that are missing, and captures that need attention (for example, "rate limited, retrying at 9:40 p.m.").

### 12.2 Area view

The state > county > city tree, with school boards, special districts and boards under their jurisdictions. Each body shows its sources, its last capture, and its health.

### 12.3 Meeting page

- Title, body, date, type, and links to the video on each channel.
- The agenda as a list of items. Each item shows its video time (with how the time was found), its packet pages, its identifiers, and its vote with evidence.
- The transcript, with timestamps, speaker names (confirmed or unconfirmed) and a search box. Clicking a line opens the video at that time.
- The packet and minutes, with page links.
- Notes on missing records and failed alignments, in plain words.
- An AI brief, generated on request or on a schedule, with citations on every sentence.

### 12.4 Search

Full-text search (SQLite FTS5 or similar) and semantic search (local embeddings) over transcripts and records, with filters for level, body, date, record type and speaker. Results show the excerpt, its time or page, and its source.

### 12.5 Topics

A topic is a saved query plus an optional AI description. TownRecord suggests topics from recurring terms across bodies (for example "license plate readers," "Technology Policy Advisory Board," "water rates"). Each topic has a timeline across the city, county and state.

### 12.6 People

A page for each official: seats and dates, meetings attended, statements (with timestamps), motions made and seconded, and votes (with the Section 10.4 evidence). The page shows public contact details from the official website only. It shows no private information about anyone.

### 12.7 Alerts

Alert on: a keyword or phrase in a new transcript or record, a topic, a person, an address or parcel, an ordinance number, a new agenda for a body, or a missing record older than N days. Delivery: desktop notification, an in-app list, an RSS or Atom feed on the local machine, and optional email through the user's own mail account (SMTP). Nothing goes through a TownRecord server, because there is none.

### 12.8 Ask

Grounded Q&A over the local archive (Section 11.7). The answer shows its citations as clickable timestamps and page links. The user can see which model answered.

### 12.9 Fact check

The user pastes a draft story or op-ed. TownRecord:

1. Lists every factual claim (votes, dates, amounts, names and titles, ordinance text, quotes, places).
2. Finds the evidence for each claim in the archive.
3. Gives a verdict per claim: confirmed, wrong, partly right, not in the record, or record not available yet.
4. Gives the citation and labels it "primary record" or "secondary source."
5. Suggests corrected wording. It never changes a quote to match a record. It shows both and flags the difference.
6. Lists the records it read and the records that are missing, with the date each missing record is expected.

This is the procedure tested by hand on September 27, 2026 on an op-ed about Longmont's Technology Policy Advisory Board. It found two claims that the primary records changed (the Axon vote date and what the first-reading vote was) and confirmed a quote on the June 2 video.

### 12.10 Clips

A clip is a start and end time on a video, with its transcript excerpt. It has a share link that opens the original video at the start time. If the user has downloaded the video file, TownRecord can cut a local clip with ffmpeg.

### 12.11 Budgets and spending (version 2)

Extract budget and appropriation tables from packets (for example, the fund table in a budget ordinance), with page citations. Show year-over-year changes per fund. Numbers are copied from the document, never computed by a model. Any computed value (a total, a per-household figure) is computed by code and labeled as computed.

## 13. Local API for TownReporter and other tools

TownRecord runs a local service. The desktop user interface and all outside tools use the same API. There is no second path.

### 13.1 Access

- HTTP on 127.0.0.1 only, by default, on a port the user can change.
- Every request needs a bearer token. The user creates tokens in settings, each with a name and a scope (read, or read and write). A token is shown once.
- Serving on a local network (for TownReporter on another machine in the house) is an option that the user turns on, with a warning.

### 13.2 Endpoints (version 1)

| Method and path | Returns |
|---|---|
| GET /v1/area | The jurisdiction tree, bodies and sources |
| GET /v1/bodies/{id}/meetings?from=&to= | Meetings with document and video status |
| GET /v1/meetings/{id} | One meeting with items, times, votes and missing-record notes |
| GET /v1/meetings/{id}/transcript?format=json (or vtt, txt) | Segments with times, speakers and origin |
| GET /v1/meetings/{id}/items | Agenda items with time ranges, identifiers, packet pages and alignment method |
| GET /v1/records/{id} | Record metadata; /file returns the file; /pages/{n} returns page text |
| GET /v1/search?q=&level=&body=&from=&to=&type= | Search results with citations |
| GET /v1/votes?body=&identifier= | Votes with evidence and source precedence |
| GET /v1/people/{id} | An official's seats, statements and votes |
| POST /v1/ask | Grounded answer with citations |
| POST /v1/factcheck | The Section 12.9 report for a supplied draft |
| GET /v1/citations/{id}/verify | Checks a citation against the current artifact hash |
| GET /v1/events?since= | New and changed items since a cursor |
| POST /v1/webhooks | Register a local URL to receive events |

### 13.3 Events

meeting.discovered, meeting.upcoming, video.captured, video.revised, transcript.aligned, record.added, record.missing, vote.established, vote.conflict, alert.fired, source.broken.

### 13.4 MCP server

The same functions as MCP tools (search, get_meeting, get_transcript, get_record_page, get_votes, verify_citation, factcheck), so that Claude, Codex and other agents can use the archive directly. The MCP server exposes read functions by default.

### 13.5 TownReporter

TownRecord does not change TownReporter in any way. TownReporter is a standalone product and keeps its own meeting capture. The API is public and documented so that TownReporter, or anyone else, can use it. Integration of the two products is a later decision.

### 13.6 Versioning

The API has a version in its path. A field is never removed or renamed within a version. TownReporter lesson: keep one source of truth for each fact, and compute derived values at read time.

## 14. Platform, packaging and hardware

### 14.1 Operating systems

Windows 10 and 11 first. The first release is Windows only. The code is built and tested on macOS (Apple silicon) from the first build, in continuous integration, so that nothing Windows-only gets into it. The signed macOS installer ships at milestone M6. Linux can run from source.

### 14.2 Architecture (decided)

A Python core service with a thin Tauri desktop shell and a React interface.

- yt-dlp, TextFlowKit, LiteLLM and most PDF and OCR tools are Python, so they run inside the core.
- The shell only draws the interface, and starts and stops the core. It holds no domain logic (the TownLight rule).
- The interface talks to the core only through the local API (Section 13). There is no second path.
- TownReporter's logic and tests are ported, not its code. Its code assumes PostgreSQL and a hosted server.

### 14.3 Database (decided)

SQLite, with FTS5 for full-text search and a vector extension for embeddings. One migration system, and no runtime table creation that repeats the migrations. TownReporter lessons: its embedded database fallback (PGLite) loses data when the process stops, and it keeps runtime table definitions that must match migrations "column-for-column," which is a recurring cost.

### 14.4 Installer

- Signed installers for Windows and macOS.
- Private, pinned copies of Python, yt-dlp, TextFlowKit, a JavaScript runtime, ffmpeg and a Chromium build, each with a SHA-256. (TownReporter's installer pins its Node and PostgreSQL runtimes this way in installer/dependencies.json. It does not pin Python, yt-dlp, ffmpeg or Chromium, and TownRecord must.)
- A first-run check that proves each tool works, with plain messages when one does not.
- The yt-dlp update process of Section 8.9.

### 14.5 Hardware tiers

The target is modest hardware, not a workstation.

| Tier | Example | What runs locally |
|---|---|---|
| 0 | 16 GB RAM, no GPU (or an integrated GPU) | Capture, records, search, small classification models. Summaries and answers use a cloud or subscription model. TextFlowKit on CPU (about 0.21x realtime) for the occasional audio fallback |
| 1 | 16 to 32 GB RAM, 8 GB GPU | Adds local summaries and answers with a mid-size model |
| 2 | 64 GB or more of unified memory | Everything local, including large models |

The setup wizard measures the machine and recommends a tier. The user can override it.

### 14.6 Laptops and sleep

A laptop sleeps. After wake, TownRecord catches up on missed scheduled work, one job at a time, and it never runs two scans for the same day.

## 15. Optional publishing

The user can export a static public website of selected meetings, briefs, topics and clips.

- Each item needs the user's approval. Nothing is published automatically.
- The site is static files. The user can host it anywhere (a static host, their own server, or a folder they share).
- Every AI-written item carries a plain disclosure, a list of its sources, and a way to report an error (a mailto link to the user's address, if the user wants one).
- A corrections page. A correction is a dated public note above the story, and the original text stays as published. (This is TownReporter's default. TownReporter can also correct the text and keep the old version internally.)
- No tracking code, no analytics, no share links with tracking parameters.
- The site links to the original video and records. It does not host copies of video.
- It publishes transcript excerpts, not full transcripts, by default. The user can change this per body.

### 15.1 AI legal-risk review

Before the user publishes, TownRecord can run an AI review of the items to be published and of the capture settings that produced them. It uses the model the user picks for this task.

- The review lists risks in plain words: republished captions, quotes from private residents, possible defamation (a claim about a person that the cited evidence does not support), copyrighted material in packets, and YouTube's terms of service.
- Every page of the review starts and ends with: "This is not legal advice. Talk to your lawyer first."
- The review never blocks publishing. The user decides.
- The review is saved with the export, with the model that ran it and the date.

## 16. Scheduling, jobs and reliability

### 16.1 Jobs

A jobs table with claim tokens and heartbeats, as in TownReporter jobs.ts, plus checkpoints so a long job can resume (TownReporter does not have these). Two lanes: a heavy lane (large summaries, drafts) with one worker, and a normal lane with two. Transcription also limits itself to one run at a time. TownReporter lesson (audit ENG-105): with one serial queue, "a 40-minute editorial" job kept a Scan or Draft that was queued behind it from starting. TownReporter's lanes are "editorial" (1) and "default" (2).

### 16.2 Schedule

- Daily scan per body at a local time the user chooses, in the area's time zone. One run per local day, with scheduled and manual runs recorded separately.
- Revision rechecks per Section 8.7.
- Upcoming-meeting checks each evening on meeting days.
- A job that cannot run pauses with a plain reason. It never skips silently. (TownReporter lesson: daily scans silently stopped when the configuring account lost owner status.)

### 16.3 Honest failure

- "Working-but-invisible is a BUG" (TownReporter handoff, Sept. 4, 2026). Every action shows a result the user can see.
- "a negative/absence claim is a probe, not a conclusion" (same handoff). "No meetings found" shows what was checked and how.
- Health checks check real content, not a status code of 200. TownReporter lesson: its public page went blank and "a 200 error page slipped through."

### 16.4 Sharing between installs (later)

Two users who watch the same city can exchange artifacts by hash, so each video is captured once. Content addressing makes this safe: a shared artifact is valid only if its hash matches. This is not in version 1.

## 17. Security and privacy

- No telemetry. No calls to a TownRecord server, because none exists.
- The watch list, alerts and searches stay on the machine. A search sent to a web search engine during discovery is shown to the user first. (TownReporter note: its Dark Desk sends searches to several search engines without asking.)
- API keys go in the operating system's credential store (Windows Credential Manager, macOS Keychain). This improves on TownReporter, which encrypts keys with a key derived from an application secret.
- Keys are write-only in the interface. An environment variable overrides a saved key.
- Outbound fetches go through a guard that blocks private and reserved addresses and DNS rebinding, limits redirects (4) and time (10 seconds), and enforces size limits (TownReporter url-guard.ts, fetch-url.ts).
- Child processes (yt-dlp, TextFlowKit, claude, codex) get an allow-listed environment and no shell.
- The local API listens on the loopback address by default and needs a token.

## 18. Testing and proof

TownReporter's process lessons, as rules:

- A regression test must be shown to fail against the old code before it is accepted. TownReporter found tests whose assertions "could not fail."
- Do not trust a green result from an environment that skipped the failing tests. Run the full suite again after a version change.
- Live proof for capture: each release captures a fixed set of real meetings (for Longmont: one regular session, one study session, one meeting with no captions that needs audio, one upcoming meeting) and checks the hashes and segment counts.
- Live proof for records: each adapter's health() runs against one known meeting per release.
- Live proof for alignment: report how many meetings aligned, by method, and the reasons for the ones that did not.
- Paid models are never called in the normal test run.

## 19. Milestones

| Milestone | Scope | Done when |
|---|---|---|
| M1: One city | Longmont City Council and city boards: PrimeGov adapter, both YouTube channels, captions, audio fallback, alignment (HTML agenda times plus spoken transitions), search, meeting page, API read endpoints | 30 days of Longmont meetings captured and aligned on a Windows machine, with the September 27, 2026 test cases passing |
| M2: The county and the schools | Boulder County (eScribe), Weld County as the second county, St. Vrain Valley Schools, special districts that post video | Each body captured for 30 days, with adapter health checks passing |
| M3: The state | Colorado General Assembly: bills, votes, committee audio or video | One session week captured and searchable |
| M4: Federal delegation | Members' votes, hearings and statements | Profiles for the district's three members |
| M5: Outputs | Alerts, topics, people pages, ask, fact check, clips, MCP server, events, published API documentation | An outside test client reads one captured meeting through the documented API |
| M6: macOS and publishing | Signed macOS installer, static site export, corrections page, AI legal-risk review | Signed installers for both systems |
| M7: Setup for any place | The wizard and discovery for a second city in another state | A tester in another state sets up their area without help |

## 20. Decisions and open questions

### 20.1 Decisions made on September 27, 2026

| # | Decision | Choice |
|---|---|---|
| 1 | Name and repository | TownRecord. Repository at github.com/scottconverse/townrecord for now; it moves to a townrecord organization later |
| 2 | Core language | Python core with a thin Tauri shell (Section 14.2) |
| 3 | Database | SQLite (Section 14.3) |
| 4 | TownReporter | TownRecord does not touch TownReporter. TownRecord offers a public API that TownReporter or anyone else can use. Integration comes later (Section 13.5) |
| 5 | YouTube capture | Captions first as specified, plus a "Local transcription only" setting. No lawyer review; an AI legal-risk review with a "not legal advice" warning (Sections 8.10, 15.1) |
| 6 | Public-site transcripts | Excerpts by default; full transcripts can be turned on for each body (Section 15) |
| 7 | State scope | The legislature by default, plus a checklist of state boards, all off by default (Section 6.1) |
| 8 | License | Apache-2.0. TownReporter (MIT) can move to the same license later if needed |
| 9 | macOS | Windows first; macOS tested from the first build; signed macOS installer at M6 (Section 14.1) |

### 20.2 Still open

1. Which vector extension to bundle with SQLite for semantic search.
2. Which Colorado state boards go on the checklist first, after their video and records sources are checked.
3. When and how TownReporter integrates with TownRecord.

## Appendix A. Facts verified on September 27, 2026

| Fact | How it was checked |
|---|---|
| PrimeGov JSON API and document downloads work from an ordinary program; the AI web-fetch tool is blocked by robots | Direct calls from a cloud shell; web fetch refused with "disallowed by robots.txt" |
| PrimeGov document links redirect to signed storage links that expire in about two days; unsigned storage links return 404 | Direct calls |
| Longmont HTML agendas carry data-videolocation seconds for items; some values are placeholders (162:30:21) | Parsed the September 8, 2026 HTML agenda |
| The June 2, 2026 study session HTML agenda has no item times | Parsed that agenda |
| Longmont channel IDs: City UCH5_wkpLrKYb1JuUk6-UdNg, Longmont Public Media UCXFW3IRzfCc6q_XM-uutAmw | Read from the channel pages |
| The channel RSS feed works from a data center | Direct call |
| Caption downloads, the player API and the watch page are blocked from a data center | yt-dlp, player API and watch page calls from a cloud shell |
| The watch page and the "Show transcript" panel work from a home connection; the full 4 h 43 min transcript was read | The desktop app's built-in browser on the user's computer |
| The caption track's baseUrl returns an empty file even from a home connection | Fetched from inside the watch page |
| A third-party transcript site (youtubetotranscript.com) returns transcripts to an AI web-fetch tool, without timestamps | Tested on four Longmont meetings |

## Appendix B. TownReporter files referenced

All paths are in github.com/scottconverse/townreporter at version 0.6.76.

- YouTube: src/lib/news/youtube.ts, youtube-data-api.ts, youtube-data-api.server.ts, meeting-capture-ytdlp.ts, meeting-capture-caps.ts, caption-parse.ts, render-fetch.ts
- Transcripts and artifacts: meeting-transcript-artifacts.ts, meeting-revision.ts, textflowkit.ts, textflowkit-transcribe.server.ts, storage-root.server.ts
- Alignment, votes and citations: meeting-agenda-items.ts, meeting-story-section5.ts, meeting-story-section5-run.ts, meeting-publish-guard.ts
- Records: primegov.ts, body-limit.ts, render-detect.ts
- AI: provider-registry.ts, automatic-failover.ts, model-assignments.ts, provider-login.server.ts, ai-codex.server.ts, ai-claude-code.server.ts, custom-ai-connections.server.ts
- Safety: url-guard.ts, fetch-url.ts, lead-match.ts, import-stories.ts
- Jobs and schedule: jobs.ts, unattended-scheduler.ts
- Release notes: docs/releases/0.6.60.md (meeting capture), 0.6.70.md (YouTube Data API)
- Handoffs with process lessons: HANDOFF-SESSION-2026-09-02.md, HANDOFF-SESSION-2026-09-04.md
