"use strict";

// --- Zustand ----------------------------------------------------------------
let inventory = [];
let picked = new Set();          // VMIDs der Mehrfachauswahl
let streaming = false;
let lastPickedKey = null;      // verhindert unnoetigen Neuaufbau der Pruefungszeilen

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

async function api(path, body) {
  const opt = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json();
  if (r.status === 401 && data && data.login) { location.href = "/login"; throw new Error("nicht angemeldet"); }
  if (data && data.error) throw new Error(data.error);
  return data;
}

// --- Schnellwahl fuer haeufige Grunddienste ---------------------------------
// Genau der Fall "pruef mir einfach 80 und 443": ein Klick haengt die passende
// Pruefung an, ohne dass jemand JSON tippen muss.
const QUICK = [
  { label: "HTTP 80", spec: { type: "port", name: "HTTP 80 lauscht", port: 80 } },
  { label: "HTTPS 443", spec: { type: "port", name: "HTTPS 443 lauscht", port: 443 } },
  { label: "HTTP 80 von außen", spec: { type: "http", name: "HTTP 80 von außen", url: "http://localhost/", external: true, expect_status: 200 } },
  { label: "HTTPS 443 von außen", spec: { type: "http", name: "HTTPS 443 von außen", url: "https://localhost/", external: true, expect_status: 200 } },
  { label: "TLS-Zertifikat", spec: { type: "tls", name: "Zertifikat gültig", port: 443, min_days: 7 } },
  { label: "SSH 22", spec: { type: "port", name: "SSH 22 lauscht", port: 22 } },
  { label: "DNS 53", spec: { type: "port", name: "DNS 53 lauscht", port: 53 } },
  { label: "Alle Dienste ok", spec: { type: "command", name: "Keine fehlgeschlagenen Dienste", run: "systemctl is-system-running || true", expect_output: "running|degraded" } },
];

function buildChips(containerId, textareaId) {
  const box = $(containerId);
  box.innerHTML = "";
  QUICK.forEach((q) => {
    const b = document.createElement("button");
    b.textContent = "+ " + q.label;
    b.onclick = () => addCheck(textareaId, q.spec);
    box.appendChild(b);
  });
  const clear = document.createElement("button");
  clear.textContent = "leeren";
  clear.className = "danger";
  clear.onclick = () => {
    if (textareaId === "checks") { checkList = []; renderCheckRows(); return; }
    $(textareaId).value = "[]";
  };
  box.appendChild(clear);
}

function addCheck(textareaId, spec) {
  // Die Schnellwahl im Lauf-Formular schreibt in den Zeileneditor, die im
  // Zeitplan weiterhin in dessen JSON-Feld.
  if (textareaId === "checks") {
    if (!checkList.some((c) => JSON.stringify(c) === JSON.stringify(spec))) checkList.push({...spec});
    renderCheckRows();
    return;
  }
  let list;
  try { list = JSON.parse($(textareaId).value || "[]"); } catch { list = []; }
  if (!Array.isArray(list)) list = [];
  if (!list.some((c) => JSON.stringify(c) === JSON.stringify(spec))) list.push(spec);
  $(textareaId).value = JSON.stringify(list, null, 2);
}

// --- Vorschlaege je nach Anwendung ------------------------------------------
function suggestChecks(g) {
  const name = (g.name || "").toLowerCase().replace(/[^a-z]/g, "");
  const known = {
    gitea: [{ type: "port", name: "Gitea lauscht", port: 3000 },
            { type: "http", name: "Weboberfläche antwortet", url: "http://localhost:3000/" }],
    vikunja: [{ type: "service", name: "Dienst läuft", unit: "vikunja" },
              { type: "port", name: "API lauscht", port: 3456 }],
    nginxproxymanager: [{ type: "port", name: "HTTP 80", port: 80 },
                        { type: "port", name: "HTTPS 443", port: 443 },
                        { type: "port", name: "Oberfläche 81", port: 81 }],
    vaultwarden: [{ type: "service", name: "Dienst läuft", unit: "vaultwarden" }],
    pihole: [{ type: "service", name: "DNS läuft", unit: "pihole-FTL" },
             { type: "port", name: "DNS 53", port: 53 }],
    paperlessngx: [{ type: "port", name: "Port 8000", port: 8000 }],
    nextcloudvm: [{ type: "port", name: "HTTP 80", port: 80 }],
    databasement: [{ type: "postgres", name: "Datenbank antwortet", query: "SELECT 1", expect: "1" }],
    collabora: [{ type: "port", name: "Port 9980", port: 9980 }],
    docmost: [{ type: "port", name: "Port 3000", port: 3000 }],
    jellyfin: [{ type: "port", name: "Port 8096", port: 8096 }],
    homeassistant: [{ type: "port", name: "Port 8123", port: 8123 }],
  };
  for (const key of Object.keys(known)) if (name.includes(key)) return known[key];
  return [{ type: "command", name: "Keine fehlgeschlagenen Dienste",
            run: "systemctl is-system-running || true", expect_output: "running|degraded" }];
}

// --- Bestandsliste ----------------------------------------------------------
function verdictPill(v) {
  if (!v) return '<span class="pill none">nie geprüft</span>';
  if (v === "VERIFIZIERT") return '<span class="pill ok">verifiziert</span>';
  if (v === "DURCHGEFALLEN") return '<span class="pill fail">durchgefallen</span>';
  return '<span class="pill warn">abgebrochen</span>';
}

function renderInventory() {
  const rows = inventory.filter((g) => !$("only-backup").checked || g.has_backup);
  $("inv").innerHTML = rows.length === 0
    ? '<tr><td colspan="7" class="muted">keine Einträge</td></tr>'
    : rows.map((g) => `
      <tr>
        <td>${g.has_backup ? `<input type="checkbox" data-pick="${g.vmid}"
              ${picked.has(g.vmid) ? "checked" : ""}>` : ""}</td>
        <td class="mono">${g.vmid}</td>
        <td><span class="namelink" data-open="${g.vmid}">${esc(g.name)}</span>${g.live_scratch
          ? ` <span class="pill live">Testgast ${g.live_scratch} läuft</span>` : ""}</td>
        <td><span class="pill ${g.kind}">${g.kind.toUpperCase()}</span></td>
        <td class="mono muted">${g.latest_ts
          ? esc(g.latest_ts.replace("T", " ").replace("Z", "")) : "—"}</td>
        <td class="mono muted">${g.size ? (g.size / 1e9).toFixed(1) + " GB" : "—"}</td>
        <td>${verdictPill(g.last_verdict)}</td>
      </tr>`).join("");

  $("inv").querySelectorAll("[data-open]").forEach((el) => {
    el.onclick = () => openDetail(Number(el.dataset.open));
  });
  $("inv").querySelectorAll("[data-pick]").forEach((cb) => {
    cb.onchange = () => {
      const id = Number(cb.dataset.pick);
      cb.checked ? picked.add(id) : picked.delete(id);
      onSelectionChanged();
    };
  });
  onSelectionChanged();
}

function onSelectionChanged() {
  const n = picked.size;
  $("sel-count").textContent = `${n} ausgewählt`;
  $("btn-run").disabled = n === 0;
  $("btn-mksched").disabled = n === 0;

  const list = [...picked].map((id) => inventory.find((g) => g.vmid === id)).filter(Boolean);
  $("sel-label").textContent = n === 0 ? "nichts gewählt"
    : n === 1 ? `${list[0].kind}/${list[0].vmid} ${list[0].name}`
    : `${n} Gäste — laufen nacheinander`;

  // Einzelauswahl: Backup-Stände und Vorschläge laden. Mehrfachauswahl: nicht sinnvoll.
  const single = n === 1;
  $("snapshot").disabled = !single;
  $("snap-hint").textContent = single
    ? "Leer lassen heißt: das neueste Backup."
    : "Bei Mehrfachauswahl wird je Gast automatisch das neueste Backup genommen.";
  // Die Zeilen nur bei echter Auswahlaenderung neu bauen. Die Oberflaeche
  // aktualisiert sich alle 15 Sekunden selbst - ein Neuaufbau bei jedem
  // Durchlauf wuerde Probelauf-Ergebnisse loeschen und den Cursor aus dem Feld
  // reissen, in das man gerade tippt.
  const key = [...picked].sort((a, b) => a - b).join(",");
  const changed = key !== lastPickedKey;
  lastPickedKey = key;

  if (single) {
    if (changed) {
      loadSnapshots(list[0].vmid);
      // Vorschlaege nur, solange nichts Eigenes drinsteht.
      if (checkList.length === 0) checkList = suggestChecks(list[0]).map((c) => ({ ...c }));
      renderCheckRows();
    } else {
      updateLiveHint();
    }
  } else {
    if (changed) $("snapshot").innerHTML = '<option value="">neuestes verwenden</option>';
    updateLiveHint();
  }

  // Eine feste IP laesst sich nicht auf mehrere Gaeste aufteilen.
  const routedOpt = $("mode").querySelector('option[value="routed"]');
  routedOpt.disabled = false;
  routedOpt.textContent = n > 1
    ? "geroutet — braucht einen Adressbereich"
    : "geroutet — echte IP im LAN";
}

async function loadSnapshots(vmid) {
  const storage = $("src-storage").value;
  const node = $("node").value;
  const snaps = await api(`/api/snapshots?vmid=${vmid}&storage=${encodeURIComponent(storage)}` +
                          (node ? `&node=${encodeURIComponent(node)}` : ""));
  $("snapshot").innerHTML = '<option value="">neuestes verwenden</option>' +
    snaps.map((s) => `<option value="${esc(s.volid)}">${
      esc(s.ts.replace("T", " ").replace("Z", ""))} (${(s.size / 1e9).toFixed(1)} GB)${
      s.pbs ? "" : " · vzdump"}</option>`).join("");
}

