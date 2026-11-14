"use strict";
function requiredElement(id, type) {
    const value = document.getElementById(id);
    if (!(value instanceof type))
        throw new Error(`Missing interface control: ${id}`);
    return value;
}
const elements = {
    desktop: requiredElement("desktop", HTMLElement),
    "settings-form": requiredElement("settings-form", HTMLFormElement),
    "setup-progress": requiredElement("setup-progress", HTMLElement),
    "desktop-status": requiredElement("desktop-status", HTMLElement),
    "desktop-paths": requiredElement("desktop-paths", HTMLElement),
    restart: requiredElement("restart", HTMLButtonElement),
    backup: requiredElement("backup", HTMLButtonElement),
    stop: requiredElement("stop", HTMLButtonElement),
    "restore-form": requiredElement("restore-form", HTMLFormElement),
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
    provider: requiredElement("provider", HTMLSelectElement),
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
    "association-destination": requiredElement("association-destination", HTMLSelectElement),
    "association-reason": requiredElement("association-reason", HTMLTextAreaElement),
    history: requiredElement("history", HTMLElement),
};
function formFromEvent(event) {
    const f = event?.currentTarget;
    if (!(f instanceof HTMLFormElement))
        throw new Error("Expected form submission.");
    return f;
}
function field(form, name) {
    const f = form.elements.namedItem(name);
    if (!(f instanceof HTMLInputElement ||
        f instanceof HTMLSelectElement ||
        f instanceof HTMLTextAreaElement))
        throw new Error(`Missing form field: ${name}`);
    return f;
}
function checkbox(form, name) {
    const f = field(form, name);
    if (!(f instanceof HTMLInputElement) || f.type !== "checkbox")
        throw new Error(`Missing checkbox: ${name}`);
    return f;
}
const operator = location.pathname === "/operator";
// Remove the launch credential before any request, including failed exchanges.
const launchCode = new URLSearchParams(location.hash.slice(1)).get("launch");
if (launchCode !== null)
    history.replaceState(null, "", location.pathname + location.search);
