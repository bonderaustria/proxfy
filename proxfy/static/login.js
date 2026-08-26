"use strict";

// Anmeldemaske. Es wird bewusst nichts im Browser gespeichert - weder Token
// noch Kennung. Die Sitzung entsteht als HttpOnly-Cookie, das JavaScript gar
// nicht lesen kann; alles Weitere liegt serverseitig.

const $ = (id) => document.getElementById(id);

function show(step) {
  document.querySelectorAll(".step").forEach((s) => s.classList.toggle("on", s.id === step));
  $("out").innerHTML = "";
}

function msg(kind, text) {
  $("out").innerHTML = `<div class="msg ${kind}">${String(text)
    .replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))}</div>`;
}

async function call(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await r.json(); } catch { /* leerer Rumpf */ }
  return { status: r.status, data };
}

function fehlerText(status, data) {
  if (status === 429) return data.message || "Zu viele Versuche.";
  if (data && data.message) return data.message;
  if (status === 401 || status === 403) return "E-Mail oder Passwort stimmt nicht.";
  return `Unerwarteter Fehler (${status}).`;
}

// --- Ersteinrichtung --------------------------------------------------------
$("btn-setup").onclick = async () => {
  const name = $("su-name").value.trim();
  const email = $("su-mail").value.trim();
  const pass = $("su-pass").value;
  if (pass.length < 10) return msg("fail", "Das Passwort braucht mindestens 10 Zeichen.");
  if (pass !== $("su-pass2").value) return msg("fail", "Die beiden Passwörter stimmen nicht überein.");

  $("btn-setup").disabled = true;
  const { status, data } = await call("/api/setup", { email, password: pass, name });
  $("btn-setup").disabled = false;
  if (status >= 400) return msg("fail", data.error || fehlerText(status, data));

  msg("ok", "Konto angelegt. Du kannst dich jetzt anmelden.");
  $("li-mail").value = email;
  setTimeout(() => { show("s-login"); $("li-pass").focus(); }, 900);
};

// --- Anmeldung --------------------------------------------------------------
$("btn-login").onclick = async () => {
  const email = $("li-mail").value.trim();
  const password = $("li-pass").value;
  if (!email || !password) return msg("fail", "Bitte E-Mail und Passwort ausfüllen.");

  $("btn-login").disabled = true;
  msg("info", "prüfe …");
  const { status, data } = await call("/api/auth/sign-in/email", { email, password });
  $("btn-login").disabled = false;

  if (status >= 400) return msg(status === 429 ? "warn" : "fail", fehlerText(status, data));

  // Bei aktivem zweiten Faktor kommt hier noch keine Sitzung zustande.
  if (data && data.twoFactorRedirect) {
    show("s-2fa");
    $("li-code").focus();
    return;
  }
  location.href = "/";
};

// --- Zweiter Faktor ---------------------------------------------------------
async function verifyTotp() {
  const code = $("li-code").value.trim();
  if (code.length !== 6) return msg("fail", "Der Code hat sechs Ziffern.");
  $("btn-2fa").disabled = true;
  const { status, data } = await call("/api/auth/two-factor/verify-totp", { code });
  $("btn-2fa").disabled = false;
  if (status >= 400) {
    $("li-code").value = "";
    $("li-code").focus();
    return msg(status === 429 ? "warn" : "fail",
               status === 429 ? fehlerText(status, data) : "Der Code stimmt nicht.");
  }
  location.href = "/";
}

$("btn-2fa").onclick = verifyTotp;
$("li-code").addEventListener("input", () => {
  $("li-code").value = $("li-code").value.replace(/\D/g, "").slice(0, 6);
  if ($("li-code").value.length === 6) verifyTotp();
});

$("btn-backup").onclick = () => { show("s-backup"); $("li-backup").focus(); };
$("btn-back2fa").onclick = () => { show("s-2fa"); $("li-code").focus(); };

$("btn-backupgo").onclick = async () => {
  const code = $("li-backup").value.trim();
  if (!code) return msg("fail", "Bitte einen Wiederherstellungscode eingeben.");
  $("btn-backupgo").disabled = true;
  const { status, data } = await call("/api/auth/two-factor/verify-backup-code", { code });
  $("btn-backupgo").disabled = false;
  if (status >= 400) return msg(status === 429 ? "warn" : "fail",
                                status === 429 ? fehlerText(status, data)
                                               : "Dieser Code ist ungültig oder verbraucht.");
  location.href = "/";
};

// Auf Enter reagieren, damit man nicht zur Maus greifen muss.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const offen = document.querySelector(".step.on");
  if (!offen) return;
  const knopf = offen.querySelector("button:not(.ghost)");
  if (knopf && !knopf.disabled) knopf.click();
});

// --- Start ------------------------------------------------------------------
(async () => {
  try {
    const r = await fetch("/api/me");
    const me = await r.json();
    if (me.authenticated) { location.href = "/"; return; }
    show(me.needs_setup ? "s-setup" : "s-login");
    ($(me.needs_setup ? "su-name" : "li-mail")).focus();
  } catch {
    show("s-login");
  }
})();