// --- Protokoll --------------------------------------------------------------
function logLine(text) {
  const pre = $("log");
  if (pre.querySelector(".dim") && pre.children.length === 1) pre.innerHTML = "";
  let cls = "";
  if (text.startsWith("===")) cls = "head";
  else if (/\[BESTANDEN\]|: ok \(/.test(text)) cls = "ok";
  else if (/\[FEHLGESCHLAGEN\]|FEHLER|!!|DURCHGEFALLEN|ABGEBROCHEN/.test(text)) cls = "fail";
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = text + "\n";
  pre.appendChild(span);
  pre.scrollTop = pre.scrollHeight;
}

function openStream() {
  if (streaming) return;
  streaming = true;
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => {
    const line = JSON.parse(ev.data).line;
    if (line.startsWith("@@DONE@@")) {
      const rep = JSON.parse(line.slice(8));
      const ok = rep.verdict === "VERIFIZIERT";
      let extra = "";
      if (rep.kept) {
        extra = rep.expires_at
          ? `\nTestgast ${rep.kind}/${rep.scratch_vmid} bleibt bis ` +
            `${String(rep.expires_at).replace("T", " ").slice(0, 16)} stehen.`
          : `\nTestgast ${rep.kind}/${rep.scratch_vmid} bleibt stehen, bis du ihn entfernst.`;
      }
      $("run-out").innerHTML = `<div class="${ok ? "okbox" : "failbox"}">Ergebnis: ${
        esc(rep.verdict)}${esc(extra)}</div>`;
      refresh();
      return;
    }
    logLine(line);
  };
  es.onerror = () => { es.close(); streaming = false; };
}

// --- Formular -> Auftrag ----------------------------------------------------
function readChecks(id) {
  // Das Lauf-Formular fuehrt die Pruefungen im Zeileneditor, der Zeitplan im
  // JSON-Feld. Beides landet hier als Liste.
  if (id === "checks") return checkList;
  try {
    const v = JSON.parse($(id).value || "[]");
    if (!Array.isArray(v)) throw new Error("es muss eine Liste sein");
    return v;
  } catch (e) { throw new Error("Prüfungen sind kein gültiges JSON: " + e.message); }
}

function buildTargets() {
  const checks = readChecks("checks");
  const keep = $("keep").value;
  const base = {
    mode: $("mode").value,
    checks,
    keep,
    ttl_minutes: keep === "ttl" ? Number($("ttl").value) : null,
    node: $("node").value || null,
    backup_storage: $("src-storage").value || null,
    target_storage: $("dst-storage").value || null,
  };
  if (keep === "ttl" && !(base.ttl_minutes > 0))
    throw new Error("Für ein Zeitfenster braucht es eine Minutenzahl größer null");

  const ids = [...picked];
  if (base.mode === "routed") {
    const eintrag = ipPool.find((e) => e.id === Number($("ip-pick").value));
    if (ids.length > 1 && !(eintrag && eintrag.ist_bereich)) {
      throw new Error("Mehrere Gäste geroutet prüfen geht nur mit einem Adressbereich. "
                    + "Einzelne Adresse gewählt.");
    }
    if (eintrag && eintrag.ist_bereich && ids.length > eintrag.anzahl) {
      throw new Error(`Der Bereich hat ${eintrag.anzahl} Adressen, ausgewählt sind ${ids.length} Gäste.`);
    }
    base.ip_pool_id = Number($("ip-pick").value) || null;
    base.gateway = $("gw").value.trim() || null;
    if (!base.ip_pool_id) throw new Error(
      "Der Modus geroutet verlangt eine hinterlegte Adresse. Unter Einstellungen anlegen.");
  }
  return ids.map((vmid) => {
    const ziel = { ...base, vmid };
    if (ids.length === 1 && $("snapshot").value) ziel.snapshot = $("snapshot").value;
    return ziel;
  });
}

// --- Laufende Testgaeste ----------------------------------------------------
function renderLeases(leases) {
  $("b-leases").textContent = leases.length;
  $("leases").innerHTML = leases.length === 0
    ? '<tr><td colspan="7" class="muted">kein Testgast läuft gerade</td></tr>'
    : leases.map((l) => `<tr>
        <td><span class="pill ${l.kind}">${l.kind.toUpperCase()}/${l.scratch_vmid}</span></td>
        <td>${esc(l.source_name || "")} <span class="mono muted">(${l.kind}/${l.source_vmid})</span></td>
        <td class="muted small">${esc(l.mode || "")}</td>
        <td class="mono">${l.ip ? esc(String(l.ip).split("/")[0]) : "—"}</td>
        <td class="small">${l.keep === "manual" ? "bis Entfernen" : "Zeitfenster"}</td>
        <td class="mono muted small">${l.expires_at
          ? esc(String(l.expires_at).replace("T", " ").slice(0, 16)) : "—"}</td>
        <td style="text-align:right">
          <button class="sm" data-ext="${l.scratch_vmid}">+60 min</button>
          <button class="sm danger" data-del="${l.scratch_vmid}">Entfernen</button>
        </td></tr>`).join("");

  $("leases").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm(T`Testgast ${b.dataset.del} endgültig entfernen?`)) return;
      await api("/api/leases/remove", { scratch_vmid: Number(b.dataset.del) });
      refresh();
    };
  });
  $("leases").querySelectorAll("[data-ext]").forEach((b) => {
    b.onclick = async () => {
      await api("/api/leases/extend", { scratch_vmid: Number(b.dataset.ext), minutes: 60 });
      refresh();
    };
  });
}


// --- Zeitplaene -------------------------------------------------------------
// Links die angelegten Zeitplaene, rechts das vollstaendige Formular. Jeder
// Zeitplan laesst sich nachtraeglich in allen Punkten aendern.

let schedules = [];
let sGewaehlt = null;      // Kennung des offenen Zeitplans, null = neuer
let sLadevorgang = 0;     // zaehlt Ladevorgaenge, damit spaete Antworten nichts ueberschreiben

function renderSchedules(list) {
  schedules = list;
  $("b-sched").textContent = list.length;

  $("sched-rail").innerHTML = list.length === 0
    ? '<div class="hintline" style="padding:6px 12px">noch keiner angelegt</div>'
    : list.map((s) => `<button data-sched="${s.id}" class="${
        sGewaehlt === s.id ? "on" : ""}">${esc(s.name)}
        <span class="tick">${s.enabled ? (s.vmids || []).length : "aus"}</span></button>`).join("");

  $("sched-rail").querySelectorAll("[data-sched]").forEach((b) => {
    b.onclick = () => zeitplanOeffnen(Number(b.dataset.sched));
  });

  // Ist der offene Zeitplan verschwunden, zurück auf "neu".
  if (sGewaehlt !== null && !list.some((s) => s.id === sGewaehlt)) zeitplanNeu();
}

function wochentageSetzen(tage) {
  $("s-wd").querySelectorAll("[data-wd]").forEach((cb) => {
    cb.checked = tage.includes(Number(cb.dataset.wd));
    cb.closest("label").classList.toggle("on", cb.checked);
  });
}

function wochentageLesen() {
  return [...$("s-wd").querySelectorAll("[data-wd]")]
    .filter((cb) => cb.checked).map((cb) => Number(cb.dataset.wd));
}

function ipAuswahlFuellen(gewaehlt) {
  $("s-ip").innerHTML = ipPool.length === 0
    ? '<option value="">keine Adresse hinterlegt</option>'
    : ipPool.map((e) => `<option value="${e.id}"${e.id === gewaehlt ? " selected" : ""}>${
        esc(e.label)} — ${esc(e.anzeige)}${
        e.ist_bereich ? ` (${e.anzahl} Adressen)` : ""}</option>`).join("");
}

function zeitplanNeu() {
  sGewaehlt = null;
  sLadevorgang++;   // bricht ein laufendes Oeffnen ab
  $("s-titel").firstChild.textContent = "Neuer Zeitplan ";
  $("s-zustand").innerHTML = "";
  $("s-zuletzt").textContent = "";
  $("s-name").value = "";
  $("s-time").value = "03:00";
  $("s-keep").value = "destroy";
  $("s-keep").onchange();
  $("s-ttl").value = 60;
  $("s-mode").value = "isolated";
  $("s-mode").onchange();
  wochentageSetzen([0, 1, 2, 3, 4]);
  [...$("s-vmids").options].forEach((o) => { o.selected = false; });
  $("s-checks").value = "[]";
  ipAuswahlFuellen(null);
  ["btn-runsched", "btn-togglesched", "btn-delsched"].forEach((id) => {
    $(id).style.display = "none";
  });
  $("btn-savesched").textContent = "Zeitplan anlegen";
  $("s-verlauf-karte").style.display = "none";
  $("sched-out").innerHTML = "";
  renderSchedules(schedules);
}

async function zeitplanOeffnen(id) {
  const s = schedules.find((x) => x.id === id);
  if (!s) return;
  sGewaehlt = id;
  // Wer waehrend eines laufenden Ladevorgangs weiterklickt, soll nicht
  // ploetzlich wieder den alten Zeitplan vor sich haben.
  const lauf = ++sLadevorgang;

  $("s-titel").firstChild.textContent = s.name + " ";
  $("s-zustand").innerHTML = s.enabled
    ? '<span class="pill ok">aktiv</span>' : '<span class="pill warn">deaktiviert</span>';
  $("s-name").value = s.name || "";
  $("s-time").value = s.at_time || "03:00";
  $("s-keep").value = s.keep || "destroy";
  $("s-keep").onchange();
  $("s-ttl").value = s.ttl_minutes || 60;
  $("s-mode").value = s.mode === "routed" ? "routed" : "isolated";
  $("s-mode").onchange();
  wochentageSetzen(s.weekdays || []);
  ipAuswahlFuellen(s.ip_pool_id || null);
  [...$("s-vmids").options].forEach((o) => {
    o.selected = (s.vmids || []).includes(Number(o.value));
  });
  $("s-checks").value = JSON.stringify(s.checks || [], null, 2);
  if (lauf !== sLadevorgang) return;

  ["btn-runsched", "btn-togglesched", "btn-delsched"].forEach((x) => {
    $(x).style.display = "";
  });
  $("btn-togglesched").textContent = s.enabled ? "Deaktivieren" : "Aktivieren";
  $("btn-savesched").textContent = "Änderungen speichern";
  $("sched-out").innerHTML = "";
  renderSchedules(schedules);
  await zeitplanVerlauf(id);
}

async function zeitplanVerlauf(id) {
  try {
    const jobs = await api(`/api/jobs?schedule_id=${id}&limit=25`);
    $("s-verlauf-karte").style.display = "";
    $("s-verlauf").innerHTML = jobs.length === 0
      ? '<tr><td colspan="5" class="muted">dieser Zeitplan lief noch nie</td></tr>'
      : jobs.map((j) => `<tr>
          <td class="mono muted small">${esc((j.started || "").replace("T", " ").slice(0, 19))}</td>
          <td class="mono">${esc(j.kind || "")}/${j.vmid}</td>
          <td>${verdictPill(j.verdict)}</td>
          <td class="mono muted small">${j.duration ? Math.round(j.duration) + " s" : ""}</td>
          <td class="small muted">${esc((j.snapshot || "").split("/").pop())}</td>
        </tr>`).join("");
    const letzter = jobs[0];
    $("s-zuletzt").textContent = letzter
      ? `zuletzt ${String(letzter.started).replace("T", " ").slice(0, 16)}`
      : "noch nie gelaufen";
  } catch {
    $("s-verlauf-karte").style.display = "none";
  }
}

