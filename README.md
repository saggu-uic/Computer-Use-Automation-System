# Rote

**An LLM discovers how to do a task in a legacy back-office application once. Rote compiles what it did into a typed, versioned capability. From then on the capability is replayed deterministically, with no model in the loop.**

Rote is the integration layer for applications that have no API: it drives the screen the way an operator would, turns a successful run into a reviewable artifact, replays it with typed inputs and outputs, separates business outcomes from recoverable conditions and hard failures, hands the live session to a human when it cannot continue safely, and keeps sensitive data out of prompts, logs and screenshots.

The target is **FakeBank Core**, a deliberately legacy core-banking app included in this repo (framesets, table layouts, unlabelled inputs, no test IDs, overlays, a live clock, and fault injection). All data is fake.

- Design write-up: [`REPORT.md`](REPORT.md)
- Evidence of discovery and replay runs: [`evidence/README.md`](evidence/README.md)

---

## Setup

Requirements: Python 3.11+ and about 500 MB for Chromium. Works on Windows, macOS and Linux.

```bash
python -m venv .venv
```

Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere), then:

```bash
pip install -e ".[dev,anthropic,gemini]"
```

```bash
python -m playwright install chromium
```

```bash
rote doctor
```

### Environment variables

There is no `.env.example`. The only variable you need is a model key, and only for **live** discovery:

| Variable | When |
|---|---|
| `ANTHROPIC_API_KEY` | Live discovery with Claude (`--provider anthropic`, the default) |
| `GEMINI_API_KEY` | Live discovery with Gemini (`--provider gemini`) |

Set the key in your shell, or put it in a `.env` file at the project root (gitignored), one `NAME=value` per line:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Rote reads only these two names from `.env`, and a variable already set in your environment takes precedence. `rote doctor` shows whether each key is set.

FakeBank credentials are **generated per run** and passed to FakeBank in memory whenever Rote starts it (the default). Nothing credential-like is in the repo. Only if you start FakeBank yourself (`rote app start`) do you set `FAKEBANK_OPERATOR_ID` / `FAKEBANK_OPERATOR_PASSWORD` for FakeBank and the same values in `ROTE_FAKEBANK_OPERATOR_ID` / `ROTE_FAKEBANK_OPERATOR_PASSWORD` for Rote.

---

## Run everything without any API key

```bash
rote demo
```

Starts FakeBank on free ports, runs scripted discovery, compile and validation, and every replay scenario (success, business outcomes, recoveries, failures, human handoff, policy blocks, stability). The results go into `evidence/`. Takes about three minutes.

---

## Demo path

Every `rote discover` / `rote replay` below starts FakeBank automatically on `http://127.0.0.1:8700` if nothing is running there.

### 1. Discover from a plain-English goal (live model)

```bash
rote discover --target http://127.0.0.1:8700 --goal "What is the current regular savings balance for member 100245?"
```

```bash
rote discover --target http://127.0.0.1:8700 --goal "Show recent transactions for member 100245"
```

```bash
rote discover --target http://127.0.0.1:8700 --goal 'Wire $500 from member 100245 to an external bank account'
```

Rote detects `100245` (and `$500`) in the goal, asks you to confirm, replaces them with placeholders before anything reaches the model, runs the observe → decide → act loop, compiles the run into a capability named after the field each value was typed into (`member_number`), replays it twice to validate it (same member, different member), and saves it under `catalog/`. The third goal ends cleanly with `GOAL_NOT_ACHIEVABLE` (`evidence/01c-…`). An earlier live attempt at it ended in a human handoff instead and is kept as `evidence/01d-…`; `evidence/README.md` explains what it exposed and what was fixed.

Single quotes around the wire goal stop bash and PowerShell from expanding `$500`.

Useful options: `--model claude-sonnet-5` (cheaper), `--provider gemini`, `--headed` (watch the browser), `--yes` (skip the confirmation), `--max-cost-usd 0.50`.

Without a key, the same flow runs with a scripted planner:

