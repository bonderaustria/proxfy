/**
 * Better-Auth-Instanz.
 *
 * Sitzungen liegen serverseitig in SQLite, der Browser bekaeme nur eine
 * HttpOnly-Cookie-Kennung. Es gibt bewusst keine Token im localStorage und
 * keine JWTs - eine Sitzung laesst sich damit jederzeit serverseitig beenden.
 *
 * Der Dienst lauscht ausschliesslich auf 127.0.0.1 und ist damit von aussen
 * nicht erreichbar. Davor sitzt die Python-Anwendung, die die Anfragen
 * durchreicht und die Sitzung bei jeder Anfrage hier nachschlaegt.
 */
import { betterAuth } from "better-auth";
import { twoFactor } from "better-auth/plugins";
import Database from "better-sqlite3";

const DB_PATH = process.env.PROXFY_AUTH_DB || "/opt/proxfy/auth.db";

// Die oeffentliche Adresse, unter der der Browser die Anwendung sieht - also
// die der Python-Anwendung, nicht die dieses Dienstes.
const PUBLIC_URL = process.env.BETTER_AUTH_URL || "http://localhost:8099";

export const auth = betterAuth({
  appName: "Restore-Verifikation",
  database: new Database(DB_PATH),
  baseURL: PUBLIC_URL,
  basePath: "/api/auth",
  secret: process.env.BETTER_AUTH_SECRET,

  emailAndPassword: {
    enabled: true,
    // Kein Mailversand eingerichtet - Konten legt eine Verwalterin von Hand an.
    requireEmailVerification: false,
    autoSignIn: false,
    minPasswordLength: 10,
  },

  user: {
    additionalFields: {
      // 'super' | 'admin' | 'user'. Bewusst nicht vom Anmeldenden setzbar
      // (input: false) - die Rolle vergibt ausschliesslich die Anwendung.
      // Waere sie beschreibbar, koennte sich jemand bei der Registrierung
      // selbst zum Super Admin machen.
      role: { type: "string", defaultValue: "user", input: false, required: false },
    },
  },

  session: {
    expiresIn: 60 * 60 * 12,      // 12 Stunden
    updateAge: 60 * 60,           // stille Verlaengerung hoechstens stuendlich
  },

  advanced: {
    // Die Anwendung laeuft im LAN auch ohne TLS. Wer sie ins Internet stellt,
    // setzt PROXFY_SECURE_COOKIES=1 - dann gibt der Browser das Cookie nur
    // ueber HTTPS heraus.
    useSecureCookies: process.env.PROXFY_SECURE_COOKIES === "1",
    defaultCookieAttributes: {
      httpOnly: true,
      sameSite: "lax",
    },
  },

  trustedOrigins: (process.env.PROXFY_TRUSTED_ORIGINS || PUBLIC_URL)
    .split(",").map((s) => s.trim()).filter(Boolean),

  plugins: [twoFactor({ issuer: "Restore-Verifikation" })],
});
