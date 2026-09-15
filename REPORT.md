# Rote: design report

Rote lets an AI agent operate back-office applications that have no API. An LLM **discovers** a task once by driving the screen, Rote **compiles** the run into a typed, versioned capability, and every later call is a **deterministic replay** with no model involved. The target is FakeBank Core, a deliberately legacy app built for this project (framesets, layout tables, unlabelled inputs, no test IDs, overlays, a live clock, fault injection).

The principle behind most decisions: **the model proposes, code disposes.** The LLM only chooses among elements that code has observed, masked and permitted; identity, verification, safety and recording are plain code.

## 1. Architecture

```
goal + target ─► DISCOVERY (LLM picks one tool per turn) ─► run record ─► COMPILER ─► capability (JSON)
                                                                                        │ validation replays
agent call ─► REPLAY (no LLM: verified locators + screen checks) ─► success | business_outcome | failure
                  └─ stuck / unknown screen / risky step ─► HUMAN takes over the same live browser ─► resume
```

- **One Python process, one asyncio loop** runs Playwright, the operator page (FastAPI) and the control broker, so pausing for a human is an `await`. FakeBank is a separate process. Storage is plain files (JSON, JSONL, masked PNGs).
- **One Action Gateway.** Every automated action, from the LLM or from an artifact, passes: control check → risk classification → policy → approval → act → verify effect → masked event. In-page listeners record human actions into the same event model.
- **Perception is a text snapshot of roles, names and labels, not screenshots.** Coordinates don't replay, images can't be masked reliably, and the same concepts exist on desktop. Playwright's built-in AI snapshot was tried first but misses bold header rows, unlabelled inputs and full-screen overlays, so a small in-page walker builds the snapshot and the *same* rules resolve locators at replay. Playwright does the clicking, waiting, dialogs, network blocking and screenshots.
- **Agent loop.** `rote discover --target URL --goal "..."`. Code swaps values in the goal (member numbers, amounts, dates) for placeholders before the model sees it, and they become typed inputs. Each turn the model gets the goal, the masked snapshot, the last result and recent history, and calls exactly one tool from a fixed menu (click, type, extract, done, cannot_complete, ask_human, …). It stops on done, cannot_complete, max steps, timeout, cost budget or escalation. A scripted planner runs the same loop offline.
- **Model: Claude Opus 5** (Gemini is also supported). Discovery runs once per capability, so reliable tool use is worth paying for: each successful live discovery cost $0.10–0.12, and all four live runs $0.45 (`evidence/costs.md`). Replay never calls a model.
- *Rejected:* multiple services (no value at this size), screenshot agents (not replayable), LLM-written selectors (unverifiable), YAML (loses types such as `012345`).

## 2. Artifact schema

Two layers live in [`catalog/fakebank-core`](catalog/fakebank-core), with JSON Schemas in [`schemas/`](schemas):

- **Product profile**, once per vendor product: sign-on (code only, credentials as `{{secret:…}}`), app-wide screens (notice, supervisor override, session expiry, server error, not authorized, loading), a screen catalog and field classifications.
- **Capability**: one self-contained flow with a **contract** and an **implementation**.

```jsonc
"contract": {
  "inputs":   { "member_number": { "type": "string", "pattern": "^[0-9]+$", "classification": "pii" } },
  "outputs":  { "savings_balance": { "type": "money", "classification": "financial" } },
  "outcomes": [ { "code": "MEMBER_NOT_FOUND" }, { "code": "ACCOUNT_RESTRICTED" }, { "code": "INVALID_MEMBER_NUMBER" } ],
  "side_effects": "none", "idempotent": true }
"steps": [ { "id": "click_go_button", "action": "click", "target": "go_button",
             "expect": [ { "screen": "member_detail", "then": "continue" },
                         { "screen": "member_not_found", "then": { "outcome": "MEMBER_NOT_FOUND" } } ] } ]
"screens": { "member_detail": { "match": [ { "heading_contains": "MEMBER DETAIL" }, { "heading_contains": "{{member_number}}" } ] } }
```

