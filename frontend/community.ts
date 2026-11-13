// Maintained browser source. Build with npm run build:ui; do not edit the generated JS.
type Id = number;
interface Choice {
  id: number | string;
  name: string;
}
interface Body extends Choice {
  id: Id;
}
interface Source {
  id: Id;
  origin: string;
  status: string;
  last_error: string | null;
  last_checked_at: string | null;
  coverage?: { date_from?: string; date_to?: string };
}
interface Job {
  id: Id;
  kind: string;
  state: string;
  last_error: string | null;
  checkpoint?: unknown;
}
interface Overview {
  tools: Record<string, string | null>;
  schedule: { time_zone: string | null; daily_time: string };
  jurisdictions: Body[];
  bodies: Body[];
  sources: Source[];
  jobs: Job[];
}
interface Summary {
  id: Id;
  title: string | null;
  body_name: string;
  starts_at: string;
  is_cancelled: boolean;
  publication_status?: string;
}
interface DocumentRecord {
  id: Id;
  title: string | null;
  kind: string;
  citation_url?: string | null;
}
interface Video {
  id: Id;
  title: string | null;
  url: string | null;
  capture_state: string;
  readiness: string;
  is_primary: boolean;
}
interface Meeting {
  title: string | null;
  starts_at: string;
  is_cancelled: boolean;
  is_continued: boolean;
  documents: DocumentRecord[];
  videos: Video[];
}
interface Vote {
  result: string;
  source_label: string;
  evidence: string;
}
interface Item {
  number: string;
  title: string;
  start_ms: number | null;
  votes: Vote[];
}
interface Transcript {
  video_id: Id;
  origin: string;
  is_provisional: boolean;
  segments: { start_ms: number; speaker_label: string | null; text: string }[];
}
interface Snapshot {
  detail: { meeting: Meeting; body: Body; notes: string[]; items: Item[] };
  publication_note?: string;
  transcript: Transcript | null;
  transcript_note?: string;
  document_text: {
    record_id: Id;
    pages: { page_number: number; text: string }[];
  }[];
  history?: {
    created_at?: string;
    observed_at?: string;
    action?: string;
    reason?: string;
    current_state?: string;
  }[];
}
function requiredElement<T extends HTMLElement>(
  id: string,
  type: { new (): T },
): T {
  const value = document.getElementById(id);
  if (!(value instanceof type))
    throw new Error(`Missing interface control: ${id}`);
  return value;
}
const elements = {
  main: requiredElement("main", HTMLElement),
  heading: requiredElement("heading", HTMLElement),
  intro: requiredElement("intro", HTMLElement),
  notice: requiredElement("notice", HTMLElement),
  login: requiredElement("login", HTMLElement),
  "login-form": requiredElement("login-form", HTMLFormElement),
  token: requiredElement("token", HTMLInputElement),
  workspace: requiredElement("workspace", HTMLElement),
  signout: requiredElement("signout", HTMLButtonElement),
  "body-form": requiredElement("body-form", HTMLFormElement),
  bodies: requiredElement("bodies", HTMLElement),
  "source-form": requiredElement("source-form", HTMLFormElement),
  jurisdictions: requiredElement("jurisdictions", HTMLSelectElement),
  sources: requiredElement("sources", HTMLElement),
  tools: requiredElement("tools", HTMLElement),
  schedule: requiredElement("schedule", HTMLElement),
  refresh: requiredElement("refresh", HTMLButtonElement),
  jobs: requiredElement("jobs", HTMLElement),
  browse: requiredElement("browse", HTMLElement),
  filters: requiredElement("filters", HTMLFormElement),
  "body-filter": requiredElement("body-filter", HTMLSelectElement),
  meetings: requiredElement("meetings", HTMLElement),
  detail: requiredElement("detail", HTMLElement),
  back: requiredElement("back", HTMLButtonElement),
  "meeting-title": requiredElement("meeting-title", HTMLElement),
  "meeting-content": requiredElement("meeting-content", HTMLElement),
  editor: requiredElement("editor", HTMLElement),
  "correction-form": requiredElement("correction-form", HTMLFormElement),
  "correction-body": requiredElement("correction-body", HTMLSelectElement),
  "publication-note": requiredElement("publication-note", HTMLTextAreaElement),
  publish: requiredElement("publish", HTMLButtonElement),
  withdraw: requiredElement("withdraw", HTMLButtonElement),
  export: requiredElement("export", HTMLButtonElement),
  "association-form": requiredElement("association-form", HTMLFormElement),
  "association-item": requiredElement("association-item", HTMLSelectElement),
  "association-destination": requiredElement(
    "association-destination",
    HTMLSelectElement,
  ),
  "association-reason": requiredElement(
    "association-reason",
    HTMLTextAreaElement,
  ),
  history: requiredElement("history", HTMLElement),
};
function formFromEvent(event?: Event): HTMLFormElement {
  const f = event?.currentTarget;
  if (!(f instanceof HTMLFormElement))
    throw new Error("Expected form submission.");
  return f;
}
function field(
  form: HTMLFormElement,
  name: string,
): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
  const f = form.elements.namedItem(name);
  if (!(
    f instanceof HTMLInputElement ||
    f instanceof HTMLSelectElement ||
    f instanceof HTMLTextAreaElement
  ))
    throw new Error(`Missing form field: ${name}`);
  return f;
}
function checkbox(form: HTMLFormElement, name: string): HTMLInputElement {
  const f = field(form, name);
  if (!(f instanceof HTMLInputElement) || f.type !== "checkbox")
    throw new Error(`Missing checkbox: ${name}`);
  return f;
}