function zeitplanAusFormular() {
  const vmids = [...$("s-vmids").selectedOptions].map((o) => Number(o.value));
  if (vmids.length === 0) throw new Error("Bitte mindestens einen Gast auswählen.");
  const weekdays = wochentageLesen();
  if (weekdays.length === 0) throw new Error("Bitte mindestens einen Wochentag auswählen.");

  const keep = $("s-keep").value;
  const modus = $("s-mode").value;
  const eintragId = Number($("s-ip").value) || null;
  if (modus === "routed") {
    if (!eintragId) throw new Error(
      "Der Modus geroutet verlangt einen Adress-Eintrag. Unter Einstellungen anlegen.");
    const e = ipPool.find((x) => x.id === eintragId);
    if (e && !e.ist_bereich && vmids.length > 1) throw new Error(
      "Mehrere Gäste geroutet prüfen geht nur mit einem Adressbereich.");
    if (e && e.ist_bereich && vmids.length > e.anzahl) throw new Error(
      `Der Bereich hat ${e.anzahl} Adressen, ausgewählt sind ${vmids.length} Gäste.`);
  }

  return {
    id: sGewaehlt || undefined,
    name: $("s-name").value.trim() || "Zeitplan",
    enabled: sGewaehlt === null
      ? true
      : (schedules.find((x) => x.id === sGewaehlt) || {}).enabled !== false,
    vmids, weekdays,
    at_time: $("s-time").value || "03:00",
    mode: modus,
    ip_pool_id: modus === "routed" ? eintragId : null,
    checks: readChecks("s-checks"),
    keep, ttl_minutes: keep === "ttl" ? Number($("s-ttl").value) : null,
    node: $("node") ? $("node").value || null : null,
    backup_storage: $("src-storage") ? $("src-storage").value || null : null,
    target_storage: $("dst-storage") ? $("dst-storage").value || null : null,
  };
}

$("s-keep").onchange = () => {
  $("s-ttl-field").style.display = $("s-keep").value === "ttl" ? "" : "none";
};
$("s-mode").onchange = () => {
  $("s-ip-wrap").style.display = $("s-mode").value === "routed" ? "" : "none";
};

$("btn-newsched").onclick = zeitplanNeu;