Why this shape:
- **The contract is all a calling agent needs** and maps directly to a tool definition; steps can change without breaking callers.
- **Business outcomes are declared return values**, not exceptions.
- **Every screen-changing step lists what may appear next**, and that list is the checkpoint. The right-record check (`{{member_number}}` in the heading) prevents reading a stale page for another member.
- **Targets are ranked locator lists** (role, row-keyed table cell, label, nearby text, text; CSS only as a flagged fallback), each verified at record time to match exactly the element the model chose. Every target needs at least one verified durable locator.
- **Typed outputs** include `money` (`{"amount": "4210.55", "currency": "USD"}`) and `table`. Classifications drive masking.
- **Version, `draft → validated`, content hash and profile hash** are recorded in every run. Validation replays a new capability with the discovery input *and* a different one, and `rote describe` prints a plain-language review.

Discovery fills steps, locators, inputs (named after the field each value was typed into), outputs and outcome wiring; the profile supplies app-wide errors; a reviewer approves proposed names. Data-dependent outcomes (a member with no transactions) need a test input that shows them.

## 3. Determinism & error handling

- **Replay imports nothing from the agent** (enforced by a test).
- **Locators are tried in rank order.** Two durable locators resolving to *different* elements give `TARGET_AMBIGUOUS`; Rote never silently clicks a fallback. A used fallback raises `LOCATOR_FALLBACK_USED`, which doubles as drift telemetry.
- **Waiting is condition-based** (Playwright actionability, then screen predicates), never "wait until the page is quiet", because legacy apps with clocks never go quiet.
- **Recovery is bounded** (at most 3 per run): dismiss a known notice, extend a slow load once, or sign on again and restart after session loss, *only if no irreversible step was attempted*.
- **In-doubt rule:** an irreversible step with no confirmation returns `OUTCOME_UNKNOWN` with `side_effects_possible: true` and is never retried.

**Result contract.** Three statuses, by who acts next: `success` (the caller uses the outputs), `business_outcome` (the caller decides), `failure` (an engineer or operator acts). Recoveries are listed, not a status. Failures carry the phase, step, expected, observed and evidence (masked screenshot, snapshot, events).

| Brief condition | FakeBank trigger | Result |
|---|---|---|
| Record not found | member 999999 | `business_outcome MEMBER_NOT_FOUND` |
| Validation error | 5-digit member # | `business_outcome INVALID_MEMBER_NUMBER` |
| Permission denial | restricted record · bot role lacks the function | `business_outcome ACCOUNT_RESTRICTED` · `failure OPERATOR_NOT_AUTHORIZED` |
| Unexpected dialog | notice · unknown popup · supervisor override | recovered · `failure UNEXPECTED_SCREEN` · human handoff |
| Session timeout | session expires mid-flow | recovered `SESSION_RENEWED_RESTARTED` |
| Slow / failed load | 10 s · 20 s · HTTP 500 | recovered `SLOW_LOAD_WAITED` · `CHECKPOINT_TIMEOUT` · `APP_ERROR` |

A restricted member is a fact the caller needs; a bot role lacking a function needs an administrator. Evidence is in `evidence/04` to `07`. **Stretch goal taken: multi-run stability** (`--repeat N`; 5/5 in `evidence/09-stability`).

## 4. Heterogeneity & multi-tenant

**Surface seam.** Contract, steps and screen predicates are surface-neutral (roles, names, labels, tables, dialogs); only locator kinds and the `Surface` implementation change. FakeBank covers legacy web. Designed adapters: **Windows desktop** through UI Automation (ControlType, Name, AutomationId and grid patterns map to `role`, `label`, `table_cell`); **mainframe terminals** through the emulator field buffer (`field_at(row, col)` locators, keyboard-unlock waits); **pixel-only remote desktops** through OCR anchors, with more escalation.

**Reuse across tenants.**
- Agents bind to an **interface** (`member.get_savings_balance`) that each vendor product implements.
- **One base capability per product** plus a **bounded tenant overlay** that may patch locators, screen predicates, labels and timeouts, but **never** the contract, step order or risk.
- **Onboarding is a conformance run**, not re-recording: replay the read-only capabilities with a tenant test member, then repair only the failing targets (the LLM proposes one element, code verifies it, a reviewer approves the overlay). Cost scales with differences, not tenants × capabilities.
- **Drift detection:** a version probe at sign-on, fallback-locator rates, ambiguity, screen near-misses and canary replays, with demotion from unattended to attended.

