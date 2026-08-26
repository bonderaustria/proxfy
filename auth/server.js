/**
 * HTTP-Aufsatz um die Better-Auth-Instanz.
 *
 * Zwei Gruppen von Pfaden:
 *   /api/auth/*   die von Better Auth selbst bedienten Endpunkte
 *   /internal/*   Benutzerverwaltung, ausschliesslich fuer die Python-Anwendung
 *
 * Gebunden wird nur auf 127.0.0.1. Die /internal-Pfade verlangen zusaetzlich
 * ein gemeinsames Geheimnis, damit sie auch dann nicht offenstehen, falls
 * jemand spaeter versehentlich die Bindung aufweitet.
 */
import express from "express";
import Database from "better-sqlite3";
import { toNodeHandler, fromNodeHeaders } from "better-auth/node";
import QRCode from "qrcode";
import { auth } from "./auth.js";

const PORT = Number(process.env.PROXFY_AUTH_PORT || 8100);
const BIND = "127.0.0.1";
const INTERNAL_SECRET = process.env.PROXFY_INTERNAL_SECRET || "";
const DB_PATH = process.env.PROXFY_AUTH_DB || "/opt/proxfy/auth.db";

if (!process.env.BETTER_AUTH_SECRET) {
  console.error("BETTER_AUTH_SECRET fehlt - Abbruch.");
  process.exit(1);
}

const app = express();
app.disable("x-powered-by");

// Muss VOR express.json() stehen: Better Auth liest den Rumpf selbst.
app.all("/api/auth/*", toNodeHandler(auth));

app.use(express.json());

function requireInternal(req, res, next) {
  if (!INTERNAL_SECRET || req.get("X-Internal-Secret") !== INTERNAL_SECRET) {
    return res.status(403).json({ error: "kein Zugriff" });
  }
  next();
}

const db = new Database(DB_PATH);

/** Sitzung zu den mitgegebenen Kopfzeilen aufloesen. */
app.get("/internal/session", requireInternal, async (req, res) => {
  try {
    const session = await auth.api.getSession({
      headers: fromNodeHeaders({ cookie: req.get("X-Forward-Cookie") || "" }),
    });
    if (!session) return res.json({ authenticated: false });
    res.json({
      authenticated: true,
      user: {
        id: session.user.id,
        email: session.user.email,
        name: session.user.name,
        twoFactorEnabled: !!session.user.twoFactorEnabled,
        role: session.user.role || "user",
      },
      expiresAt: session.session?.expiresAt || null,
    });
  } catch (e) {
    res.json({ authenticated: false, error: String(e && e.message) });
  }
});