```bash
rote discover --planner scripted --script scripts/get_savings_balance.json --goal "What is the current regular savings balance for member 100245?" --yes
```

To keep a live run as evidence:

```bash
rote evidence save runs/<run_id> 01a-discovery-live-savings-balance
```

### 2. Review the capability

```bash
rote describe fakebank.member.get_savings_balance
```

```bash
rote catalog list
```

### 3. Replay deterministically (no model)

Replay the capability that discovery just saved. The agent proposes its name, so use the id printed at the end of `rote discover` (or `rote catalog list`). The live run in `evidence/01a-…` produced `get_regular_savings_balance`:

```bash
rote replay fakebank.member.get_regular_savings_balance --input member_number=100377
```

The examples below use `get_savings_balance`, which `rote demo` creates, so they work without an API key.

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100377
```

The result is JSON: `success` with typed outputs (`{"amount": "1292.40", "currency": "USD"}`), `business_outcome` with a code, or `failure` with step, expected, observed and evidence paths, plus `recoveries`, `interventions`, `ui_touched` and `side_effects_possible`.

### 4. Errors and exceptional states

```bash
rote replay fakebank.member.get_savings_balance --input member_number=999999
```

```bash
rote replay fakebank.member.recent_transactions --input member_number=55010
```

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault notice=once
```

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault latency.member_detail=10000
```

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault session_expire_after=2
```

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault unknown_popup=once
```

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault error500.member_detail=once
```

Those are, in order: not found, application-side validation error, maintenance notice (recovered), slow load (recovered), session expiry (re-login and restart), unknown popup (failure with evidence), and server error.

### 5. Human takes over the live session

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault supervisor_override=once --attended --headed
```

When the supervisor override appears, Rote pauses, shows an intervention with a masked screenshot on the operator page (the link is printed, default `http://127.0.0.1:8765`), and waits. Click **Take control**, type any supervisor password into the dialog **in the same browser window**, click OK, then **Resume** on the operator page. Rote records what you did, checks that the application is on the screen it expects, and finishes the run.

---

## Tests

```bash
pytest tests/unit -q
```

```bash
pytest tests/integration -q
```

Integration tests start their own FakeBank and browser and cover every runtime condition named in the brief, discovery → compile → validation, the human handoff (a test operator bot plays the human through the operator page API), the policy demos, and a **PII canary** check that no seeded name, tax ID or balance appears in any persisted file. CI runs both on Linux (`.github/workflows/ci.yml`).

---

## Repository map

```
catalog/fakebank-core/   product profile + capabilities (JSON, versioned)
policies/                allowlist and risk rules (owned separately from artifacts)
schemas/                 JSON Schemas exported from the models
scripts/                 scripted-planner scripts (offline discovery, misled-agent demo)
src/rote/
  surface/               Playwright surface + in-page walker (perception, locators, human capture)
  perception/            masking at the model and persistence boundaries
  gateway.py             the one path every automation action takes
  policy/                allowlist and risk classification
  replay/                deterministic replay engine, matcher, typed parsing, pre-flight
  agent/                 discovery loop, input detection, tool menu, planners (Claude, Gemini, scripted)
  compiler/              run record -> capability, validation replays
  handoff/               control broker, operator page, test operator bot
  evidence/              run recorder and timelines
  cli.py, demo.py
fakebank/                the target application
evidence/                committed evidence (see evidence/README.md)
```

Run records are written to `runs/` (git-ignored); curated copies live in `evidence/`.

## Troubleshooting

- **Port 8700 already in use:** Rote uses the running instance and needs `ROTE_FAKEBANK_OPERATOR_ID` / `ROTE_FAKEBANK_OPERATOR_PASSWORD`. Stop it to let Rote start its own, or use `rote demo`, which always picks free ports.
- **Chromium not found:** run `python -m playwright install chromium`. Set `PLAYWRIGHT_BROWSERS_PATH` if browsers live somewhere other than the default cache.
