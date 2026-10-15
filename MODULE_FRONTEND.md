# MODULE FE — Frontend (React + TypeScript + Ant Design)

> **Attach to every FE chat:** `PROJECT_CONTEXT.md` + `PROGRESS.md` + this file. Also attach the frontend files the step modifies. **Don't paste `mock_fixtures.json` into the chat** (1.7 MB). Its shape is described in §3 below, which is all Claude needs.
> **Run from:** Claude #2. **Paired with:** Member D (tests every screen per role) and E (Vercel deploy).

---

## 1. Mission

Build every screen of Trade Perfect: login, role-based navigation, the trade booking form with real-time validation, bulk upload, trade dashboard, repair queue, audit trail viewer, team performance, ML monitoring and model registry, admin pages, notifications and the chat widget. It talks to the backend only through the REST contract (PROJECT_CONTEXT §8.1/8.2) and runs fully on **mock data** until the backend is ready.

## 2. Boundaries

| You own | You don't touch |
|---|---|
| `frontend/**` | `backend/**` |

- `src/types/api.ts` is a **verbatim copy of §8.2**. If a field seems missing, don't invent one: ask the backend owner and log it in §17.
- Use the routes and access rules in **§13.1**, the dashboard columns in **§13.2**, and the colours in **§13.3**.
- Hiding buttons is only UX; the backend enforces permissions. Still, hide anything the user's `permissions[]` doesn't allow.

## 3. Mock data (provided) — `frontend/src/mocks/fixtures/mock_fixtures.json`

Generated from the **real 20K file** with a prototype of the scoring spec, so the numbers, reasons and explanations look exactly like production. Keys:

| Key | Type (§8.2) | Contents |
|---|---|---|
| `trades` | `TradeResult[]` | 287 trades: PASS 115, MONITOR 87, EXCEPTION 60, REJECTED 25 (inactive account, unknown symbol, blank ID/type). Scored ones include `ruleHits` and `shapValues` |
| `repairQueue` | `QueueItem[]` | 262 items with mixed status/assignee, plus `canResubmit`/`canReject` |
| `dashboardSummary` | `{today, historical}` | KPI counts and percentages |
| `modelProduction` | `ModelVersion` | v1 metrics, targets, `passed` (FPR fails → red card) |
| `modelVersions` | `ModelVersion[]` | v1 PRODUCTION, v0 ARCHIVED |
| `metricsHistory` | `ModelEvaluation[]` | 9 monthly points (Jan–Sep 2026) |
| `driftLatest` | `DriftCheck` | 8 features: GREEN/AMBER/RED mix |
| `featureImportance` | `{feature, meanAbsShap}[]` | 17 features, sorted |
| `reference` | `{symbols, counterparties[{name,riskTier,tierLabel}], accounts[{accountId,isActive}], traders}` | Dropdown data |

**Not in the fixtures — hand-write small ones:** mock users (one per role: `trader` = "John Miller", `ops.user1`, `ops.manager`, `ml.admin`, `sys.admin`; any password), `AuditEvent[]` for a few trades (build them from `trades`), team performance, notifications, chat answers, `UploadBatch` progress (simulate processedRows increasing on each poll).
MSW handlers should do **real filtering, sorting and pagination** over the fixture arrays, so the screens behave realistically.

## 4. Libraries (pin exact versions in package.json)
react 18, react-dom, vite, typescript, antd 5, @ant-design/icons, react-router-dom 6, @tanstack/react-query 5, axios, dayjs (with utc + timezone plugins, display **Asia/Kolkata**), recharts, zod, msw 2, vitest, @testing-library/react, @testing-library/user-event, jsdom; optional @playwright/test.

## 5. Shared building blocks (build in FE-1, reuse everywhere)
- `api/client.ts`: axios instance with `baseURL = VITE_API_BASE_URL + '/api/v1'`, Bearer token, 401 → logout + redirect `/login?expired=1`, errors normalised to `{code, message, fields[]}`, blob download helper for CSV/PDF (reads the filename from `Content-Disposition`).
- `auth/`: `AuthContext` (user, token, login, logout, `hasPermission(code)`), `ProtectedRoute` (permission and/or roles; 403 page otherwise), token in `sessionStorage`.
- `layouts/AppLayout.tsx`: left menu built from a **single route config** (path, label, icon, required permission), filtered by permissions; header with user name, role tag, NotificationBell and logout; default landing per role (§13.1).
- Components: `StatusTag`, `PriorityTag`, `QueueStatusTag`, `RiskScoreBadge` (0–1 colour scale with the same thresholds 0.5/0.7), `RiskFactorsBar` (mini stacked bar + tooltip listing factor/pct/detail), `ExplanationPopover`, `TradeResultCard`, `AuditBox`, `ExportCsvButton` (passes current filters), `DateRangeFilter`, `MetricCard` (value, target, ✅/❌ colour), `PsiSeverityTag`, `EmptyState`, `ErrorState`, `PageHeader`.
- `utils/format.ts`: `fmtDate('2026-10-10') → '10 Oct 2026'`, `fmtDateTime` (IST), `fmtNum` (thousands separators: quantities reach 223,333,333), `fmtScore` (2 dp), `fmtPct`.
- `utils/constants.ts`: enum values and colour map (§13.3), page size 50.
- Filters live in the **URL query string** (`useSearchParams`), so back/refresh keep them and links from other pages can pre-filter.