const operator = location.pathname === "/operator";

let token = "";
let selected: Id | null = null;
let listFocus: HTMLButtonElement | null = null;
let overview: Overview | null = null;

const $ = <K extends keyof typeof elements>(id: K): (typeof elements)[K] =>
  elements[id];

function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string | null,
  cls?: string,
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}

function message(text: string, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "error" : "";
}

function link(text: string, url: string) {
  const a = node("a", text);
  try {
    const u = new URL(url, location.origin);
    if (!["http:", "https:"].includes(u.protocol)) throw Error();
    a.href = u.href;
    a.rel = "noopener noreferrer";
  } catch {
    a.removeAttribute("href");
  }
  return a;
}

async function api<T = unknown>(
  path: string,
  data?: unknown,
  method?: string,
): Promise<T> {
  const r = await fetch(path, {
    method: method || (data ? "POST" : "GET"),
    headers: {
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
    cache: "no-store",
  });
  if (!r.ok) {
    let detail: unknown;
    try {
      const e: unknown = await r.json();
      if (e && typeof e === "object" && "detail" in e) detail = e.detail;
    } catch {}
    throw Error(
      typeof detail === "string" ? detail : `Request failed (${r.status}).`,
    );
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

function task(fn: (event?: Event) => Promise<unknown>) {
  return async (event?: Event) => {
    event?.preventDefault();
    const target =
      event instanceof SubmitEvent ? event.submitter : event?.currentTarget;
    const control = target instanceof HTMLButtonElement ? target : null;
    if (control?.disabled) return;
    if (control) control.disabled = true;
    message("Workingâ€¦");
    try {
      await fn(event);
      message("");
    } catch (err: unknown) {
      message(
        err instanceof Error ? err.message : "The operation failed. Try again.",
        true,
      );
    } finally {
      if (control) control.disabled = false;
    }
  };
}

function button(text: string, fn: () => Promise<unknown>) {
  const b = node("button", text);
  b.type = "button";
  b.addEventListener("click", task(fn));
  return b;
}

function options<T extends { id: string | number }>(
  select: HTMLSelectElement,
  items: T[],
  label: (item: T) => string,
) {
  const old = select.value;
  select.replaceChildren();
  for (const item of items) {
    const o = node("option", label(item));
    o.value = String(item.id);
    select.append(o);
  }
  if (items.some((i) => String(i.id) === old)) select.value = old;
}

function formValues(form: HTMLFormElement): Record<string, string> {
  return Object.fromEntries(
    [...new FormData(form)].map(([key, value]) => [
      key,
      typeof value === "string" ? value : "",
    ]),
  );
}

function tile(title: string, detail: string) {
  const a = node("article");
  a.append(node("h3", title), node("p", detail, "meta"));
  return a;
}

function showList(rows: Summary[]) {
  $("meetings").replaceChildren();
  if (!rows.length)
    $("meetings").append(
      node(
        "p",
        "No meetings match these filters. Try a different date or topic.",
      ),
    );
  for (const m of rows) {
    const a = tile(
      m.title || "Untitled meeting",
      `${m.body_name} Â· ${m.starts_at}${m.is_cancelled ? " Â· Cancelled" : ""}`,
    );
    const openButton = button("Open meeting", async () => { listFocus=openButton; await openMeeting(m.id); });
    a.append(openButton);
    if (operator) a.append(node("p", m.publication_status || "Not published"));
    $("meetings").append(a);
  }
}

async function browse() {
  const params = new URLSearchParams(formValues($("filters")));
  const data = await api<Summary[]>(
    (operator ? "/v1/operator" : "/public/v1") + "/meetings?" + params,
  );
  showList(data);
}

async function refresh() {
  overview = await api<Overview>("/v1/operator/overview");
  $("tools").replaceChildren();
  for (const [name, version] of Object.entries(overview.tools || {})) {
    const row = node("p", `${name}: ${version || "not installed"} `);
    row.append(
      button("Set up / check " + name, async () => {
        await api(`/v1/operator/tools/${name}/setup`, {});
        await refresh();
      }),
    );
    $("tools").append(row);
  }
  $("schedule").textContent = overview.schedule.time_zone
    ? `Daily retrieval: ${overview.schedule.daily_time} (${overview.schedule.time_zone}), while service is running.`
    : "Daily retrieval is paused: set TOWNRECORD_TIME_ZONE before starting the service.";
  options($("jurisdictions"), overview.jurisdictions, (j) => j.name);
  options($("correction-body"), overview.bodies, (b) => b.name);
  const bf = $("body-filter");
  options(
    bf,
    [{ id: "", name: "All bodies" }, ...overview.bodies],
    (b) => b.name,
  );
  $("bodies").replaceChildren(...overview.bodies.map((b) => node("p", b.name)));
  $("sources").replaceChildren();
  for (const s of overview.sources) {
    const a = tile(
      s.origin,
      `${s.status} Â· ${s.last_error || (s.last_checked_at ? "Last checked " + s.last_checked_at : "Not retrieved yet")}`,
    );
    a.append(
      node(
        "p",
        `Coverage: ${s.coverage?.date_from || "not configured"} through ${s.coverage?.date_to || "not configured"}`,
      ),
    );
    a.append(
      button("Retrieve configured coverage", async () => {
        await api(`/v1/operator/sources/${s.id}/sync`, {});
        await refresh();
      }),
    );
    $("sources").append(a);
  }
  $("jobs").replaceChildren();
  for (const j of overview.jobs) {
    const a = tile(`${j.kind} Â· ${j.state}`, j.last_error || `Job ${j.id}`);
    if (["failed", "paused"].includes(j.state))
      a.append(
        button("Retry", async () => {
          await api(`/v1/operator/jobs/${j.id}/retry`, {});
          await refresh();
        }),
      );
    if (j.checkpoint) {
      const details = node("details");
      details.append(
        node("summary", "Coverage and processing result"),
        node("pre", JSON.stringify(j.checkpoint, null, 2)),
      );
      a.append(details);
    }
    $("jobs").append(a);
  }
  await browse();
}

function addSection(parent: HTMLElement, title: string) {
  const s = node("section");
  s.append(node("h3", title));
  parent.append(s);
  return s;
}

async function openMeeting(id: Id) {
  selected = id;
  const data = await api<Snapshot>(
    (operator ? "/v1/operator" : "/public/v1") + `/meetings/${id}`,
  );
  const d = data.detail,
    m = d.meeting;
  $("detail").hidden = false;
  $("browse").hidden = true;
  $("meeting-title").textContent = m.title || "Meeting";
  const box = $("meeting-content");
  box.replaceChildren(
    node(
      "p",
      `${d.body.name} Â· ${m.starts_at}${m.is_cancelled ? " Â· Cancelled" : ""}`,
    ),
  );
  if (data.publication_note) box.append(node("p", data.publication_note));
  for (const n of d.notes || [])
    box.append(node("p", typeof n === "string" ? n : JSON.stringify(n)));
  const docs = addSection(box, "Documents");
  for (const doc of m.documents || []) {
    const row = node("p");
    row.append(
      link(
        doc.title || doc.kind,
        operator
          ? `/v1/records/${doc.id}/file`
          : `/public/v1/meetings/${id}/documents/${doc.id}`,
      ),
    );
    if (operator) {
      row.replaceChildren(
        button(doc.title || doc.kind, () =>
          downloadPrivate(
            `/v1/records/${doc.id}/file`,
            `${doc.kind}-${doc.id}`,
          ),
        ),
      );
    }
    if (doc.citation_url)
      row.append(" Â· ", link("Original source", doc.citation_url));
    docs.append(row);
    const extracted = (data.document_text || []).find(
      (t) => t.record_id === doc.id,
    );
    if (extracted?.pages.length) {
      const details = node("details");
      details.append(node("summary", "Read extracted document text"));
      for (const page of extracted.pages) {
        details.append(
          node("h4", `Page ${page.page_number}`),
          node("p", page.text),
        );
      }
      docs.append(details);
    }
  }
  if (!(m.documents || []).length)
    docs.append(node("p", "No documents are currently available."));
  const videos = addSection(box, "Recordings");
  for (const v of m.videos || []) {
    const p = node("p");
    if (v.url)
      p.append(
        link(v.title || "Open original recording", v.url),
        node("span", ` \u00b7 ${v.capture_state} \u00b7 ${v.readiness}`),
      );
    else p.textContent = "Recording URL unavailable";
    videos.append(p);
  }
  if (!(m.videos || []).length)
    videos.append(node("p", "No recording is currently available."));
  if (operator) {
    for (const v of m.videos || [])
      videos.append(
        button(
          "Create local transcript: " + (v.title || "recording"),
          async () => {
            await api(`/v1/operator/videos/${v.id}/transcribe`, {
              reason:
                "Operator explicitly requested a local machine-generated transcript.",
            });
            await refresh();
          },
        ),
      );
  }
  const agenda = addSection(box, "Agenda");
  for (const i of d.items || []) {
    const p = node("p", `${i.number} ${i.title}`);
    const video = (m.videos || []).find((v) => v.is_primary) || m.videos?.[0];
    if (i.start_ms != null && video?.url)
      p.append(
        " Â· ",
        link("Open at this item", timestampURL(video.url, i.start_ms)),
      );
    agenda.append(p);
    for (const vote of i.votes || []) {
      agenda.append(
        node(
          "p",
          `${vote.result} \u00b7 ${vote.source_label}: ${vote.evidence}`,
        ),
      );
    }
  }
  const ts = addSection(box, "Transcript");
  const t = data.transcript;
  if (t) {
    ts.append(
      node(
        "p",
        `Origin: ${(<Record<string, string>>{ publisher_captions: "Publisher-supplied captions (not certified minutes)", auto_captions: "Machine-generated platform captions", local_speech_to_text: "Machine-generated TextFlowKit transcript", sister_channel: "Captions from another recording source" })[t.origin] || t.origin}${t.is_provisional ? " Â· Provisional" : ""}. Check the source recording before relying on this text.`,
      ),
    );
    const video = (m.videos || []).find((v) => v.id === t.video_id);
    for (const seg of t.segments || []) {
      const p = node("p", null, "segment");
      const sec = Math.floor(seg.start_ms / 1000),
        time = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
      if (video?.url)
        p.append(link(time, timestampURL(video.url, seg.start_ms)), " ");
      else p.append(time + " ");
      p.append((seg.speaker_label ? seg.speaker_label + ": " : "") + seg.text);
      ts.append(p);
    }
  } else
    ts.append(node("p", data.transcript_note || "No transcript is available."));
  $("editor").hidden = !operator;
  if (operator) {
    options(
      $("association-item"),
      [
        ...m.documents.map((d) => ({
          id: "records/" + d.id,
          name: d.title || d.kind,
        })),
        ...m.videos.map((v) => ({
          id: "videos/" + v.id,
          name: v.title || "Recording",
        })),
      ],
      (x) => x.name,
    );
    const destinations = await api<Summary[]>("/v1/operator/meetings");
    options(
      $("association-destination"),
      destinations,
      (x) => `${x.title} \u00b7 ${x.starts_at}`,
    );
    $("association-destination").value = String(id);
    $("association-reason").value = "";
    const f = $("correction-form");
    field(f, "title").value = m.title || "";
    field(f, "starts_at").value = m.starts_at;
    field(f, "body_id").value = String(d.body.id);
    checkbox(f, "is_cancelled").checked = m.is_cancelled;
    checkbox(f, "is_continued").checked = m.is_continued;
    field(f, "reason").value = "";
    $("history").replaceChildren(
      ...(data.history || []).map((h) =>
        node(
          "p",
          `${h.created_at || h.observed_at} Â· ${h.action || "Source change"} Â· ${h.reason || h.current_state || ""}`,
        ),
      ),
    );
  }
  $("meeting-title").tabIndex = -1;
  $("meeting-title").focus();
}

function timestampURL(url: string, ms: number) {
  const u = new URL(url);
  if (/(^|\.)youtube\.com$/.test(u.hostname) || u.hostname === "youtu.be")
    u.searchParams.set("t", Math.floor(ms / 1000) + "s");
  else u.hash = "t=" + Math.floor(ms / 1000);
  return u.href;
}

async function downloadPrivate(url: string, name: string) {
  const r = await fetch(url, {
    headers: { Authorization: "Bearer " + token },
    cache: "no-store",
  });
  if (!r.ok) throw Error(`Download failed (${r.status})`);
  const blob = await r.blob(),
    href = URL.createObjectURL(blob),
    a = node("a");
  a.href = href;
  const type = (r.headers.get("Content-Type") || "").split(";")[0];
  const extensions: Record<string, string> = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/json": ".json",
  };
  const ext = extensions[type] || "";
  a.download = name.includes(".") ? name : name + ext;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

$("filters").addEventListener("submit", task(browse));
$("back").onclick = () => {
  $("detail").hidden = true;
  $("browse").hidden = false;
  selected = null;
  if(listFocus?.isConnected)listFocus.focus();
};

$("login-form").addEventListener(
  "submit",
  task(async () => {
    token = $("token").value;
    $("token").value = "";
    try {
      await refresh();
      $("login").hidden = true;
      $("workspace").hidden = false;
      $("browse").hidden = false;
    } catch (e) {
      token = "";
      throw e;
    }
  }),
);

$("signout").onclick = () => {
  token = "";
  selected = null;
  overview = null;
  $("workspace").hidden = true;
  $("detail").hidden = true;
  $("browse").hidden = true;
  $("login").hidden = false;
  for (const id of [
    "meetings",
    "meeting-content",
    "history",
    "sources",
    "jobs",
    "tools",
    "bodies",
    "jurisdictions",
    "correction-body",
    "association-item",
    "association-destination",
    "body-filter",
  ] as const)
    $(id).replaceChildren();
  $("correction-form").reset();
  $("source-form").reset();
  $("body-form").reset();
  $("association-form").reset();
  $("filters").reset();
  $("publication-note").value = "";
  message("Signed out.");
};

$("body-form").addEventListener(
  "submit",
  task(async (e) => {
    await api("/v1/operator/bodies", formValues(formFromEvent(e)));
    await refresh();
  }),
);

$("source-form").addEventListener(
  "submit",
  task(async (e) => {
    const raw = formValues(formFromEvent(e));
    const v = {
      ...raw,
      jurisdiction_id: Number(raw.jurisdiction_id),
      download_origins: (raw.download_origins || "")
        .split(/\s+/)
        .filter(Boolean),
    };
    await api("/v1/operator/sources", v);
    await refresh();
  }),
);

$("refresh").onclick = task(refresh);

$("correction-form").addEventListener(
  "submit",
  task(async (e) => {
    const f = formFromEvent(e),
      raw = formValues(f);
    const v = {
      ...raw,
      body_id: Number(raw.body_id),
      is_cancelled: checkbox(f, "is_cancelled").checked,
      is_continued: checkbox(f, "is_continued").checked,
    };
    await api(`/v1/operator/meetings/${selected}`, v, "PATCH");
    if (selected !== null) await openMeeting(selected);
  }),
);

$("publish").onclick = task(async () => {
  await api(`/v1/operator/meetings/${selected}/publish`, {
    reason: $("publication-note").value,
  });
  if (selected !== null) await openMeeting(selected);
});

$("withdraw").onclick = task(async () => {
  await api(`/v1/operator/meetings/${selected}/withdraw`, {
    reason: $("publication-note").value,
  });
  if (selected !== null) await openMeeting(selected);
});

$("export").onclick = task(() =>
  downloadPrivate(
    `/v1/operator/meetings/${selected}/handoff`,
    `townrecord-meeting-${selected}.json`,
  ),
);

if (operator) {
  $("heading").textContent = "Operator workspace";
  $("intro").textContent =
    "Review acquisitions, correct records and choose what residents can see.";
  $("login").hidden = false;
  $("browse").hidden = true;
} else {
  task(async () => {
    const bodies = await api<Body[]>("/public/v1/bodies");
    options(
      $("body-filter"),
      [{ id: "", name: "All bodies" }, ...bodies],
      (b) => b.name,
    );
    await browse();
    const target = new URLSearchParams(location.search).get("meeting");
    if (target && /^\d+$/.test(target)) await openMeeting(Number(target));
  })();
}

$("association-form").addEventListener(
  "submit",
  task(async () => {
    await api("/v1/operator/" + $("association-item").value + "/associate", {
      meeting_id: Number($("association-destination").value),
      reason: $("association-reason").value,
    });
    if (selected !== null) await openMeeting(selected);
  }),
);