let desktopMode = false;
let desktopSettings = null;
let lifecycleBusy = false;
let providersLoaded = false;
let token = "";
let selected = null;
let listFocus = null;
let overview = null;
const $ = (id) => elements[id];
function node(tag, text, cls) {
    const n = document.createElement(tag);
    if (text != null)
        n.textContent = text;
    if (cls)
        n.className = cls;
    return n;
}
function message(text, error = false) {
    $("notice").textContent = text;
    $("notice").className = error ? "error" : "";
}
function link(text, url) {
    const a = node("a", text);
    try {
        const u = new URL(url, location.origin);
        if (!["http:", "https:"].includes(u.protocol))
            throw Error();
        a.href = u.href;
        a.rel = "noopener noreferrer";
    }
    catch {
        a.removeAttribute("href");
    }
    return a;
}
async function api(path, data, method) {
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
        let detail;
        try {
            const e = await r.json();
            if (e && typeof e === "object" && "detail" in e)
                detail = e.detail;
        }
        catch { }
        throw Error(typeof detail === "string" ? detail : `Request failed (${r.status}).`);
    }
    return r.status === 204 ? undefined : (await r.json());
}
function task(fn) {
    return async (event) => {
        event?.preventDefault();
        const target = event instanceof SubmitEvent ? event.submitter : event?.currentTarget;
        const control = target instanceof HTMLButtonElement ? target : null;
        if (control?.disabled)
            return;
        if (control)
            control.disabled = true;
        message("Working…");
        try {
            await fn(event);
            message("");
        }
        catch (err) {
            message(err instanceof Error ? err.message : "The operation failed. Try again.", true);
        }
        finally {
            if (control)
                control.disabled = false;
        }
    };
}
function button(text, fn) {
    const b = node("button", text);
    b.type = "button";
    b.addEventListener("click", task(fn));
    return b;
}
function options(select, items, label) {
    const old = select.value;
    select.replaceChildren();
    for (const item of items) {
        const o = node("option", label(item));
        o.value = String(item.id);
        select.append(o);
    }
    if (items.some((i) => String(i.id) === old))
        select.value = old;
}
function formValues(form) {
    return Object.fromEntries([...new FormData(form)].map(([key, value]) => [
        key,
        typeof value === "string" ? value : "",
    ]));
}
function tile(title, detail) {
    const a = node("article");
    a.append(node("h3", title), node("p", detail, "meta"));
    return a;
}
function showList(rows) {
    $("meetings").replaceChildren();
    if (!rows.length)
        $("meetings").append(node("p", "No meetings match these filters. Try a different date or topic."));
    for (const m of rows) {
        const a = tile(m.title || "Untitled meeting", `${m.body_name} · ${m.starts_at}${m.is_cancelled ? " · Cancelled" : ""}`);
        const openButton = button("Open meeting", async () => { listFocus = openButton; await openMeeting(m.id); });
        openButton.dataset.meetingId = String(m.id);
        a.append(openButton);
        if (operator)
            a.append(node("p", m.publication_status || "Not published"));
        $("meetings").append(a);
    }
}
async function browse() {
    const params = new URLSearchParams(formValues($("filters")));
    const data = await api((operator ? "/v1/operator" : "/public/v1") + "/meetings?" + params);
    showList(data);
}
async function refresh() {
    if (!providersLoaded) {
        const registry = await api("/v1/connectors/capabilities");
        const providers = registry.data.providers.map(p => ({ id: p.provider }));
        options($("provider"), providers, p => p.id === "primegov" ? "PrimeGov" : p.id.replaceAll("_", " "));
        providersLoaded = true;
    }
    overview = await api("/v1/operator/overview");
    $("tools").replaceChildren();
    for (const [name, version] of Object.entries(overview.tools || {})) {
        const row = node("p", `${name}: ${version || "not installed"} `);
        row.append(button("Set up / check " + name, async () => {
            await api(`/v1/operator/tools/${name}/setup`, {});
            await refresh();
        }));
        $("tools").append(row);
    }
    $("schedule").textContent = overview.schedule.time_zone
        ? `Daily retrieval: ${overview.schedule.daily_time} (${overview.schedule.time_zone}), while service is running.`
        : desktopMode ? "Daily retrieval is paused: save your timezone above." : "Daily retrieval is paused: set TOWNRECORD_TIME_ZONE before starting the service.";
    options($("jurisdictions"), overview.jurisdictions, (j) => j.name);
    options($("correction-body"), overview.bodies, (b) => b.name);
    const bf = $("body-filter");
    options(bf, [{ id: "", name: "All bodies" }, ...overview.bodies], (b) => b.name);
    $("bodies").replaceChildren(...overview.bodies.map((b) => node("p", b.name)));
    $("sources").replaceChildren();
    for (const s of overview.sources) {
        const a = tile(s.origin, `${s.status} · ${s.last_error || (s.last_checked_at ? "Last checked " + s.last_checked_at : "Not retrieved yet")}`);
        a.append(node("p", `Coverage: ${s.coverage?.date_from || "not configured"} through ${s.coverage?.date_to || "not configured"}`));
        a.append(button("Retrieve configured coverage", async () => {
            await api(`/v1/operator/sources/${s.id}/sync`, {});
            await refresh();
        }));
        $("sources").append(a);
    }
    $("jobs").replaceChildren();
    for (const j of overview.jobs) {
        const a = tile(`${j.kind} · ${j.state}`, j.last_error || `Job ${j.id}`);
        if (["failed", "paused"].includes(j.state))
            a.append(button("Retry", async () => {
                await api(`/v1/operator/jobs/${j.id}/retry`, {});
                await refresh();
            }));
        if (j.checkpoint) {
            const details = node("details");
            details.append(node("summary", "Coverage and processing result"), node("pre", JSON.stringify(j.checkpoint, null, 2)));
            a.append(details);
        }
        $("jobs").append(a);
    }
    if (desktopSettings) {
        const remaining = [desktopSettings.setup_required ? "save schedule and sample recording" : "", !overview.bodies.length ? "add a public body" : "", !overview.sources.length ? "save a source" : "", Object.values(overview.tools).some(v => !v) ? "set up processing tools" : ""].filter(Boolean);
        $("setup-progress").textContent = remaining.length ? "Setup remaining: " + remaining.join("; ") + "." : "Community setup saved. Check processing results before reviewing and publishing records.";
    }
    await browse();
}
function addSection(parent, title) {
    const s = node("section");
    s.append(node("h3", title));
    parent.append(s);
    return s;
}
async function openMeeting(id) {
    selected = id;
    const data = await api((operator ? "/v1/operator" : "/public/v1") + `/meetings/${id}`);
    const d = data.detail, m = d.meeting;
    $("detail").hidden = false;
    $("browse").hidden = true;
    $("meeting-title").textContent = m.title || "Meeting";
    const box = $("meeting-content");
    box.replaceChildren(node("p", `${d.body.name} · ${m.starts_at}${m.is_cancelled ? " · Cancelled" : ""}`));
    if (data.publication_note)
        box.append(node("p", data.publication_note));
    for (const n of d.notes || [])
        box.append(node("p", typeof n === "string" ? n : JSON.stringify(n)));
    const docs = addSection(box, "Documents");
    for (const doc of m.documents || []) {
        const row = node("p");
        row.append(link(doc.title || doc.kind, operator
            ? `/v1/records/${doc.id}/file`
            : `/public/v1/meetings/${id}/documents/${doc.id}`));
        if (operator) {
            row.replaceChildren(button(doc.title || doc.kind, () => downloadPrivate(`/v1/records/${doc.id}/file`, `${doc.kind}-${doc.id}`)));
        }
        if (doc.citation_url)
            row.append(" · ", link("Original source", doc.citation_url));
        docs.append(row);
        const extracted = (data.document_text || []).find((t) => t.record_id === doc.id);
        if (extracted?.pages.length) {
            const details = node("details");
            details.append(node("summary", "Read extracted document text"));
            for (const page of extracted.pages) {
                details.append(node("h4", `Page ${page.page_number}`), node("p", page.text));
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
            p.append(link(v.title || "Open original recording", v.url), node("span", ` \u00b7 ${v.capture_state} \u00b7 ${v.readiness}`));
        else
            p.textContent = "Recording URL unavailable";
        videos.append(p);
    }
    if (!(m.videos || []).length)
        videos.append(node("p", "No recording is currently available."));
    if (operator) {
        for (const v of m.videos || [])
            videos.append(button("Create local transcript: " + (v.title || "recording"), async () => {
                await api(`/v1/operator/videos/${v.id}/transcribe`, {
                    reason: "Operator explicitly requested a local machine-generated transcript.",
                });
                await refresh();
            }));
    }
    const agenda = addSection(box, "Agenda");
    for (const i of d.items || []) {
        const p = node("p", `${i.number} ${i.title}`);
        const video = (m.videos || []).find((v) => v.is_primary) || m.videos?.[0];
        if (i.start_ms != null && video?.url)
            p.append(" · ", link("Open at this item", timestampURL(video.url, i.start_ms)));
        agenda.append(p);
        for (const vote of i.votes || []) {
            agenda.append(node("p", `${vote.result} \u00b7 ${vote.source_label}: ${vote.evidence}`));
        }
    }
    const ts = addSection(box, "Transcript");
    const t = data.transcript;
    if (t) {
        ts.append(node("p", `Origin: ${{ publisher_captions: "Publisher-supplied captions (not certified minutes)", auto_captions: "Machine-generated platform captions", local_speech_to_text: "Machine-generated TextFlowKit transcript", sister_channel: "Captions from another recording source" }[t.origin] || t.origin}${t.is_provisional ? " · Provisional" : ""}. Check the source recording before relying on this text.`));
        const video = (m.videos || []).find((v) => v.id === t.video_id);
        for (const seg of t.segments || []) {
            const p = node("p", null, "segment");
            const sec = Math.floor(seg.start_ms / 1000), time = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
            if (video?.url)
                p.append(link(time, timestampURL(video.url, seg.start_ms)), " ");
            else
                p.append(time + " ");
            p.append((seg.speaker_label ? seg.speaker_label + ": " : "") + seg.text);
            ts.append(p);
        }
    }
    else
        ts.append(node("p", data.transcript_note || "No transcript is available."));
    $("editor").hidden = !operator;
    if (operator) {
        options($("association-item"), [
            ...m.documents.map((d) => ({
                id: "records/" + d.id,
                name: d.title || d.kind,
            })),
            ...m.videos.map((v) => ({
                id: "videos/" + v.id,
                name: v.title || "Recording",
            })),
        ], (x) => x.name);
        const destinations = await api("/v1/operator/meetings");
        options($("association-destination"), destinations, (x) => `${x.title} \u00b7 ${x.starts_at}`);
        $("association-destination").value = String(id);
        $("association-reason").value = "";
        const f = $("correction-form");
        field(f, "title").value = m.title || "";
        field(f, "starts_at").value = m.starts_at;
        field(f, "body_id").value = String(d.body.id);
        checkbox(f, "is_cancelled").checked = m.is_cancelled;
        checkbox(f, "is_continued").checked = m.is_continued;
        field(f, "reason").value = "";
        $("history").replaceChildren(...(data.history || []).map((h) => node("p", `${h.created_at || h.observed_at} · ${h.action || "Source change"} · ${h.reason || h.current_state || ""}`)));
    }
    $("meeting-title").tabIndex = -1;
    $("meeting-title").focus();
}
function timestampURL(url, ms) {
    const u = new URL(url);
    if (/(^|\.)youtube\.com$/.test(u.hostname) || u.hostname === "youtu.be")
        u.searchParams.set("t", Math.floor(ms / 1000) + "s");
    else
        u.hash = "t=" + Math.floor(ms / 1000);
    return u.href;
}
async function downloadPrivate(url, name) {
    const r = await fetch(url, {
        headers: { Authorization: "Bearer " + token },
        cache: "no-store",
    });
    if (!r.ok)
        throw Error(`Download failed (${r.status})`);
    const blob = await r.blob(), href = URL.createObjectURL(blob), a = node("a");
    a.href = href;
    const type = (r.headers.get("Content-Type") || "").split(";")[0];
    const extensions = {
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
    const target = listFocus?.isConnected ? listFocus : $("meetings").querySelector(`button[data-meeting-id="${selected}"]`);
    selected = null;
    target?.focus();
};
$("login-form").addEventListener("submit", task(async () => {
    token = $("token").value;
    $("token").value = "";
    try {
        await refresh();
        $("login").hidden = true;
        $("workspace").hidden = false;
        $("browse").hidden = false;
    }
    catch (e) {
        token = "";
        throw e;
    }
}));
$("signout").onclick = () => {
    token = "";
    selected = null;
    overview = null;
    $("workspace").hidden = true;
    $("detail").hidden = true;
    $("browse").hidden = true;
    $("login").hidden = desktopMode;
    $("desktop").hidden = true;
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
    ])
        $(id).replaceChildren();
    $("correction-form").reset();
    $("source-form").reset();
    $("body-form").reset();
    $("association-form").reset();
    $("filters").reset();
    $("publication-note").value = "";
    message(desktopMode ? "Signed out. Open TownRecord from the Start Menu to sign in again." : "Signed out.");
};
$("body-form").addEventListener("submit", task(async (e) => {
    await api("/v1/operator/bodies", formValues(formFromEvent(e)));
    await refresh();
}));
$("source-form").addEventListener("submit", task(async (e) => {
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
}));
$("refresh").onclick = task(refresh);
$("correction-form").addEventListener("submit", task(async (e) => {
    const f = formFromEvent(e), raw = formValues(f);
    const v = {
        ...raw,
        body_id: Number(raw.body_id),
        is_cancelled: checkbox(f, "is_cancelled").checked,
        is_continued: checkbox(f, "is_continued").checked,
    };
    await api(`/v1/operator/meetings/${selected}`, v, "PATCH");
    if (selected !== null)
        await openMeeting(selected);
}));
$("publish").onclick = task(async () => {
    await api(`/v1/operator/meetings/${selected}/publish`, {
        reason: $("publication-note").value,
    });
    if (selected !== null)
        await openMeeting(selected);
});
$("withdraw").onclick = task(async () => {
    await api(`/v1/operator/meetings/${selected}/withdraw`, {
        reason: $("publication-note").value,
    });
    if (selected !== null)
        await openMeeting(selected);
});
$("export").onclick = task(() => downloadPrivate(`/v1/operator/meetings/${selected}/handoff`, `townrecord-meeting-${selected}.json`));
function showDesktopSettings(settings) {
    desktopSettings = settings;
    $("desktop").hidden = false;
    const form = $("settings-form");
    field(form, "time_zone").value = settings.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    field(form, "daily_time").value = settings.daily_time || "06:00";
    field(form, "test_video_url").value = settings.test_video_url || "";
    $("desktop-paths").textContent = `Data folder: ${settings.data_path}. Backup folder: ${settings.backup_path}.`;
}
async function waitForRestart(operationId) {
    // The service is briefly unavailable. Wait for persisted settings to be active.
    for (let attempt = 0; attempt < 300; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (!token)
            throw Error("Open TownRecord from the Start Menu to reconnect.");
        try {
            const settings = await api("/v1/desktop/settings");
            if (settings.restart_required || settings.last_operation?.operation_id !== operationId)
                continue;
            showDesktopSettings(settings);
            await refresh();
            const receipt = settings.last_operation;
            if (receipt && !receipt.success)
                throw Error(`${receipt.kind} failed: ${receipt.error || "Check the application log."}`);
            $("desktop-status").textContent = receipt?.kind === "backup" ? `Backup verified. ${receipt.path || settings.backup_path}. TownRecord is running.` : "TownRecord is running. Settings are applied.";
            return;
        }
        catch (error) {
            if (error instanceof Error && error.message.includes(" failed:"))
                throw error;
        }
    }
    throw Error("TownRecord has not reconnected yet. Open it from the Start Menu, then check status. Do not repeat a backup until you have checked the backup folder.");
}
async function lifecycle(path, data = {}) {
    if (lifecycleBusy)
        return;
    lifecycleBusy = true;
    for (const id of ["restart", "backup", "stop"])
        $(id).disabled = true;
    $("desktop-status").textContent = path.endsWith("backup") ? "Stopping work to create and verify a backup, then restarting..." : "Restarting TownRecord...";
    try {
        const result = await api("/v1/desktop/" + path, data);
        if (path === "stop" || path === "restore") {
            token = "";
            $("workspace").hidden = true;
            $("browse").hidden = true;
            $("intro").textContent = path === "stop" ? "TownRecord is stopping. Open TownRecord from the Start Menu to start it again." : `Restored profile prepared at ${result.path || "a separate folder"}. TownRecord is restarting. Open TownRecord from the Start Menu to sign in and check your restored records.`;
            return;
        }
        if (!result.operation_id)
            throw Error("Restart did not return an operation receipt. Reopen TownRecord to check status.");
        await waitForRestart(result.operation_id);
    }
    catch (error) {
        $("desktop-status").textContent = error instanceof Error ? error.message : "The operation failed. Reopen TownRecord to check status.";
        throw error;
    }
    finally {
        lifecycleBusy = false;
        for (const id of ["restart", "backup", "stop"])
            $(id).disabled = false;
    }
}
$("settings-form").addEventListener("submit", task(async (e) => {
    await api("/v1/desktop/settings", formValues(formFromEvent(e)), "PUT");
    await lifecycle("restart");
}));
$("restart").onclick = task(() => lifecycle("restart"));
$("backup").onclick = task(() => lifecycle("backup"));
$("stop").onclick = task(() => lifecycle("stop"));
$("restore-form").addEventListener("submit", task(async (e) => {
    if (!confirm("Restore this backup into a separate profile and switch TownRecord to it? Keep the current profile until you verify the restored records."))
        return;
    await lifecycle("restore", formValues(formFromEvent(e)));
}));
if (operator) {
    $("heading").textContent = "Operator workspace";
    $("intro").textContent = "Review acquisitions, correct records and choose what residents can see.";
    $("browse").hidden = true;
    task(async () => {
        const info = await api("/v1/desktop/info");
        desktopMode = info.desktop;
        if (!desktopMode) {
            $("login").hidden = false;
            return;
        }
        if (!launchCode)
            throw Error("Open TownRecord from the Start Menu to sign in. Each launch link works once; reload does not retain access.");
        try {
            const result = await api("/v1/desktop/exchange", { code: launchCode });
            token = result.token;
        }
        catch {
            throw Error("This launch link expired or was already used. Open TownRecord from the Start Menu to sign in again.");
        }
        showDesktopSettings(await api("/v1/desktop/settings"));
        await refresh();
        $("workspace").hidden = false;
        $("browse").hidden = false;
    })();
}
else {
    task(async () => {
        const bodies = await api("/public/v1/bodies");
        options($("body-filter"), [{ id: "", name: "All bodies" }, ...bodies], b => b.name);
        await browse();
        const target = new URLSearchParams(location.search).get("meeting");
        if (target && /^\d+$/.test(target))
            await openMeeting(Number(target));
    })();
}
$("association-form").addEventListener("submit", task(async () => {
    await api("/v1/operator/" + $("association-item").value + "/associate", {
        meeting_id: Number($("association-destination").value),
        reason: $("association-reason").value,
    });
    if (selected !== null)
        await openMeeting(selected);
}));