## 6. Steps

### FE-1 — Shell, auth, mocks
Vite + TS + AntD (`ConfigProvider` theme: primary `#0b5cad`, compact tables `size="small"`), folder structure (§13), `.env.example` (`VITE_API_BASE_URL`, `VITE_USE_MOCKS=true`), MSW worker started only when `VITE_USE_MOCKS === 'true'`, `types/api.ts`, everything in §5, placeholder pages for every route, 403/404 pages.
**Acceptance:**
- [ ] Logging in as each mock role shows the right menu and landing page.
- [ ] Refresh keeps the session; logout clears it; an expired token redirects to login.
- [ ] `npm run build` has zero TypeScript errors.

### FE-2 — Trade Booking Form (FR-TB-001..006, 008, 010, 011; FR-RV-011)
Route `/trades/new` (Submit mode) and `/trades/:tradeId` (Update mode).
- Fields in order: Trade ID (read-only, "Auto-generated on submit" in new mode), Trade Type (radio BUY/SELL), Symbol (searchable select), Quantity (integer > 0, thousands separator), Price (decimal > 0, 4 dp), Counterparty (searchable select showing a tier tag), Account ID (select; inactive accounts shown disabled with an "Inactive" label), Trade Date, Settlement Date (≥ trade date; amber warning if weekend), SSI Matched (Yes/No), Currency Matched (Yes/No), Trader Name (read-only = logged-in user).
- **Real-time validation:** a zod schema runs on change/blur and shows inline errors under each field. A debounced (500 ms) `POST /trades/validate` maps server errors (`fields[].field`) onto the same inline errors. Submit stays disabled while there are errors. A 422 on submit also maps inline (FR-TB-006).
- After submit: `TradeResultCard` shows a big status tag, final score + rule score + ML score bars, RiskFactorsBar, likely reason, suggested action, explanation, a queue ID link, and buttons "Book another trade" / "View in dashboard".
- Update mode: prefill from `GET /trades/:id`; the button text is **Update Trade** (FR-TB-005). With `?queueId=RQ-…`: a banner "Applying fix for RQ-…" and a required "Applied fix" textarea, sent as `appliedFix`, + `changeReason`. Tabs: **Details | History** (versions table with an old → new diff) **| Audit** (AuditBox + mini timeline).
- REJECTED trade → read-only page with rejection reasons and the note "Must be fixed in the upstream/source system" (FR-RQ-011/012). No Update button.
**Acceptance:** vitest covers required fields, BUY/SELL, positive numbers, settlement ≥ trade date, and inline server errors.

### FE-3 — Bulk Upload + My Trades (FR-TB-002, 007, 009)
- `/trades/upload`: Upload.Dragger (`.csv`, `.xlsx`, ≤ 20 MB), a "Download CSV template" link (header row from §2), the note "Max 10,000 rows processed per batch; larger files are processed in batches automatically".
- After upload: poll `GET /uploads/{batchId}` every 2 s with a progress bar (processedRows / totalRows), then summary cards (Accepted, PASS, MONITOR, EXCEPTION, REJECTED), "Download rejection report", and "View these trades" (→ grid filtered by `batchId`). Show the error state for a FAILED batch.
- `/my-trades` (Trader): the same grid component as the dashboard with `mine=true`.

### FE-4 — Trade Dashboard (FR-RQ-001..011 of the Dashboard section)
- **KPI rows:** *Today*: Total, PASS, MONITOR, EXCEPTION (count + %), REJECTED. *Historical*: the same (FR-RQ-002/003), colour-coded.
- **Charts** (recharts): status by trade date (stacked bars), top 10 counterparties by EXCEPTION count, risk score histogram.
- **Filters:** Trade Date range, Settlement Date range, Status (multi), Priority (multi); **search** by Trade ID/Symbol (debounced) (FR-RQ-004/005).
- **Grid:** exactly the §13.2 columns. Trade ID link → `/trades/:id` (**plain text for REJECTED**, FR-RQ-009/011); Queue ID link → `/repair-queue?queueId=` (FR-RQ-010); server-side pagination **50/page** (FR-RQ-007); sortable score/date columns.
- **Export CSV** of the current filters (FR-RQ-006).
**Acceptance:** filters survive refresh (URL); the export downloads a file; REJECTED rows aren't clickable.

### FE-5 — Repair Queue + Team page (Repair Queue FR-RQ-001..015)
- **Top bar:** total items + counts by status/priority. Filters: Status (default All), Priority (default All), Assigned To (Manager only), search, **"Hide LOW" toggle (default ON**, see §16 Q2). Buttons: **Bulk Assignment, Bulk Re-Submit, Export CSV**.
- **Table** with row selection: Queue ID, Trade ID, Priority, Status, Risk Score, Likely Reason, Suggested Fix, Assigned To, Updated. Default sort priority HIGH → LOW, then score.
- **Row actions:**
  - **Apply Fix** → `/trades/:tradeId?queueId=…`.
  - **Re-Submit** (disabled with a tooltip "EXCEPTION (HIGH) trades must be fixed first" when `!canResubmit`, FR-RQ-007).
  - **Reject** (only when `canReject`; modal with a required reason, FR-RQ-008).
  - **Approve** (Manager, HIGH).
  - **Assign** (Manager: user select) / **Pick up** (Ops User on unassigned items).