None of this plumbing is built; name normalisation already absorbs trivial label differences.

## 5. Escalation & handoff

**Detecting "stuck".** In discovery: `ask_human`, two actions with no effect, the same screen three times, repeated invalid or blocked decisions, or an irreversible action. In replay: a needs-human screen (supervisor override) or an unknown screen in attended mode. Unattended replay doesn't wait: it fails with `HUMAN_REQUIRED` and attaches the request.

**Intervention request:** the goal or capability, step, reason, masked screenshot and summary, and a **resume expectation** (the screens automation will accept afterwards).

**Control model:** `AUTOMATION → WAITING_FOR_HUMAN → HUMAN → VERIFYING → AUTOMATION`, enforced in code. The gateway refuses automated actions unless the state is `AUTOMATION`, and every transition is logged with actor and time.

**Same live session.** Attended runs use a visible browser. The operator clicks **Take control** on the operator page and works in *that* window, with the same cookies, frames and state; in-page listeners record their clicks and field changes (values masked). On **Resume**, automation re-checks the expectation before continuing. Irreversible actions are approved or rejected instead, without handing over the browser.

**Tested, and seen live.** A test operator bot performs a full takeover (`evidence/07`). The first live wire-transfer run also escalated for real: the network guard blocked `ADMIN`, but the model saw only a blank page and looped into `STUCK_LOOP` (`evidence/01d`). Blocked navigation is now explained to the agent, with a test; the rerun ended `GOAL_NOT_ACHIEVABLE` (`evidence/01c`).

Discovery runs in which a human performed flow steps are **not compiled** (`HUMAN_STEPS_NOT_COMPILABLE`), because a capability silently depending on unrecorded steps would be worse.

## 6. Safety

- **Allowlist** (`policies/`): the app origin, blocked areas (`/cgi/ADMIN*`) and permitted action types, enforced at the gateway **and** the network layer, which also catches redirects and human clicks.
- **Risk** is classified on the **live element at runtime** (`CLOSE|DELETE|TRANSFER|SUBMIT…`, confirmation dialogs; unknown form submits fail closed). Discovery asks a human to approve irreversible actions. Replay requires approval for declared ones and **blocks undeclared ones, whatever the artifact claims**: a tampered capability gets `POLICY_BLOCKED` (`evidence/08`).
- **Data.** The model sees placeholders and stable tokens (`«MONEY_1»`) instead of names, tax IDs, account IDs, balances and notes, and never sees screenshots or credentials; it finds *where* data is and `extract` reads it. Everything persisted passes a redactor, screenshots black out sensitive values, and a test checks that no seeded PII appears in any file.
- **Secrets:** FakeBank credentials are generated per run and kept in memory; the model key comes from the environment or a gitignored `.env`.
- **Prompt injection:** a member note containing an injection is masked as untrusted text. A scripted misled agent's `CLOSE SHARE` is rejected and its `ADMIN` visit blocked.

**Limits:** free-text PII outside classified fields can slip through; the model still sees masked structure; regex risk rules can misclassify (failing closed costs approvals, not unsafe clicks); the operator page has no authentication; real data would need a no-training agreement with the model provider.

## 7. Cuts

| Cut | Why | Instead |
|---|---|---|
| Approval lifecycle, unattended pre-authorisation | Stretch goal | `draft → validated` plus hashes |
| A write capability (open a share account) | Would double the project | A real irreversible `CLOSE SHARE` for the policy demos |
| Tenant overlays, onboarding, drift telemetry | Design-only in the brief | Section 4 |
| Desktop, terminal and vision adapters | Design-only | The `Surface` seam and locator kinds |
| Remote operator console, routing, operator auth | Out of scope | A local operator page with a JSON API |

**Next:** approval gating for unattended writes, a tenant-overlay demo, an agent-facing capability catalog, and a remote operator console.
