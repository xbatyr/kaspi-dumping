export const SESSION_COOKIE = "repricer_session";
export const SESSION_SECONDS = 12 * 60 * 60;

function secret(): string {
  return process.env.DASHBOARD_PASSWORD ?? "";
}

async function signingKey(): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(`kaspi-repricer-session-v1:${secret()}`),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

function toHex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function createSession(): Promise<string> {
  const payload = `v1.${Math.floor(Date.now() / 1000) + SESSION_SECONDS}.${crypto.randomUUID()}`;
  const signature = await crypto.subtle.sign("HMAC", await signingKey(), new TextEncoder().encode(payload));
  return `${payload}.${toHex(signature)}`;
}

export async function validSession(value: string | undefined): Promise<boolean> {
  if (!value || !secret()) return false;
  const match = /^(v1\.[0-9]{10}\.[0-9a-f-]{36})\.([0-9a-f]{64})$/.exec(value);
  if (!match) return false;
  const expires = Number(match[1].split(".")[1]);
  const now = Math.floor(Date.now() / 1000);
  if (expires <= now || expires > now + SESSION_SECONDS) return false;
  const signature = Uint8Array.from(match[2].match(/../g)!, (byte) => Number.parseInt(byte, 16));
  return crypto.subtle.verify("HMAC", await signingKey(), signature, new TextEncoder().encode(match[1]));
}

export function safeDestination(value: string | null): string {
  if (!value?.startsWith("/") || value.startsWith("//") || /[\u0000-\u001f\u007f\\]/.test(value)) return "/";
  try {
    return new URL(value, "https://local.invalid").origin === "https://local.invalid" ? value : "/";
  } catch {
    return "/";
  }
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}