- **Bulk:** Bulk Assignment (Manager modal), Bulk Re-Submit (warn and skip non-LOW items, FR-RQ-010). Show a `BulkResult` summary (n succeeded, list of failures with reasons).
- Clicking a row opens a drawer with trade details, risk factors, fixes and history. `?queueId=` in the URL auto-opens that item.
- `/team` (Manager): table per Ops user (assigned, in progress, resolved, avg hours) + a bar chart.
**Acceptance:** each role sees the right buttons; after an action the list refreshes (react-query invalidation); the status changes visibly (OPEN → IN_PROGRESS → RESOLVED).

### FE-6 — Audit Trail viewer (FR-AT-001..007)
- Search: Trade ID, date range + date-field selector (Trade Date / Settlement Date / Event Time), User (dropdown from `/reference/users`), Action.
- Results table (paginated). When a single Trade ID is searched: AntD **Timeline** of its lifecycle (colour by action type) + **AuditBox** card (Trade Id, Queue ID, Risk Score, Likely Reason, Recommended Fix, Applied Fix, Applied By, Applied Timestamp, Status).
- **Export CSV**; **Report** button → `POST /audit/trades/{id}/report` → download the PDF link + a message about the email status.
- Sys Admin: **"Verify integrity"** → `/audit/verify` → green "Chain valid" / red "Broken at #id".

### FE-7 — ML Monitoring, Registry, Admin, Notifications (FR-MM-001..010)
- `/ml/monitoring`:
  - 6 `MetricCard`s (AUC-ROC, Precision, Recall, F1, Accuracy, FPR) with the target and a green/red badge.
  - Trend line chart (metric selector or small multiples) with a dashed target line; filters for date range + model version.
  - PSI table + bar chart with GREEN/AMBER/RED tags, "Last check: …" and a **Run Drift Check Now** button (spinner, then refresh).
  - Feature importance horizontal bar chart (mean |SHAP|).
- `/ml/models`: versions table (status tags), **Retrain** (confirm → poll status → toast), **Promote** (confirm), α/β form (linked sliders, α+β = 1) + classification thresholds.
- `/admin/users`: table, create/edit modal (role select, active switch). `/admin/config`: forms per §12 key (rule weights, quantity limits, business date override date-picker, report recipients); holidays list.
- **NotificationBell:** poll `/notifications` every 60 s, badge count, dropdown list, mark read, click → link.

### FE-8 — Chat widget (A2)
A floating button (bottom-right, every page) opens a drawer with the message list, an input box, and suggestion chips for the 4 brief questions:
- "How many times did settlement fail for BlackRock?"
- "How many times did settlement fail for INFY?"
- "Which rule was triggered for T110003?"
- "Which trades settle in the next 2 weeks?"

Render `answer` as text, plus a small table if `data` is an array. Generate a `sessionId` per login. Show typing dots and handle errors.

### FE-9 — Polish, tests, deploy
- Loading skeletons, empty states and error states (with a retry button) on every page. Consistent page headers; usable at 1280 px width and up.
- vitest: booking validation, StatusTag colours, menu filtering by permissions, Re-Submit disabled for HIGH, Hide-LOW toggle; optional Playwright e2e "Trader books trade → Ops Manager assigns → Ops applies fix → resubmits".
- `vercel.json` SPA rewrite (`/(.*) → /index.html`), `VITE_API_BASE_URL` = Render URL, `VITE_USE_MOCKS=false`.

## 7. Integration checkpoints (with BE)
| When | What to switch to the real API | Check |
|---|---|---|
| After BE-2 | auth, reference, booking form, trade detail/history | field names match §8.2; 422 errors map inline |
| After BE-4 | dashboard, bulk upload, repair queue | pagination, filters, exports, actions |
| After BE-6 | audit, ML pages, admin, notifications | charts render with real data |
Switch per area using a mock passthrough (MSW `passthrough()` for areas that are live), so you never block on unfinished endpoints.

## 8. Definition of Done (FE module)
- [ ] Every route in §13.1 works for its roles; forbidden ones show 403.
- [ ] All FR-TB, Dashboard FR-RQ, Repair-Queue FR-RQ, FR-AT and FR-MM UI items covered (D ticks them off in TEST_PLAN.md).
- [ ] Runs against the real backend with `VITE_USE_MOCKS=false`; deployed on Vercel.
- [ ] `npm run build` + `npm test` green.

## 9. Session-end ritual
Ask Claude for the PROGRESS.md session entry. Tick the "Frontend wired" column (PROGRESS §2). Commit as `feat(ui): …`. Report any contract gaps to the backend owner and log them in §17.
