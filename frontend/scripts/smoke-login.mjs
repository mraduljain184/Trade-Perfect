/**
 * Smoke test: login + /auth/me against a running API.
 * Usage: node scripts/smoke-login.mjs [baseUrl] [username] [password]
 */
const base = (process.argv[2] || process.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(
  /\/$/,
  '',
);
const username = process.argv[3] || 'priya.sharma';
const password = process.argv[4] || process.env.SMOKE_PASSWORD;

if (!password) {
  console.error('Set SMOKE_PASSWORD or pass password as 3rd arg');
  process.exit(1);
}

const loginRes = await fetch(`${base}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password }),
});
if (!loginRes.ok) {
  console.error('login failed', loginRes.status, await loginRes.text());
  process.exit(1);
}
const login = await loginRes.json();
const meRes = await fetch(`${base}/api/v1/auth/me`, {
  headers: { Authorization: `Bearer ${login.accessToken}` },
});
if (!meRes.ok) {
  console.error('/me failed', meRes.status, await meRes.text());
  process.exit(1);
}
const me = await meRes.json();
if (me.username !== username) {
  console.error('username mismatch', me);
  process.exit(1);
}
console.log('OK login + me for', me.fullName, `(${me.role})`);