$("btn-savesched").onclick = async () => {
  try {
    const gespeichert = await api("/api/schedules", zeitplanAusFormular());
    sGewaehlt = gespeichert.id;
    $("sched-out").innerHTML = '<div class="okbox">Gespeichert.</div>';
    await refresh();
    await zeitplanOeffnen(gespeichert.id);
  } catch (e) {
    $("sched-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};


$("btn-togglesched").onclick = async () => {
  const s = schedules.find((x) => x.id === sGewaehlt);
  if (!s) return;
  try {
    await api("/api/schedules", { ...zeitplanAusFormular(), enabled: !s.enabled });
    await refresh();
    await zeitplanOeffnen(sGewaehlt);
  } catch (e) {
    $("sched-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};

$("btn-runsched").onclick = async () => {
  try {
    const r = await api("/api/schedules/run", { id: sGewaehlt });
    $("sched-out").innerHTML =
      `<div class="okbox">${r.count} Auftrag/Aufträge eingereiht. Der Fortschritt steht
       unter „Prüfen“ im Live-Protokoll.</div>`;
    openStream();
    refresh();
  } catch (e) {
    $("sched-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};

$("btn-delsched").onclick = async () => {
  if (!confirm(t("Diesen Zeitplan löschen?"))) return;
  await api("/api/schedules/delete", { id: sGewaehlt });
  zeitplanNeu();
  refresh();
};
// --- Laden ------------------------------------------------------------------
async function loadStoragesAndNodes() {
  const nodes = await api("/api/nodes");
  const st = await api("/api/status");
  $("node").innerHTML = nodes.map((n) =>
    `<option value="${esc(n.node)}"${n.node === st.node ? " selected" : ""}>${esc(n.node)}${
      n.status === "online" ? "" : " (offline)"}</option>`).join("");

  const stores = await api(`/api/storages?node=${encodeURIComponent($("node").value)}`);
  $("src-storage").innerHTML = stores.sources.map((s) =>
    `<option value="${esc(s.name)}"${s.name === st.backup_storage ? " selected" : ""}>${
      esc(s.name)} · ${esc(s.type)}</option>`).join("")
    || '<option value="">kein Backup-Storage gefunden</option>';
  $("dst-storage").innerHTML = stores.targets.map((s) =>
    `<option value="${esc(s.name)}"${s.name === st.target_storage ? " selected" : ""}>${
      esc(s.name)} · ${esc(s.type)}${s.avail ? ` · ${(s.avail / 1e9).toFixed(0)} GB frei` : ""}</option>`
    ).join("") || '<option value="">kein Ziel-Storage gefunden</option>';
}

function renderEinstellungen(st) {
  // Die Werte stehen noch in der Datei. Sie hier zu zeigen ist der erste
  // Schritt; änderbar werden sie im nächsten.
  // Nur die Felder, die NICHT vom Nutzer bearbeitet werden - sonst wuerde die
  // Aktualisierung alle 15 Sekunden die Eingabe ueberschreiben.
  const setz = (id, wert) => { const e = $(id); if (e) e.value = wert ?? ""; };
  setz("pm-scratch", `${st.scratch_range[0]} – ${st.scratch_range[1]}`);
  setz("pm-auth", "127.0.0.1:8100 (nur lokal)");

  const zustand = $("pm-state");
  if (zustand) zustand.innerHTML = `<span class="pill ok">verbunden mit ${esc(st.node)}</span>`;

  const stats = $("about-stats");
  if (stats) {
    const kachel = (k, v) => `<div class="stat"><div class="k">${k}</div>
      <div class="v sm">${v}</div></div>`;
    stats.innerHTML = [
      kachel("Proxmox", `<span class="pill ok">${esc(st.node)}</span>`),
      kachel("Anmeldung", '<span class="pill ok">aktiv</span>'),
      kachel("Läufe gesamt", `<span class="num">${histCount}</span>`),
      kachel("Testgäste aktiv", `<span class="num">${st.active_leases ?? 0}</span>`),
      kachel("Zeitpläne", `<span class="num">${st.schedule_count ?? 0}</span>`),
      kachel("Scratch-Bereich",
             `<span class="num">${st.scratch_range[0]}–${st.scratch_range[1]}</span>`),
    ].join("");
  }
}

let histCount = 0;

async function refresh() {
  const st = await api("/api/status");
  const q = (st.pending || []).length;
  $("queue").textContent = st.busy
    ? `läuft: ${st.current.kind || ""}${st.current.vmid}` + (q ? ` · ${q} wartend` : "")
    : q ? `${q} in der Warteschlange` : "";
  $("btn-run").disabled = picked.size === 0;

  inventory = await api("/api/inventory");
  // Die Gastliste der Dateiansicht haengt am selben Bestand.
  if (typeof fGaesteFuellen === "function") fGaesteFuellen();
  renderInventory();
  renderLeases(await api("/api/leases"));
  renderSchedules(await api("/api/schedules"));

  // Die Gästeliste nur neu bauen, wenn sich der Bestand geändert hat, und die
  // Auswahl dabei erhalten. Ein Neuaufbau bei jedem Durchlauf würde sonst alle
  // 15 Sekunden wegwerfen, was gerade zusammengeklickt wurde.
  const auswahl = new Set([...$("s-vmids").selectedOptions].map((o) => o.value));
  const kandidaten = inventory.filter((g) => g.has_backup);
  const schluessel = kandidaten.map((g) => `${g.vmid}:${g.name}`).join("|");
  if ($("s-vmids").dataset.key !== schluessel) {
    $("s-vmids").dataset.key = schluessel;
    $("s-vmids").innerHTML = kandidaten.map((g) =>
      `<option value="${g.vmid}"${auswahl.has(String(g.vmid)) ? " selected" : ""}>${
        g.kind}/${g.vmid} — ${esc(g.name)}</option>`).join("");
  }

  const jobs = await api("/api/jobs?limit=100");
  histCount = jobs.length;
  renderEinstellungen(st);
  $("hist").innerHTML = jobs.length === 0
    ? '<tr><td colspan="6" class="muted">noch keine Läufe</td></tr>'
    : jobs.map((j) => `<tr>
        <td class="mono muted">${esc((j.started || "").replace("T", " ").slice(0, 19))}</td>
        <td class="mono">${esc(j.kind || "")}/${j.vmid}</td>
        <td class="muted small">${esc(j.mode || "")}</td>
        <td>${verdictPill(j.verdict)}</td>
        <td class="mono muted">${j.duration ? Math.round(j.duration) + " s" : ""}</td>
        <td class="small muted">${esc(j.source || "manuell")}</td>
      </tr>`).join("");
}

function switchView(name) {
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("on", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("on", v.id === "v-" + name));
}

// Seitenleisten in Einstellungen und Konto.
document.querySelectorAll(".rail").forEach((rail) => {
  const bereich = rail.closest(".view");
  // Nur Schaltflaechen, die wirklich einen Bereich umschalten. Sonst wuerde
  // hier auch "+ Neuer Zeitplan" ueberschrieben, das in derselben Leiste sitzt.
  rail.querySelectorAll("button[data-pane]").forEach((b) => {
    b.onclick = () => {
      rail.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      bereich.querySelectorAll(".pane").forEach((p) =>
        p.classList.toggle("on", p.id === b.dataset.pane));
    };
  });
});

// --- Verdrahtung ------------------------------------------------------------
document.querySelectorAll("nav button").forEach((b) => {
  b.onclick = () => switchView(b.dataset.view);
});

$("mode").onchange = () => {
  const routed = $("mode").value === "routed";
  $("routed-fields").style.display = routed ? "" : "none";
  $("mode-hint").innerHTML = routed
    ? "Der Gast landet mit der angegebenen Adresse im echten Netz. Vor dem Start läuft " +
      "ein Preflight, der abbricht, falls die Adresse belegt ist."
    : "Der Gast hängt an einer Bridge ohne Uplink und kann nichts erreichen. Prüfungen " +
      "laufen von innen.";
};

$("keep").onchange = () => {
  const v = $("keep").value;
  $("ttl-field").style.display = v === "ttl" ? "" : "none";
  $("keep-hint").textContent =
    v === "destroy" ? "Der Testgast verschwindet direkt nach den Prüfungen. Richtig für den Automatiklauf."
    : v === "ttl" ? "Der Testgast bleibt für das Zeitfenster erreichbar und wird danach automatisch entfernt."
    : "Der Testgast bleibt stehen, bis du ihn unter „Laufende Testgäste“ entfernst. Er belegt so lange eine Scratch-VMID.";
};

$("s-keep").onchange = () => {
  $("s-ttl-field").style.display = $("s-keep").value === "ttl" ? "" : "none";
};

$("node").onchange = async () => { await loadStoragesAndNodes(); };
$("src-storage").onchange = () => { if (picked.size === 1) loadSnapshots([...picked][0]); };
$("checks").oninput = () => { $("checks").dataset.auto = "0"; };

$("pick-all").onchange = () => {
  const rows = inventory.filter((g) => g.has_backup &&
    (!$("only-backup").checked || g.has_backup));
  if ($("pick-all").checked) rows.forEach((g) => picked.add(g.vmid));
  else picked.clear();
  renderInventory();
};
$("only-backup").onchange = renderInventory;

$("btn-preflight").onclick = async () => {
  $("preflight-out").innerHTML = '<div class="hint">prüfe …</div>';
  try {
    const e = ipPool.find((x) => x.id === Number($("ip-pick").value));
    if (!e) throw new Error("Keine Adresse gewählt.");
    const r = await api("/api/preflight", { ip: e.ist_bereich ? e.von + "/" + e.praefix : e.anzeige });
    $("preflight-out").innerHTML = `<div class="${r.ok ? "okbox" : "failbox"}">${esc(r.message)}</div>`;
  } catch (e) {
    $("preflight-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};

$("btn-run").onclick = async () => {
  try {
    const targets = buildTargets();
    $("run-out").innerHTML = "";
    $("log").innerHTML = "";
    openStream();
    const r = await api("/api/run", { targets });
    $("run-out").innerHTML = `<div class="okbox">${r.count} Auftrag/Aufträge eingereiht.</div>`;
    refresh();
  } catch (e) {
    $("run-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};

$("btn-mksched").onclick = () => {
  // Uebernimmt die Auswahl aus "Pruefen" in einen neuen Zeitplan, samt der
  // gerade zusammengestellten Pruefungen.
  switchView("sched");
  zeitplanNeu();
  const namen = [...picked].map((id) => (inventory.find((g) => g.vmid === id) || {}).name)
    .filter(Boolean);
  $("s-name").value = picked.size === 1 ? `Pruefung ${namen[0] || [...picked][0]}`
                                        : `Pruefung ${picked.size} Gaeste`;
  $("s-checks").value = JSON.stringify(checkList, null, 2);
  [...$("s-vmids").options].forEach((o) => { o.selected = picked.has(Number(o.value)); });
  $("s-name").focus();
};


$("btn-reap").onclick = async () => {
  const r = await api("/api/reap", { force: false });
  if (r.found.length === 0) { alert(t("Keine verwaisten Testgäste gefunden.")); return; }
  if (confirm(T`Gefunden: ${r.found.join(", ")}\n\nJetzt vernichten?`)) {
    await api("/api/reap", { force: true });
    refresh();
  }
};

$("btn-reload").onclick = refresh;
$("btn-live-reload").onclick = refresh;




// --- Detailansicht eines Gastes ---------------------------------------------
// Klick auf den Namen: Verlauf dieses Gastes. Klick auf einen Lauf: warum er
// bestanden hat oder woran er gescheitert ist.

let modalVmid = null;

function fmtWhen(s) {
  return String(s || "").replace("T", " ").slice(0, 19);
}

function sourceLabel(src) {
  if (!src || src === "manuell") return "manuell ausgelöst";
  return src;
}

async function openDetail(vmid) {
  const g = inventory.find((x) => x.vmid === vmid);
  if (!g) return;
  modalVmid = vmid;

  $("m-title").innerHTML =
    `${esc(g.name)} <span class="pill ${g.kind}">${g.kind.toUpperCase()}/${g.vmid}</span>` +
    (g.live_scratch ? ` <span class="pill live">Testgast ${g.live_scratch} läuft</span>` : "");
  $("m-sub").textContent =
    (g.latest_ts ? `neuestes Backup ${fmtWhen(g.latest_ts).replace("Z", "")}` : "kein Backup")
    + (g.size ? ` · ${(g.size / 1e9).toFixed(1)} GB` : "")
    + ` · ${g.snapshot_count} Stände`;
  $("modal").style.display = "flex";
  $("m-runs").innerHTML = '<div class="hint">lade …</div>';

  try {
    const runs = await api(`/api/jobs?vmid=${vmid}&limit=50`);
    renderRuns(runs);
  } catch (e) {
    $("m-runs").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
}

function renderRuns(runs) {
  if (runs.length === 0) {
    $("m-runs").innerHTML =
      '<div class="infobox" style="margin:0">Dieser Gast wurde noch nie geprüft. ' +
      'Ein Lauf entsteht nur durch „Jetzt prüfen“ oder durch einen Zeitplan.</div>';
    return;
  }
  $("m-runs").innerHTML = runs.map((j) => `
    <div class="run" data-job="${esc(j.job_id)}">
      <div class="run-head">
        <span class="arrow">&#9656;</span>
        <span class="when">${esc(fmtWhen(j.started))}</span>
        ${verdictPill(j.verdict)}
        <span class="src">${esc(sourceLabel(j.source))} · ${esc(j.mode || "")}</span>
        <span class="dur">${j.duration ? Math.round(j.duration) + " s" : ""}</span>
      </div>
      <div class="run-body"><div class="hint">lade …</div></div>
    </div>`).join("");

  $("m-runs").querySelectorAll(".run").forEach((el) => {
    el.querySelector(".run-head").onclick = () => toggleRun(el);
  });
  // Der jüngste Lauf interessiert am meisten - der klappt gleich auf.
  toggleRun($("m-runs").querySelector(".run"));
}

async function toggleRun(el) {
  if (!el) return;
  const open = el.classList.toggle("open");
  el.querySelector(".arrow").innerHTML = open ? "&#9662;" : "&#9656;";
  if (!open || el.dataset.loaded === "1") return;

  const body = el.querySelector(".run-body");
  try {
    const job = await api(`/api/jobs/${el.dataset.job}`);
    body.innerHTML = renderRunBody(job);
    el.dataset.loaded = "1";
  } catch (e) {
    body.innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
}

function renderRunBody(job) {
  const r = job.report || {};
  const steps = (r.phases || []).map((p) => `
    <div class="step">
      <span class="mark ${p.ok ? "ok" : "fail"}">${p.ok ? "ok" : "FEHLER"}</span>
      <span class="nm">${esc(p.name)}</span>
      <span class="dt">${esc(p.detail || "")}${p.duration ? `  (${p.duration.toFixed(1)} s)` : ""}</span>
    </div>`).join("");

  const checks = (r.checks || []).map((c) => {
    const cls = c.skipped ? "skip" : c.passed ? "ok" : "fail";
    const mark = c.skipped ? "übersprungen" : c.passed ? "ok" : "FEHLER";
    return `
    <div class="step">
      <span class="mark ${cls}">${mark}</span>
      <span class="nm">${esc(c.name)}${c.required === false ? ' <span class="muted">(optional)</span>' : ""}</span>
      <span class="dt">${esc(c.detail || "")}</span>
    </div>`;
  }).join("");

  const failed = (r.checks || []).filter((c) => !c.passed && !c.skipped);
  let why = "";
  if (r.error) {
    why = `<div class="failbox">Abgebrochen: ${esc(r.error)}</div>`;
  } else if (failed.length) {
    why = `<div class="failbox">Durchgefallen an: ${
      esc(failed.map((c) => c.name).join(", "))}</div>`;
  }

  const meta = [
    r.snapshot ? `Backup ${r.snapshot}` : null,
    r.scratch_vmid ? `Testgast ${r.kind}/${r.scratch_vmid}` : null,
    r.target_storage ? `Ziel ${r.target_storage}` : null,
    r.ip ? `IP ${r.ip}` : null,
    r.keep ? `Lebensdauer ${r.keep}` : null,
    r.cleaned_up === false && r.kept ? "stehen geblieben" : null,
  ].filter(Boolean).join(" · ");

  return `
    ${why}
    <div class="mono muted small" style="margin:2px 0 10px">${esc(meta)}</div>
    <h4>Ablauf</h4>${steps || '<div class="hint">keine Phasen</div>'}
    <h4 style="margin-top:12px">Prüfungen</h4>${checks || '<div class="hint">keine Prüfungen</div>'}
    <h4 style="margin-top:12px">Protokoll</h4>
    <pre class="log">${esc(job.log || "")}</pre>`;
}

function closeDetail() {
  $("modal").style.display = "none";
  modalVmid = null;
}

$("m-close").onclick = closeDetail;
$("modal").onclick = (e) => { if (e.target === $("modal")) closeDetail(); };
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("modal").style.display !== "none") closeDetail();
});

$("m-run").onclick = () => {
  if (modalVmid == null) return;
  picked = new Set([modalVmid]);
  lastPickedKey = null;
  closeDetail();
  switchView("check");
  renderInventory();
  $("btn-run").scrollIntoView({ block: "center", behavior: "smooth" });
};
// --- Zeileneditor fuer Pruefungen -------------------------------------------
// Ersetzt das rohe JSON-Feld. Das JSON bleibt als Expertenansicht erhalten und
// wird in beide Richtungen synchron gehalten.

const CHECK_TYPES = {
  boot:        { label: "Bootet",         fields: [] },
  service:     { label: "systemd-Dienst", fields: [["unit", "Unit", "text", "nginx"]] },
  port:        { label: "TCP-Port",       fields: [["port", "Port", "number", "80"]] },
  http:        { label: "HTTP",           fields: [["url", "URL", "text", "http://localhost/"],
                                                   ["expect_status", "Status", "number", "200"],
                                                   ["expect_body", "Muster im Rumpf", "text", "optional"]] },
  tls:         { label: "TLS-Zertifikat", fields: [["port", "Port", "number", "443"],
                                                   ["min_days", "Mind. Tage gueltig", "number", "7"]] },
  command:     { label: "Kommando",       fields: [["run", "Kommando", "text", "systemctl is-system-running"],
                                                   ["expect_output", "Muster in der Ausgabe", "text", "optional"],
                                                   ["expect_rc", "Exitcode", "number", "0"]] },
  file:        { label: "Datei vorhanden", fields: [["path", "Pfad", "text", "/var/lib/app/db.sqlite"],
                                                    ["min_bytes", "Mind. Bytes", "number", "1"]] },
  newest_file: { label: "Juengste Datei", fields: [["path", "Verzeichnis", "text", "/var/lib/app/media"],
                                                   ["max_age_hours", "Hoechstalter (h)", "number", "48"]] },
  file_count:  { label: "Dateianzahl",    fields: [["path", "Verzeichnis", "text", "/var/lib/app/media"],
                                                   ["min_count", "Mindestens", "number", "1"],
                                                   ["pattern", "Namensmuster", "text", "*.pdf"]] },
  postgres:    { label: "PostgreSQL",     fields: [["database", "Datenbank", "text", "postgres"],
                                                   ["user", "Nutzer", "text", "postgres"],
                                                   ["query", "Abfrage", "text", "SELECT 1"],
                                                   ["expect", "Erwartet", "text", "optional"]] },
  mysql:       { label: "MySQL/MariaDB",  fields: [["database", "Datenbank", "text", ""],
                                                   ["user", "Nutzer", "text", "root"],
                                                   ["password", "Passwort", "text", ""],
                                                   ["query", "Abfrage", "text", "SELECT 1"]] },
  db_fresh:    { label: "Daten aktuell",  fields: [["engine", "System", "text", "postgres"],
                                                   ["database", "Datenbank", "text", "meinedb"],
                                                   ["user", "Nutzer", "text", "postgres"],
                                                   ["query", "Abfrage auf Zeitstempel", "text", "SELECT max(created_at) FROM t"],
                                                   ["max_age_hours", "Hoechstalter (h)", "number", "48"]] },
};

let checkList = [];        // die Wahrheit; Zeilen und JSON leiten sich daraus ab
let jsonVisible = false;

function liveScratchFor(vmid) {
  const g = inventory.find((x) => x.vmid === vmid);
  return g && g.live_scratch ? g.live_scratch : null;
}

function currentScratch() {
  if (picked.size !== 1) return null;
  return liveScratchFor([...picked][0]);
}

function updateLiveHint() {
  const sc = currentScratch();
  const single = picked.size === 1;
  $("btn-discover").disabled = !sc;
  document.querySelectorAll("[data-try]").forEach((b) => { b.disabled = !sc; });
  $("live-hint").innerHTML = sc
    ? `Werkbank: Testgast <span class="mono">${sc}</span> l&auml;uft &mdash; Erkennung und Probelauf sind m&ouml;glich.`
    : single
      ? "Kein laufender Testgast. F&uuml;r Erkennung und Probelauf einen Lauf mit der " +
        "Lebensdauer <b>&bdquo;stehen lassen&ldquo;</b> starten."
      : "F&uuml;r Erkennung und Probelauf genau einen Gast ausw&auml;hlen.";
}

function renderCheckRows() {
  const box = $("check-rows");
  box.innerHTML = "";
  if (checkList.length === 0) {
    box.innerHTML = '<div class="hint">Noch keine Pr&uuml;fung. &bdquo;Aus Testgast erkennen&ldquo; ' +
      'oder &bdquo;+ Pr&uuml;fung&ldquo; verwenden.</div>';
  }
  checkList.forEach((c, i) => box.appendChild(buildRow(c, i)));
  syncJson();
  updateLiveHint();
}

function buildRow(check, idx) {
  const type = check.type || "command";
  const def = CHECK_TYPES[type] || CHECK_TYPES.command;
  const row = document.createElement("div");
  row.className = "crow";

  const opts = Object.entries(CHECK_TYPES).map(([k, v]) =>
    `<option value="${k}"${k === type ? " selected" : ""}>${esc(v.label)}</option>`).join("");

  row.innerHTML = `
    <div class="top">
      <select data-type="${idx}">${opts}</select>
      <input class="cname" data-name="${idx}" value="${esc(check.name || "")}" placeholder="Bezeichnung">
      <button class="sm iconbtn" data-try="${idx}" title="Gegen laufenden Testgast ausf&uuml;hren">&#9654;</button>
      <button class="sm iconbtn danger" data-del="${idx}" title="Entfernen">&times;</button>
    </div>
    <div class="fields">${def.fields.map(([key, label, kind, ph]) => `
      <div><label>${esc(label)}</label>
        <input data-field="${idx}:${key}" type="${kind}" placeholder="${esc(ph)}"
               value="${esc(check[key] == null ? "" : check[key])}"></div>`).join("")}
    </div>
    <div class="flags">
      <label><input type="checkbox" data-flag="${idx}:external"
        ${check.external ? "checked" : ""}> von au&szlig;en (Modus geroutet)</label>
      <label><input type="checkbox" data-flag="${idx}:required"
        ${check.required === false ? "" : "checked"}> Pflicht</label>
    </div>
    <div class="res" data-res="${idx}" style="display:none"></div>`;

  row.querySelector(`[data-type="${idx}"]`).onchange = (e) => {
    checkList[idx] = { type: e.target.value, name: checkList[idx].name || "" };
    renderCheckRows();
  };
  row.querySelector(`[data-name="${idx}"]`).oninput = (e) => {
    checkList[idx].name = e.target.value;
    syncJson();
  };
  row.querySelectorAll("[data-field]").forEach((inp) => {
    inp.oninput = () => {
      const key = inp.dataset.field.split(":")[1];
      const v = inp.value.trim();
      if (v === "") delete checkList[idx][key];
      else checkList[idx][key] = inp.type === "number" ? Number(v) : v;
      syncJson();
    };
  });
  row.querySelectorAll("[data-flag]").forEach((cb) => {
    cb.onchange = () => {
      const key = cb.dataset.flag.split(":")[1];
      if (key === "required") checkList[idx].required = cb.checked;
      else if (cb.checked) checkList[idx].external = true;
      else delete checkList[idx].external;
      syncJson();
    };
  });
  row.querySelector(`[data-del="${idx}"]`).onclick = () => {
    checkList.splice(idx, 1);
    renderCheckRows();
  };
  row.querySelector(`[data-try="${idx}"]`).onclick = () => tryCheck(idx);
  return row;
}

function syncJson() { $("checks").value = JSON.stringify(checkList, null, 2); }

async function tryCheck(idx) {
  const sc = currentScratch();
  const box = document.querySelector(`[data-res="${idx}"]`);
  if (!sc) return;
  box.style.display = "";
  box.className = "res wait";
  box.textContent = "laeuft ...";
  try {
    const r = await api("/api/checks/try", { scratch_vmid: sc, check: checkList[idx] });
    box.className = "res " + (r.passed ? "ok" : "fail");
    box.textContent = `[${r.status}] ${r.detail || ""}`.trim();
  } catch (e) {
    box.className = "res fail";
    box.textContent = e.message;
  }
}

$("btn-addcheck").onclick = () => {
  checkList.push({ type: "port", name: "", port: 80 });
  renderCheckRows();
};

$("btn-json").onclick = () => {
  jsonVisible = !jsonVisible;
  $("checks").style.display = jsonVisible ? "" : "none";
  $("check-rows").style.display = jsonVisible ? "none" : "";
  $("btn-json").classList.toggle("primary", jsonVisible);
  if (!jsonVisible) {
    // Rueckweg aus der Expertenansicht: JSON uebernehmen, falls gueltig.
    try {
      const v = JSON.parse($("checks").value || "[]");
      if (Array.isArray(v)) checkList = v;
    } catch {
      alert(t("Das JSON ist nicht gueltig - die Zeilenansicht zeigt den letzten gueltigen Stand."));
    }
    renderCheckRows();
  }
};

$("btn-discover").onclick = async () => {
  const sc = currentScratch();
  if (!sc) return;
  $("live-hint").innerHTML = '<span class="muted">untersuche Testgast &hellip;</span>';
  try {
    const r = await api("/api/discover", { scratch_vmid: sc });
    const fresh = r.checks.filter((c) =>
      !checkList.some((e) => JSON.stringify(e) === JSON.stringify(c)));
    checkList = checkList.concat(fresh);
    renderCheckRows();
    $("live-hint").innerHTML = `${r.count} Pr&uuml;fungen erkannt, ${fresh.length} &uuml;bernommen. ` +
      `Ports: ${r.ports.join(", ") || "keine"}.`;
  } catch (e) {
    $("live-hint").innerHTML = `<span style="color:var(--fail)">${esc(e.message)}</span>`;
  }
};

// --- Konto, Benutzer und zweiter Faktor -------------------------------------
// Es wird bewusst nichts im Browser abgelegt. Wer angemeldet ist, beantwortet
// bei jeder Anfrage der Server.

let me = null;

async function loadMe() {
  const r = await fetch("/api/me");
  const data = await r.json();
  if (!data.authenticated) { location.href = "/login"; return null; }
  me = data.user;
  // Sprache des Kontos, sonst was der Browser nahelegt.
  spracheSetzen(me.sprache || spracheVorgabe());
  schalterZeichnen();
  document.body.dataset.role = me.role || "user";
  const rn = t({ super: "Super Admin", admin: "Admin", user: "Benutzer" }[me.role] || me.role);
  $("whoami").innerHTML = `${esc(me.name || me.email)} <span class="pill ${
    { super: "super", admin: "adm", user: "usr" }[me.role] || "usr"}">${esc(rn)}</span>`;
  $("k-wer").textContent = `${me.email} · ${rn}`;
  const t2 = $("tick-2fa");
  if (t2) t2.textContent = me.two_factor ? t("an") : t("aus");
  render2fa();
  return me;
}

function render2fa() {
  const an = !!(me && me.two_factor);
  $("k-2fa-off").style.display = an ? "none" : "";
  $("k-2fa-on").style.display = an ? "" : "none";
  if (an) $("k-2fa-setup").style.display = "none";
}

function kmsg(kind, text) {
  $("k-out").innerHTML = `<div class="${kind}box">${esc(text)}</div>`;
}

$("btn-logout").onclick = async () => {
  await fetch("/api/auth/sign-out", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: "{}" });
  location.href = "/login";
};

// --- Zwei-Faktor einschalten ------------------------------------------------
$("btn-2fa-on").onclick = async () => {
  const password = $("k-pass").value;
  if (!password) return kmsg("fail", "Bitte das eigene Passwort zur Bestätigung eingeben.");
  $("btn-2fa-on").disabled = true;
  try {
    // Better Auth legt das Geheimnis an und liefert URI samt Ersatzcodes.
    const r = await fetch("/api/auth/two-factor/enable", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.message || "Das Passwort stimmt nicht.");

    const uri = data.totpURI || data.totpUri || "";
    $("k-secret").value = (uri.match(/[?&]secret=([^&]+)/) || [, ""])[1];

    const qr = await api("/api/qr", { text: uri });
    $("k-qr").innerHTML = qr.svg || "";

    if (Array.isArray(data.backupCodes) && data.backupCodes.length) {
      $("k-codes").innerHTML =
        '<div class="warnbox">Wiederherstellungscodes &mdash; jetzt sichern. Jeder gilt ' +
        'einmal und sie werden nur dieses eine Mal angezeigt.</div>' +
        '<div class="codes">' + data.backupCodes.map((c) => `<span>${esc(c)}</span>`).join("") +
        "</div>";
    }
    $("k-2fa-off").style.display = "none";
    $("k-2fa-setup").style.display = "";
    $("k-pass").value = "";
    $("k-code").focus();
  } catch (e) {
    kmsg("fail", e.message);
  } finally {
    $("btn-2fa-on").disabled = false;
  }
};

$("btn-2fa-confirm").onclick = async () => {
  const code = $("k-code").value.trim();
  if (code.length !== 6) return kmsg("fail", "Der Code hat sechs Ziffern.");
  $("btn-2fa-confirm").disabled = true;
  try {
    const r = await fetch("/api/auth/two-factor/verify-totp", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.message || "Der Code stimmt nicht.");
    kmsg("ok", "Zwei-Faktor ist jetzt aktiv. Ab der nächsten Anmeldung wird der Code verlangt.");
    $("k-2fa-setup").style.display = "none";
    await loadMe();
    loadUsers();
  } catch (e) {
    kmsg("fail", e.message);
    $("k-code").value = "";
  } finally {
    $("btn-2fa-confirm").disabled = false;
  }
};

$("btn-2fa-off").onclick = async () => {
  const password = $("k-pass-off").value;
  if (!password) return kmsg("fail", "Bitte das eigene Passwort zur Bestätigung eingeben.");
  if (!confirm(t("Zwei-Faktor wirklich abschalten?"))) return;
  try {
    const r = await fetch("/api/auth/two-factor/disable", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.message || "Das Passwort stimmt nicht.");
    $("k-pass-off").value = "";
    kmsg("warn", "Zwei-Faktor ist abgeschaltet.");
    await loadMe();
    loadUsers();
  } catch (e) {
    kmsg("fail", e.message);
  }
};

// --- Benutzerverwaltung -----------------------------------------------------
// Was hier ausgeblendet wird, ist Bequemlichkeit - verbindlich prueft der
// Server. Ein ausgeblendeter Knopf schuetzt nichts.

const ROLLEN_NAME = { super: "Super Admin", admin: "Admin", user: "Benutzer" };

function rollenMarke(rolle) {
  const k = { super: "super", admin: "adm", user: "usr" }[rolle] || "usr";
  return `<span class="pill ${k}">${esc(ROLLEN_NAME[rolle] || rolle)}</span>`;
}

function darfVerwalten(zielRolle) {
  if (!me) return false;
  if (me.role === "super") return true;
  if (me.role === "admin") return zielRolle === "user";
  return false;
}

async function loadUsers() {
  if (!me || me.role === "user") {
    // Einfache Benutzer sehen die Verwaltung gar nicht.
    const box = $("users");
    if (box) box.innerHTML =
      '<tr><td colspan="5" class="muted">Benutzer verwalten dürfen nur Admins.</td></tr>';
    document.querySelectorAll(".only-admin").forEach((e) => { e.style.display = "none"; });
    return;
  }
  try {
    const list = await api("/api/users");
    const tu = $("tick-users"); if (tu) tu.textContent = list.length;
    $("users").innerHTML = list.map((u) => {
      const ich = u.id === me.user_id;
      const kann = darfVerwalten(u.role) && !ich;
      const knoepfe = [];
      if (kann) {
        knoepfe.push(`<button class="sm" data-u2fa="${esc(u.id)}"${u.twoFactorEnabled ? "" : " disabled"}
          title="Zwei-Faktor zurücksetzen, damit sich der Benutzer neu einrichten kann">2FA zurücksetzen</button>`);
      }
      if (me.role === "super" && !ich) {
        const ziel = u.role === "admin" ? "user" : "admin";
        knoepfe.push(`<button class="sm" data-urole="${esc(u.id)}" data-neu="${ziel}">Zu ${
          ROLLEN_NAME[ziel]} machen</button>`);
      }
      if (kann || (me.role === "super" && !ich)) {
        knoepfe.push(`<button class="sm" data-ulogout="${esc(u.id)}">Abmelden</button>`);
        knoepfe.push(`<button class="sm danger" data-udel="${esc(u.id)}">Löschen</button>`);
      }
      if (ich) knoepfe.push('<span class="hintline">das bist du</span>');
      else if (!knoepfe.length) knoepfe.push('<span class="hintline">nur Super Admin</span>');

      return `<tr>
        <td>${esc(u.name || "")}${ich ? ' <span class="pill live">du</span>' : ""}</td>
        <td class="mono small">${esc(u.email)}</td>
        <td>${rollenMarke(u.role)}</td>
        <td>${u.twoFactorEnabled
          ? '<span class="pill ok">an</span>' : '<span class="pill none">aus</span>'}</td>
        <td style="text-align:right">${knoepfe.join(" ")}</td></tr>`;
    }).join("");

    const tun = async (frage, pfad, daten) => {
      if (frage && !confirm(t(frage))) return;
      try { await api(pfad, daten); loadUsers(); }
      catch (e) { $("u-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`; }
    };
    $("users").querySelectorAll("[data-udel]").forEach((b) => {
      b.onclick = () => tun("Diesen Benutzer endgültig entfernen?",
                            "/api/users/delete", { id: b.dataset.udel });
    });
    $("users").querySelectorAll("[data-u2fa]").forEach((b) => {
      b.onclick = () => tun(
        "Zwei-Faktor dieses Benutzers zurücksetzen? Er meldet sich danach nur mit Passwort an und richtet ihn neu ein. Alle seine Sitzungen werden beendet.",
        "/api/users/reset-2fa", { id: b.dataset.u2fa });
    });
    $("users").querySelectorAll("[data-urole]").forEach((b) => {
      b.onclick = () => tun(T`Rolle auf „${ROLLEN_NAME[b.dataset.neu]}“ ändern?`,
                            "/api/users/role", { id: b.dataset.urole, role: b.dataset.neu });
    });
    $("users").querySelectorAll("[data-ulogout]").forEach((b) => {
      b.onclick = async () => {
        const r = await api("/api/users/logout", { id: b.dataset.ulogout });
        $("u-out").innerHTML = `<div class="okbox">${r.ended} Sitzung(en) beendet.</div>`;
      };
    });

    // Die Rollenauswahl beim Anlegen gibt es nur für Super Admins.
    const rollenfeld = $("u-role-wrap");
    if (rollenfeld) rollenfeld.style.display = me.role === "super" ? "" : "none";
  } catch (e) {
    $("users").innerHTML = `<tr><td colspan="5" class="muted">${esc(e.message)}</td></tr>`;
  }
}

$("btn-adduser").onclick = async () => {
  const pass = $("u-pass").value;
  if (pass.length < 10) {
    $("u-out").innerHTML = '<div class="failbox">Das Passwort braucht mindestens 10 Zeichen.</div>';
    return;
  }
  try {
    await api("/api/users", {
      email: $("u-mail").value.trim(), password: pass, name: $("u-name").value.trim(),
      role: $("u-role") ? $("u-role").value : "user",
    });
    $("u-name").value = $("u-mail").value = $("u-pass").value = "";
    $("u-out").innerHTML = '<div class="okbox">Benutzer angelegt.</div>';
    loadUsers();
  } catch (e) {
    $("u-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};
// --- Anmeldeversuche --------------------------------------------------------
async function loadLogins() {
  try {
    const list = await api("/api/logins?limit=60");
    $("logins").innerHTML = list.length === 0
      ? '<tr><td colspan="4" class="muted">keine Versuche verzeichnet</td></tr>'
      : list.map((l) => `<tr>
          <td class="mono muted small">${esc(String(l.ts).replace("T", " ").slice(0, 19))}</td>
          <td class="mono small">${esc(l.ip)}</td>
          <td class="small">${esc(l.identifier || "—")}</td>
          <td>${l.ok ? '<span class="pill ok">erfolgreich</span>'
                     : '<span class="pill fail">fehlgeschlagen</span>'}</td>
        </tr>`).join("");
  } catch { /* ohne Anmeldung nicht abrufbar */ }
}

// --- Hinterlegte Test-Adressen ----------------------------------------------
// Einzelne Adressen und Bereiche. Bei einem Bereich waehlt erst der Lauf die
// konkrete Adresse - so lassen sich mehrere Gaeste gleichzeitig geroutet
// pruefen.

let ipPool = [];
let ipBearbeitet = null;      // Kennung des Eintrags, der gerade geändert wird

function renderIpPool() {
  // Auswahlliste im Lauf-Formular
  const cur = $("ip-pick").value;
  $("ip-pick").innerHTML = ipPool.length === 0
    ? '<option value="">keine hinterlegt — unter Einstellungen anlegen</option>'
    : ipPool.map((e) => `<option value="${e.id}" data-gw="${esc(e.gateway || "")}">${
        esc(e.label)} — ${esc(e.anzeige)}${
        e.ist_bereich ? ` (${e.anzahl} Adressen)` : ""}</option>`).join("");
  if (cur && [...$("ip-pick").options].some((o) => o.value === cur)) $("ip-pick").value = cur;
  syncGateway();

  const tick = $("tick-ips");
  if (tick) tick.textContent = ipPool.reduce((n, e) => n + (e.anzahl || 1), 0);
  const c = $("ip-count");
  if (c) {
    const adr = ipPool.reduce((n, e) => n + (e.anzahl || 1), 0);
    c.textContent = `${ipPool.length} Einträge · ${adr} Adressen`;
  }

  // Verwaltungstabelle
  $("ips").innerHTML = ipPool.length === 0
    ? '<tr><td colspan="5" class="muted">noch keine Adresse hinterlegt</td></tr>'
    : ipPool.map((e) => `<tr>
        <td>${esc(e.label)}</td>
        <td class="mono">${esc(e.anzeige)}${e.ist_bereich
          ? `<div class="hintline">${e.anzahl} Adressen</div>` : ""}</td>
        <td class="mono muted">${esc(e.gateway || "—")}</td>
        <td class="small muted">${esc(e.note || "")}</td>
        <td class="right">
          <button class="sm" data-editip="${e.id}">Ändern</button>
          <button class="sm danger" data-delip="${e.id}">Löschen</button>
        </td></tr>`).join("");

  $("ips").querySelectorAll("[data-delip]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm(t("Diesen Eintrag entfernen?"))) return;
      await api("/api/ips/delete", { id: Number(b.dataset.delip) });
      if (ipBearbeitet === Number(b.dataset.delip)) ipFormularLeeren();
      await loadIpPool();
    };
  });
  $("ips").querySelectorAll("[data-editip]").forEach((b) => {
    b.onclick = () => ipBearbeiten(Number(b.dataset.editip));
  });
}

function ipBearbeiten(id) {
  const e = ipPool.find((x) => x.id === id);
  if (!e) return;
  ipBearbeitet = id;
  $("ip-label").value = e.label || "";
  $("ip-cidr").value = e.anzeige || e.ip_cidr || "";
  $("ip-gw").value = e.gateway || "";
  $("ip-note").value = e.note || "";
  $("btn-saveip").textContent = "Änderung speichern";
  $("ip-editnote").innerHTML =
    `<div class="infobox">Eintrag <b>${esc(e.label)}</b> wird geändert. ` +
    '<button class="sm" id="btn-ipcancel" style="margin-left:8px">Abbrechen</button></div>';
  $("btn-ipcancel").onclick = ipFormularLeeren;
  $("ip-label").focus();
}

function ipFormularLeeren() {
  ipBearbeitet = null;
  $("ip-label").value = $("ip-cidr").value = $("ip-gw").value = $("ip-note").value = "";
  $("btn-saveip").textContent = "Adresse speichern";
  $("ip-editnote").innerHTML = "";
  $("ip-out").innerHTML = "";
}

function syncGateway() {
  const opt = $("ip-pick").selectedOptions[0];
  if (opt && opt.dataset.gw) $("gw").value = opt.dataset.gw;
}

async function loadIpPool() {
  ipPool = await api("/api/ips");
  renderIpPool();
}

$("ip-pick").onchange = syncGateway;

$("btn-checkip").onclick = async () => {
  $("ip-out").innerHTML = '<div class="hint">prüfe …</div>';
  try {
    const r = await api("/api/preflight", { ip: $("ip-cidr").value.trim() });
    $("ip-out").innerHTML = `<div class="${r.ok ? "okbox" : "failbox"}">${esc(r.message)}</div>`;
  } catch (e) {
    $("ip-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};

$("btn-saveip").onclick = async () => {
  try {
    await api("/api/ips", {
      id: ipBearbeitet || undefined,
      label: $("ip-label").value.trim(),
      ip_cidr: $("ip-cidr").value.trim(),
      gateway: $("ip-gw").value.trim(),
      note: $("ip-note").value.trim(),
    });
    const geaendert = ipBearbeitet !== null;
    ipFormularLeeren();
    $("ip-out").innerHTML = `<div class="okbox">${
      geaendert ? "Änderung gespeichert." : "Eintrag angelegt."}</div>`;
    await loadIpPool();
  } catch (e) {
    $("ip-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
};
// --- Einstellungen ----------------------------------------------------------
// Drei Gruppen mit unterschiedlicher Absicherung. Was hier steht, ist
// Bedienkomfort - abgewiesen wird serverseitig.

let einstellungen = null;
let pmGeprueft = false;      // Proxmox erst nach bestandener Prüfung speicherbar

function feld(id) { return $(id); }

async function loadEinstellungen() {
  if (!me || me.role !== "super") return;
  try {
    einstellungen = await api("/api/settings");
    const w = einstellungen.werte;

    // Proxmox
    setzeWert("pm-host", w["host.host"]);
    setzeWert("pm-user", w["host.user"]);
    setzeWert("pm-key", w["host.key_file"]);

    // Standardwerte
    setzeWert("pm-src", w["restore.backup_storage"]);
    setzeWert("pm-dst", w["restore.target_storage"]);
    setzeWert("pm-iso", w["restore.isolated_bridge"]);
    setzeWert("pm-lan", w["restore.lan_bridge"]);
    setzeWert("pm-boot", w["restore.boot_timeout"]);
    setzeWert("pm-agent", w["restore.agent_timeout"]);
    setzeWert("pm-keep", w["default_keep"]);
    setzeWert("pm-ttl", w["default_ttl"]);
    setzeWert("pm-parallel", w["max_parallel"]);

    // Zugriff
    const fwd = $("pm-fwd");
    if (fwd) fwd.checked = !!w["trust_forwarded_for"];
    const pub = $("pm-public");
    if (pub) pub.value = w["public_url"] || "";
    const sec = $("pm-secure");
    if (sec) sec.checked = !!w["secure_cookies"];

    // Sperr-Schwellen
    setzeWert("sp-delay", w["delay_from"]);
    setzeWert("sp-lock", w["lock_from"]);
    setzeWert("sp-min", w["lock_minutes"]);

    zeigeRuecknahme(einstellungen.ruecknahme);
    kennzeichneUeberlagert(einstellungen.ueberlagert || []);
  } catch (e) {
    /* Nur Super Admins - für alle anderen bleibt der Bereich ohnehin verborgen. */
  }
}

function setzeWert(id, wert) {
  const e = $(id);
  if (e && wert !== undefined && wert !== null) e.value = wert;
}

function kennzeichneUeberlagert(keys) {
  // Zeigt an, welche Werte von der Datei abweichen.
  const marke = $("cfg-abweichungen");
  if (!marke) return;
  marke.innerHTML = keys.length === 0
    ? '<span class="hintline">Alle Werte stammen aus der Konfigurationsdatei.</span>'
    : `<span class="hintline">${keys.length} Wert(e) in der Oberfläche geändert: ` +
      `<span class="mono">${keys.map(esc).join(", ")}</span></span>`;
}

function zeigeRuecknahme(r) {
  const box = $("cfg-rollback");
  if (!box) return;
  if (!r) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="warnbox">
    <b>Änderung noch nicht bestätigt.</b> Wenn du bis
    ${esc(String(r.faellig).replace("T", " ").slice(11, 16))} nicht bestätigst, stellt
    Proxfy den vorherigen Stand wieder her — damit dich eine falsche Einstellung nicht
    dauerhaft aussperrt.
    <div class="btns" style="margin-top:9px">
      <button class="primary" id="btn-cfg-confirm">Änderung bestätigen</button>
      <button id="btn-cfg-rollback">Jetzt zurücknehmen</button>
    </div></div>`;
  $("btn-cfg-confirm").onclick = async () => {
    await api("/api/settings/confirm", {});
    loadEinstellungen();
  };
  $("btn-cfg-rollback").onclick = async () => {
    await api("/api/settings/rollback", {});
    loadEinstellungen();
    refresh();
  };
}

async function speichere(gruppe, werte, ausgabeId, password) {
  const out = $(ausgabeId);
  try {
    const r = await api("/api/settings", { gruppe, werte, password });
    out.innerHTML = '<div class="okbox">Gespeichert.</div>';
    await loadEinstellungen();
    await refresh();
    return r;
  } catch (e) {
    out.innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
    return null;
  }
}

// --- Proxmox-Anbindung ------------------------------------------------------
$("btn-pm-test").onclick = async () => {
  const out = $("pm-out");
  out.innerHTML = '<div class="hint">prüfe Verbindung …</div>';
  try {
    const r = await api("/api/settings/test", {
      host: $("pm-host").value.trim(), user: $("pm-user").value.trim(),
      key_file: $("pm-key").value.trim(),
    });
    out.innerHTML = `<div class="${r.ok ? "okbox" : "failbox"}">${esc(r.message)}</div>`;
    pmGeprueft = r.ok;
    $("btn-pm-save").disabled = !r.ok;
  } catch (e) {
    out.innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
    pmGeprueft = false;
    $("btn-pm-save").disabled = true;
  }
};

["pm-host", "pm-user", "pm-key"].forEach((id) => {
  const e = $(id);
  if (e) e.oninput = () => { pmGeprueft = false; $("btn-pm-save").disabled = true; };
});

$("btn-pm-save").onclick = () => {
  if (!pmGeprueft) return;
  speichere("proxmox", {
    "host.host": $("pm-host").value.trim(),
    "host.user": $("pm-user").value.trim(),
    "host.key_file": $("pm-key").value.trim(),
  }, "pm-out");
};

// --- Standardwerte ----------------------------------------------------------
$("btn-def-save").onclick = () => {
  speichere("defaults", {
    "restore.backup_storage": $("pm-src").value.trim(),
    "restore.target_storage": $("pm-dst").value.trim(),
    "restore.isolated_bridge": $("pm-iso").value.trim(),
    "restore.lan_bridge": $("pm-lan").value.trim(),
    "restore.boot_timeout": Number($("pm-boot").value),
    "restore.agent_timeout": Number($("pm-agent").value),
    "default_keep": $("pm-keep").value,
    "default_ttl": Number($("pm-ttl").value),
    "max_parallel": Number($("pm-parallel").value),
  }, "def-out");
};

$("btn-def-reset").onclick = () => loadEinstellungen();


// --- Reverse Proxy -----------------------------------------------------------
$("btn-proxycheck").onclick = async () => {
  const url = $("pm-public").value.trim();
  const out = $("proxy-out");
  if (!url) { out.innerHTML = `<div class="warnbox">Trage zuerst die Adresse von außen ein.</div>`; return; }
  out.innerHTML = `<div class="infobox">Prüfe ${url} …</div>`;
  let r;
  try { r = await api("/api/settings/proxy", { public_url: url }); }
  catch (e) { out.innerHTML = `<div class="warnbox">${e.message}</div>`; return; }
  // Ein selbst ausgestelltes Zertifikat sagt nichts darüber, ob der Proxy
  // richtig zeigt. Dann noch einmal ohne Prüfung, aber mit deutlichem Hinweis.
  let zertHinweis = "";
  if (r && r.zertifikat) {
    zertHinweis = `<div class="warnbox">${r.message}</div>`;
    r = await api("/api/settings/proxy", { public_url: url, insecure: true });
  }
  out.innerHTML = zertHinweis + (r && r.ok
    ? `<div class="okbox">${r.message}</div>`
    : `<div class="warnbox">${(r && r.message) || "Prüfung fehlgeschlagen."}</div>`);
};

$("btn-copyadv").onclick = () => {
  navigator.clipboard.writeText($("px-adv").textContent);
  $("btn-copyadv").textContent = "Kopiert";
  setTimeout(() => { $("btn-copyadv").textContent = "Block kopieren"; }, 1500);
};
// --- Zugriff und Netzwerk ---------------------------------------------------
$("btn-net-save").onclick = async () => {
  const pw = $("net-pass").value;
  if (!pw) {
    $("net-out").innerHTML =
      '<div class="failbox">Bitte das eigene Passwort zur Bestätigung eingeben.</div>';
    return;
  }
  const r = await speichere("zugriff", { trust_forwarded_for: $("pm-fwd").checked,
                                        public_url: $("pm-public").value.trim(),
                                        secure_cookies: $("pm-secure").checked },
                            "net-out", pw);
  $("net-pass").value = "";
  if (r && r.ruecknahme) {
    $("net-out").innerHTML += `<div class="warnbox">Bestätige die Änderung innerhalb von
      ${r.ruecknahme} Minuten, sonst wird sie zurückgenommen.</div>`;
  }
};

// --- Sperr-Schwellen --------------------------------------------------------
$("btn-sp-save").onclick = async () => {
  const pw = $("sp-pass").value;
  if (!pw) {
    $("sp-out").innerHTML =
      '<div class="failbox">Bitte das eigene Passwort zur Bestätigung eingeben.</div>';
    return;
  }
  await speichere("sperre", {
    delay_from: Number($("sp-delay").value),
    lock_from: Number($("sp-lock").value),
    lock_minutes: Number($("sp-min").value),
  }, "sp-out", pw);
  $("sp-pass").value = "";
};

// Wochentagswahl einmalig aufbauen.
function buildWeekdayPicker() {
  $("s-wd").innerHTML = WD.map((d, i) => `
    <label class="${i < 5 ? "on" : ""}"><input type="checkbox" data-wd="${i}"
      ${i < 5 ? "checked" : ""}><span class="small">${d}</span></label>`).join("");
  $("s-wd").querySelectorAll("[data-wd]").forEach((cb) => {
    cb.onchange = () => cb.closest("label").classList.toggle("on", cb.checked);
  });
}

// --- Sprache ----------------------------------------------------------------
function schalterZeichnen() {
  const s = sprache();
  $("btn-de").classList.toggle("on", s === "de");
  $("btn-en").classList.toggle("on", s === "en");
}

/** Zeichnet neu, was app.js selbst erzeugt hat.
 *
 * Das statische Markup traegt der Uebersetzer selbst - er hat sich beim Laden
 * gemerkt, was urspruenglich dastand, und kann deshalb in beide Richtungen.
 * Fuer alles Erzeugte geht das nicht: eine englische Zeile laesst sich nicht
 * zurueckuebersetzen, das Verzeichnis kennt nur den Weg hin. Also wird es aus
 * den Daten neu aufgebaut - dabei entsteht wieder deutscher Text, den der
 * Beobachter uebersetzt, falls Englisch eingestellt ist.
 *
 * Bewusst NICHT dabei: die Einstellungsmasken. Sie neu zu laden verwuerfe
 * ungespeicherte Aenderungen. Ihre Beschriftungen stehen ohnehin im Markup.
 */
async function neuZeichnen() {
  // Die Wochentage werden beim Aufbauen auf Mo-Fr zurueckgesetzt. Was gewaehlt
  // war, bleibt erhalten - sonst verliert ein Sprachwechsel die Eingabe.
  const wdVorher = [...document.querySelectorAll("#s-wd [data-wd]")]
    .filter((c) => c.checked).map((c) => c.dataset.wd);
  const gastVorher = $("f-guest") ? $("f-guest").value : "";

  buildChips("chips", "checks");
  buildChips("s-chips", "s-checks");
  buildWeekdayPicker();
  document.querySelectorAll("#s-wd [data-wd]").forEach((cb) => {
    const an = wdVorher.includes(cb.dataset.wd);
    cb.checked = an;
    cb.closest("label").classList.toggle("on", an);
  });
  renderCheckRows();

  try {
    await refresh();
    await loadIpPool();
    await loadUsers();
    await loadLogins();
  } catch (e) { /* im Zweifel bleibt eine Ansicht deutsch stehen */ }

  if ($("f-guest") && gastVorher) $("f-guest").value = gastVorher;
}

async function spracheWechseln(neu) {
  if (neu === sprache()) return;
  spracheSetzen(neu);          // statisches Markup und Beobachter
  schalterZeichnen();
  try { await api("/api/me/language", { sprache: neu }); } catch (e) { /* egal */ }
  await neuZeichnen();
}

$("btn-de").onclick = () => spracheWechseln("de");
$("btn-en").onclick = () => spracheWechseln("en");

// --- Dateien aus einem Backup -----------------------------------------------
// Der Pfad wandert als base64 hin und her, genau so, wie proxmox-file-restore
// ihn liefert. Das erspart die Frage, wie ein Dateiname mit Doppelpunkt,
// Schraegstrich oder Umlaut durch zwei Schichten kommt.
let fWeg = [];          // Brotkrumen: [{name, pfad}]
let fGewaehlt = null;   // der markierte Eintrag

function fBytes(n) {
  if (n === null || n === undefined) return "";
  const e = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < e.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + " " + e[i];
}

function fGaesteFuellen() {
  const sel = $("f-guest");
  if (!sel) return;
  const mit = inventory.filter((g) => g.has_backup);
  sel.innerHTML = `<option value="">${t("bitte wählen")}</option>` + mit.map((g) =>
    `<option value="${g.vmid}">${esc(g.name)} · ${g.vmid} · ${esc(g.kind || "")}</option>`
  ).join("");
}

async function fStaendeFuellen() {
  const vmid = $("f-guest").value;
  const sel = $("f-snap");
  fWeg = []; fGewaehlt = null; fZeichnen([]);
  if (!vmid) { sel.innerHTML = ""; return; }
  sel.innerHTML = `<option value="">${t("lade …")}</option>`;
  try {
    const r = await api(`/api/snapshots?vmid=${encodeURIComponent(vmid)}`);
    const liste = r.snapshots || r || [];
    sel.innerHTML = liste.map((s) =>
      `<option value="${esc(s.volid)}">${esc((s.ts || "").replace("T", " ").slice(0, 16))}</option>`
    ).join("");
    if (liste.length) await fOeffnen("/");
  } catch (e) {
    sel.innerHTML = "";
    $("f-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
}

async function fOeffnen(pfad, name) {
  const volid = $("f-snap").value;
  if (!volid) return;
  $("f-list").innerHTML = `<div class="hint">${t("lade …")}</div>`;
  $("f-out").innerHTML = "";
  try {
    const r = await api("/api/files/list", { volid, pfad });
    if (pfad === "/") fWeg = [];
    else fWeg.push({ name: name || "…", pfad });
    fGewaehlt = null;
    fMarkieren();
    fZeichnen(r.eintraege || []);
  } catch (e) {
    $("f-list").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  }
}

function fZurueck(bis) {
  fWeg = fWeg.slice(0, bis);
  const letzt = fWeg[fWeg.length - 1];
  const pfad = letzt ? letzt.pfad : "/";
  fWeg = fWeg.slice(0, -1);
  fOeffnen(pfad, letzt && letzt.name);
}

function fKrumen() {
  const c = $("f-crumbs");
  if (!c) return;
  c.innerHTML = `<button class="sm" data-fup="0">${t("Anfang")}</button>` +
    fWeg.map((k, i) => `<span>/</span><button class="sm" data-fup="${i + 1}">${esc(k.name)}</button>`).join("");
  c.querySelectorAll("[data-fup]").forEach((b) => {
    b.onclick = () => fZurueck(Number(b.dataset.fup));
  });
}

function fZeichnen(eintraege) {
  fKrumen();
  const l = $("f-list");
  if (!eintraege.length) {
    l.innerHTML = `<div class="hint">${t("hier liegt nichts")}</div>`;
    return;
  }
  l.innerHTML = eintraege.map((e, i) =>
    `<div class="row" data-fi="${i}">
       <span class="ic">${e.verzeichnis ? "▸" : "·"}</span>
       <span class="nm">${esc(e.name || "")}</span>
       <span class="sz">${e.verzeichnis ? "" : fBytes(e.groesse)}</span>
     </div>`).join("");
  l.querySelectorAll("[data-fi]").forEach((row) => {
    const e = eintraege[Number(row.dataset.fi)];
    row.onclick = () => {
      if (e.verzeichnis) { fOeffnen(e.pfad, e.name); return; }
      fGewaehlt = e;
      l.querySelectorAll(".row").forEach((r) => r.classList.remove("on"));
      row.classList.add("on");
      fMarkieren();
    };
  });
}

function fMarkieren() {
  const s = $("f-sel");
  const b = $("btn-fdl");
  if (!s || !b) return;
  if (!fGewaehlt) {
    s.className = "hint";
    s.textContent = t("Noch nichts gewählt.");
    b.disabled = true;
    return;
  }
  s.className = "okbox";
  s.innerHTML = `<b>${esc(fGewaehlt.name)}</b> · ${fBytes(fGewaehlt.groesse)}`;
  b.disabled = false;
}

async function fHerunterladen() {
  const pw = $("f-pass").value;
  if (!pw) {
    $("f-out").innerHTML = `<div class="failbox">${t("Bitte das eigene Passwort zur Bestätigung eingeben.")}</div>`;
    return;
  }
  if (!fGewaehlt) return;
  const b = $("btn-fdl");
  b.disabled = true;
  $("f-out").innerHTML = `<div class="infobox">${t("hole die Datei …")}</div>`;
  try {
    // Bewusst nicht ueber api(): die Antwort ist die Datei selbst, kein JSON.
    const r = await fetch("/api/files/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volid: $("f-snap").value, pfad: fGewaehlt.pfad, password: pw }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || d.message || `HTTP ${r.status}`);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fGewaehlt.name || "datei";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    $("f-out").innerHTML = `<div class="okbox">${t("Fertig.")}</div>`;
  } catch (e) {
    $("f-out").innerHTML = `<div class="failbox">${esc(e.message)}</div>`;
  } finally {
    $("f-pass").value = "";
    b.disabled = false;
  }
}

if ($("f-guest")) {
  $("f-guest").onchange = fStaendeFuellen;
  $("f-snap").onchange = () => { fWeg = []; fOeffnen("/"); };
  $("btn-fdl").onclick = fHerunterladen;
}

// --- Start ------------------------------------------------------------------
buildChips("chips", "checks");
buildChips("s-chips", "s-checks");
buildWeekdayPicker();
renderCheckRows();
$("mode").onchange();
$("keep").onchange();

(async () => {
  try {
    // Wer nicht angemeldet ist, landet in loadMe() auf der Anmeldemaske.
    if (!await loadMe()) return;
    await loadStoragesAndNodes();
    await loadIpPool();
    await loadUsers();
    await loadLogins();
    await loadEinstellungen();
    zeitplanNeu();
    await refresh();
    setInterval(refresh, 15000);
  } catch (e) {
    $("whoami").textContent = "Fehler: " + e.message;
  }
})();