/** Benutzerliste - fuer die Verwaltungsansicht. */
app.get("/internal/users", requireInternal, (req, res) => {
  try {
    const rows = db.prepare(
      "SELECT id, email, name, emailVerified, twoFactorEnabled, role, createdAt " +
      "FROM user ORDER BY createdAt"
    ).all();
    res.json(rows.map((r) => ({
      id: r.id, email: r.email, name: r.name,
      twoFactorEnabled: !!r.twoFactorEnabled,
      role: r.role || "user",
      createdAt: r.createdAt,
    })));
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** Benutzer anlegen. */
app.post("/internal/users", requireInternal, async (req, res) => {
  const { email, password, name, role } = req.body || {};
  if (!email || !password) return res.status(400).json({ error: "E-Mail und Passwort noetig" });
  try {
    // Das allererste Konto wird Super Admin - sonst koennte niemand jemals
    // Einstellungen aendern oder Rollen vergeben.
    const bestehende = db.prepare("SELECT count(*) AS n FROM user").get().n;
    const rolle = bestehende === 0 ? "super"
                : ["super", "admin", "user"].includes(role) ? role : "user";

    await auth.api.signUpEmail({
      body: { email, password, name: name || email },
      asResponse: false,
    });
    db.prepare("UPDATE user SET role=? WHERE email=?").run(rolle, email);
    res.json({ created: true, email, role: rolle });
  } catch (e) {
    res.status(400).json({ error: String((e && e.body && e.body.message) || e.message || e) });
  }
});

/** Benutzer entfernen - samt Sitzungen, Konten und 2FA-Geheimnis. */
app.post("/internal/users/delete", requireInternal, (req, res) => {
  const { id } = req.body || {};
  if (!id) return res.status(400).json({ error: "id fehlt" });
  try {
    const n = db.prepare("SELECT count(*) AS n FROM user").get().n;
    if (n <= 1) return res.status(400).json({ error: "Der letzte Benutzer kann nicht entfernt werden" });
    // Der letzte Super Admin bleibt bestehen - sonst koennte niemand mehr
    // Einstellungen aendern oder Rollen vergeben. Diese Grenze steht bewusst
    // auch hier und nicht nur in der aufrufenden Schicht.
    const ziel = db.prepare("SELECT role FROM user WHERE id=?").get(id);
    if (ziel && ziel.role === "super") {
      const s = db.prepare("SELECT count(*) AS n FROM user WHERE role='super'").get().n;
      if (s <= 1) {
        return res.status(400).json({ error: "Der letzte Super Admin kann nicht entfernt werden." });
      }
    }
    db.transaction(() => {
      db.prepare("DELETE FROM session WHERE userId=?").run(id);
      db.prepare("DELETE FROM account WHERE userId=?").run(id);
      try { db.prepare("DELETE FROM twoFactor WHERE userId=?").run(id); } catch { /* Tabelle fehlt */ }
      db.prepare("DELETE FROM user WHERE id=?").run(id);
    })();
    res.json({ deleted: true });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** Alle Sitzungen eines Benutzers beenden - nach Passwortwechsel oder Verdacht. */
app.post("/internal/users/logout", requireInternal, (req, res) => {
  const { id } = req.body || {};
  if (!id) return res.status(400).json({ error: "id fehlt" });
  try {
    const info = db.prepare("DELETE FROM session WHERE userId=?").run(id);
    res.json({ ended: info.changes });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** Passwort eines Benutzers pruefen, ohne ihn anzumelden.
 *
 * Better Auth bietet dafuer keinen eigenen Endpunkt. Der Umweg ueber die
 * regulaere Anmeldung ist zuverlaessig - die dabei entstehende Sitzung wird
 * sofort wieder entfernt, damit keine Karteileiche zurueckbleibt. Ist der
 * zweite Faktor aktiv, entsteht ohnehin keine Sitzung, sondern nur die
 * Aufforderung dazu; auch das beweist, dass das Passwort stimmte.
 */
app.post("/internal/verify-password", requireInternal, async (req, res) => {
  const { email, password } = req.body || {};
  if (!email || !password) return res.status(400).json({ error: "E-Mail und Passwort noetig" });

  const vorher = db.prepare("SELECT token FROM session").all().map((r) => r.token);
  try {
    await auth.api.signInEmail({ body: { email, password }, asResponse: false });
  } catch {
    return res.json({ ok: false });
  } finally {
    // Neu entstandene Sitzungen wieder entfernen.
    const nachher = db.prepare("SELECT token FROM session").all().map((r) => r.token);
    const neu = nachher.filter((t) => !vorher.includes(t));
    for (const t of neu) db.prepare("DELETE FROM session WHERE token=?").run(t);
  }
  res.json({ ok: true });
});

/** Rolle eines Benutzers setzen.
 *
 * Wer das darf, entscheidet die Python-Schicht - hier wird nur ausgefuehrt.
 * Die eine Regel, die AUCH hier steht: der letzte Super Admin bleibt einer.
 * Sonst koennte sich der Betrieb aussperren, und diese Grenze soll nicht davon
 * abhaengen, dass die aufrufende Schicht richtig prueft.
 */
app.post("/internal/users/role", requireInternal, (req, res) => {
  const { id, role } = req.body || {};
  if (!id || !["super", "admin", "user"].includes(role)) {
    return res.status(400).json({ error: "id und gueltige Rolle noetig" });
  }
  try {
    const ziel = db.prepare("SELECT id, email, role FROM user WHERE id=?").get(id);
    if (!ziel) return res.status(404).json({ error: "Benutzer nicht gefunden" });

    if (ziel.role === "super" && role !== "super") {
      const n = db.prepare("SELECT count(*) AS n FROM user WHERE role='super'").get().n;
      if (n <= 1) {
        return res.status(400).json({
          error: "Der letzte Super Admin kann nicht herabgestuft werden.",
        });
      }
    }
    db.prepare("UPDATE user SET role=? WHERE id=?").run(role, id);
    res.json({ id, role, email: ziel.email });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** Zwei-Faktor eines fremden Benutzers zuruecksetzen.
 *
 * Der regulaere Weg (two-factor/disable) verlangt das Passwort des Betroffenen
 * und hilft daher nicht, wenn genau der sein Geraet verloren hat. Hier wird das
 * Geheimnis samt Ersatzcodes geloescht; der Betroffene meldet sich danach nur
 * mit Passwort an und richtet den zweiten Faktor neu ein.
 */
app.post("/internal/users/reset-2fa", requireInternal, (req, res) => {
  const { id } = req.body || {};
  if (!id) return res.status(400).json({ error: "id fehlt" });
  try {
    const ziel = db.prepare("SELECT id, email FROM user WHERE id=?").get(id);
    if (!ziel) return res.status(404).json({ error: "Benutzer nicht gefunden" });
    db.transaction(() => {
      try { db.prepare("DELETE FROM twoFactor WHERE userId=?").run(id); } catch { /* Tabelle fehlt */ }
      db.prepare("UPDATE user SET twoFactorEnabled=0 WHERE id=?").run(id);
      // Alle Sitzungen beenden - wer den zweiten Faktor verliert, soll sich
      // ueberall neu anmelden muessen.
      db.prepare("DELETE FROM session WHERE userId=?").run(id);
    })();
    res.json({ reset: true, email: ziel.email });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** QR-Code als SVG - fuer die Einrichtung der Authenticator-App. */
app.post("/internal/qr", requireInternal, async (req, res) => {
  const text = (req.body || {}).text;
  if (!text) return res.status(400).json({ error: "text fehlt" });
  try {
    const svg = await QRCode.toString(String(text), {
      type: "svg", margin: 1, errorCorrectionLevel: "M",
      color: { dark: "#e6edf3", light: "#0000" },
    });
    res.json({ svg });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message) });
  }
});

/** Gibt es ueberhaupt schon einen Benutzer? Steuert die Ersteinrichtung. */
app.get("/internal/bootstrap", requireInternal, (req, res) => {
  try {
    const n = db.prepare("SELECT count(*) AS n FROM user").get().n;
    res.json({ users: n, needsSetup: n === 0 });
  } catch (e) {
    res.json({ users: 0, needsSetup: true, error: String(e && e.message) });
  }
});

app.listen(PORT, BIND, () => {
  console.log(`Anmeldedienst laeuft auf http://${BIND}:${PORT} (Datenbank ${DB_PATH})`);
});
