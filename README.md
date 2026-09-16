# Rote

**An LLM discovers how to do a task in a legacy back-office application once. Rote compiles what it did into a typed, versioned capability. From then on the capability is replayed deterministically, with no model in the loop.**

Banks and credit unions run many applications that have no API. The only way in is the screen a human operator uses. Rote is the integration layer for that case:

- it lets an LLM **drive the screen** to accomplish a goal written in plain English,
- turns the successful run into a **reviewable artifact** (a "capability") with typed inputs, outputs and business outcomes,
- **replays** that capability for any new input without calling a model, detecting validation errors, missing records, permission denials, pop-ups, session expiry, slow loads and app errors,
- **hands the live session to a human** when it cannot continue safely, then takes control back,
- and keeps **sensitive data out of prompts, logs and screenshots** while staying inside an explicit allowlist.

The target application is **FakeBank Core**, a deliberately legacy core-banking app included in this repo: framesets, table layouts, unlabelled inputs, no test IDs, pop-up overlays, a live clock and switchable faults. All data in it is fake.

| Document | What it covers |
|---|---|
| [`REPORT.md`](REPORT.md) | Design write-up: architecture, artifact schema, determinism and error handling, heterogeneity and multi-tenant, escalation and handoff, safety, cuts and what comes next |
| [`evidence/README.md`](evidence/README.md) | Index of recorded runs: live LLM discoveries, replays, errors, recoveries, human handoff, policy blocks, stability |
| [`evidence/costs.md`](evidence/costs.md) | Discovery cost versus replay cost for every recorded discovery |

---

## Contents

1. [How it works](#how-it-works)
2. [What goals it can handle](#what-goals-it-can-handle)
3. [Setup](#setup)
4. [Run everything without an API key](#run-everything-without-an-api-key)
5. [Demo path: discover a goal, then replay it](#demo-path-discover-a-goal-then-replay-it)
6. [FakeBank test data and fault switches](#fakebank-test-data-and-fault-switches)
7. [Command reference](#command-reference)
8. [Reading a run's evidence](#reading-a-runs-evidence)
9. [Tests and CI](#tests-and-ci)
10. [Repository layout](#repository-layout)
11. [Troubleshooting](#troubleshooting)

---

## How it works

Rote has two halves. The model is only used in the first one.

| | **Discovery** (once per task) | **Replay** (every time after) |
|---|---|---|
| You give | A goal in plain English and a target URL | A capability id and typed inputs, e.g. `member_number=100377` |
| Who decides each step | The LLM, one tool call per turn, choosing only among elements Rote has observed and permitted | Nobody: the recorded steps run in order |
| How elements are found | The model points at a numbered element; code records ranked locators that it verified match exactly that element | The recorded locators, tried in rank order |
| What comes out | A capability JSON file in `catalog/`, validated by replaying it with the same input and a different one | A structured result: `success` with typed outputs, a `business_outcome` such as `MEMBER_NOT_FOUND`, or a `failure` with step, expected, observed and evidence |
| Cost | About $0.10 and 25 seconds per goal with Claude Opus 5 | $0 and 1 to 3 seconds |

A worked example from the recorded evidence:

1. **Discovery.** `rote discover --goal 'What is the current regular savings balance for member 100245?'`. Rote replaces `100245` with a placeholder before the model sees anything. The model clicks **MEMBER INQUIRY**, types the placeholder into **MEMBER #**, clicks **GO** and extracts the **BALANCE** cell in the **REGULAR SAVINGS** row. Rote compiles this into `fakebank.member.get_regular_savings_balance` (input `member_number`, output `regular_savings_balance` of type money, outcomes `MEMBER_NOT_FOUND`, `INVALID_MEMBER_NUMBER`, `ACCOUNT_RESTRICTED`, `NO_REGULAR_SAVINGS`). See `evidence/01a-discovery-live-savings-balance`.
2. **Replay.** `rote replay fakebank.member.get_regular_savings_balance --input member_number=100377` runs the same four steps with no model and returns `{"amount": "1292.40", "currency": "USD"}`. The same command with `999999` returns `business_outcome MEMBER_NOT_FOUND`.

In production the calling AI agent is the one that turns a user's sentence ("what's the savings balance for member 100377?") into "call `get_regular_savings_balance` with `member_number=100377`". Each capability's contract already has the shape of a tool definition for that purpose. Rote itself does not match English to existing capabilities: a new `rote discover` always uses the model. See [`REPORT.md` section 7](REPORT.md#7-cuts) for the planned agent-facing catalog.

---

## What goals it can handle

Discovery accepts **any** goal in plain English. What can actually be achieved is bounded by what the target application offers and by the safety policy.

**FakeBank Core offers:**

| Screen | What is on it |
|---|---|
| Sign on | Operator ID and password (typed by code, never by the model) |
| Main menu | Member inquiry, daily reports, system administration |
| Member inquiry | A member number field and a GO button |
| Member detail | Member name, tax ID, member-since date, a SHARE ACCOUNTS table (type, share ID, balance, status, a CLOSE SHARE button per active share), member notes, TRANS HIST and NEW INQUIRY buttons |
| Transaction history | A TRANSACTION HISTORY table (date, share, description, amount) |
| Daily reports | "Operator not authorized" |
| Administration | Blocked by policy; never reachable by automation |

**What the agent can read** from those screens:

| Kind | Example | Replay locator |
|---|---|---|
| One table cell | Balance of the REGULAR SAVINGS row | `table_cell` keyed by column and row values |
| A whole table | All recent transactions | `table` |
| A value next to a label | MEMBER SINCE date | `field` keyed by the label |

**Goals that have been run live (Claude Opus 5, evidence in `evidence/01a` to `01f`):**

| Goal | Result | Capability |
|---|---|---|
| What is the current regular savings balance for member 100245? | completed, validated | `get_regular_savings_balance` |
| Show recent transactions for member 100245 | completed, validated | `get_member_recent_transactions` (table output) |
| When did member 100377 become a member? | completed, validated | `get_member_since_date` (label–value output) |
| Wire $500 from member 100245 to an external bank account | `GOAL_NOT_ACHIEVABLE` (FakeBank has no wire function) | none |

**Current limits:**

- Goals that need a screen FakeBank does not have end with `GOAL_NOT_ACHIEVABLE`.
- Irreversible actions (anything like CLOSE, DELETE, TRANSFER, SUBMIT) need a human's approval during discovery and are blocked in replay unless the capability declares them.
- Capabilities **retrieve**; callers **reason**. "Which of this member's shares has the highest balance?" should be a capability that returns the share table, and the caller compares.
- Extraction covers table cells, whole tables and label–value fields. Values embedded in free-running paragraphs are not addressable yet.

---

## Setup

**Requirements:** Python 3.11 or newer and about 500 MB of disk for Chromium. Works on Windows, macOS and Linux.

1. Create and activate a virtual environment.

   ```bash
   python -m venv .venv
   ```

   Activate it with `.venv\Scripts\activate` on Windows or `source .venv/bin/activate` on macOS and Linux.

2. Install Rote with the test tools and both model providers.

   ```bash
   pip install -e ".[dev,anthropic,gemini]"
   ```

3. Install the browser Playwright drives.

   ```bash
   python -m playwright install chromium
   ```

4. Check the environment. `rote doctor` loads the catalog and policy, launches Chromium and reports which environment variables are set (never their values).

   ```bash
   rote doctor
   ```

### API keys and configuration

A model key is needed **only for live discovery**. Replays, the demo and all tests run without one. There is no `.env.example`.

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Live discovery with Claude (`--provider anthropic`, the default; default model `claude-opus-5`) |
| `GEMINI_API_KEY` | Live discovery with Gemini (`--provider gemini`; default model `gemini-2.5-flash`) |
| `PLAYWRIGHT_BROWSERS_PATH` | Optional. Only if Chromium was installed somewhere other than Playwright's default cache |
| `FAKEBANK_OPERATOR_ID`, `FAKEBANK_OPERATOR_PASSWORD` | Only when you start FakeBank yourself with `rote app start` |
| `ROTE_FAKEBANK_OPERATOR_ID`, `ROTE_FAKEBANK_OPERATOR_PASSWORD` | Only when Rote should use a FakeBank you started yourself (same values as above) |

Set the model key in your shell, or put it in a `.env` file at the project root. The file is gitignored, Rote reads only the two model key names from it, and a variable already set in your environment takes precedence:

```
ANTHROPIC_API_KEY=sk-ant-...
```

**FakeBank credentials are generated per run.** Whenever Rote starts FakeBank itself (the default), it creates a random operator ID and password, passes them to FakeBank in memory and types them on the SIGN ON screen from code. Nothing credential-like is stored in the repository or in any run record.

---

## Run everything without an API key

```bash
rote demo
```

This starts FakeBank on free ports with fresh credentials and runs every scripted scenario end to end:

- scripted discovery of three goals (savings balance, recent transactions, member-since date), each compiled and validated, plus the impossible wire transfer;
- replays with different members and a table output;
- every business outcome (not found, invalid number, restricted, no savings share, no transactions);
- every recoverable condition (maintenance notice, slow load, session expiry);
- every hard failure (bad input, unknown pop-up, server error, operator not authorized, load beyond the time limit);
- a human handoff played by a test operator bot;
- the policy demonstrations (a misled agent and a tampered capability);
- five consecutive replays for stability.

Results are written to `evidence/`, including `evidence/README.md` and `evidence/costs.md`. It takes about three minutes. Live LLM runs (`evidence/01a-` onwards) are never overwritten by the demo.

The "scripted" planner replays pre-written decisions through exactly the same discovery loop, gateway, compiler and validation as a live model. It exists so that the whole pipeline can be tested and demonstrated offline.

---

## Demo path: discover a goal, then replay it

Every `rote discover`, `rote replay` and `rote validate` command below starts FakeBank automatically on `http://127.0.0.1:8700` if nothing is running there.

> **Quoting:** wrap goals in single quotes. In both bash and PowerShell, double quotes would turn `$500` into an empty variable.

### Step 1. Discover a capability from a plain-English goal (live model)

```bash
rote discover --target http://127.0.0.1:8700 --goal 'What is the current regular savings balance for member 100245?'
```

What happens and what you will see:

1. Rote prints the values it detected in the goal (`value_1 = 100245 (digits)`) and asks you to confirm. `--yes` skips the question.
2. It signs on with the generated credentials, then runs the observe → decide → act loop. Add `--headed` to watch the browser.
3. It prints `Discovery completed`, the number of model calls, tokens and cost, and the run folder under `runs/`.
4. It prints a **compile report**: capability name and version, inputs (named after the field each value was typed into, here `member_number`), outputs, wired outcomes, and locator robustness.
5. It replays the new capability twice as **validation**: once with the discovery input and once with a different test member. Both must succeed for the status to become `validated`.
6. It saves the capability to `catalog/` and prints a plain-language description of it.

More goals to try:

```bash
rote discover --goal 'Show recent transactions for member 100245' --yes
```

```bash
rote discover --goal 'When did member 100377 become a member?' --yes
```

```bash
rote discover --goal 'Wire $500 from member 100245 to an external bank account' --yes
```

The last one ends cleanly with `GOAL_NOT_ACHIEVABLE`: FakeBank has no wire function.

Useful options: `--model claude-sonnet-5` (cheaper), `--provider gemini`, `--headed` (watch the browser), `--max-cost-usd 0.50`, `--max-steps 30`, `--max-minutes 10`, `--name get_savings_balance` (choose the capability name instead of letting the agent propose one).

Without an API key, the same flow runs with the scripted planner:

```bash
rote discover --planner scripted --script scripts/get_savings_balance.json --goal 'What is the current regular savings balance for member 100245?' --yes
```

To keep a live run as evidence:

```bash
rote evidence save runs/<run_id> 01g-discovery-live-my-goal
```

### Step 2. Review the capability

```bash
rote catalog list
```

```bash
rote describe fakebank.member.get_regular_savings_balance
```

`describe` prints what the capability does, what it needs, what it returns, which business outcomes it may return instead, each step with its expected screens, app-wide handling, the success condition, locator robustness and provenance.

### Step 3. Replay it deterministically (no model)

Use the capability id printed at the end of `rote discover`. The agent proposes the name, so a new run of your own may pick a different one; `rote catalog list` shows them all.

```bash
rote replay fakebank.member.get_regular_savings_balance --input member_number=100377
```

The result is JSON:

```json
{
  "invocation_id": "rep_20260915_153551_b404",
  "capability": "fakebank.member.get_regular_savings_balance@1.0.0",
  "content_hash": "sha256:…",
  "mode": "unattended",
  "status": "success",
  "outputs": { "regular_savings_balance": { "amount": "1292.40", "currency": "USD" } },
  "outcome": null,
  "error": null,
  "recoveries": [],
  "warnings": [],
  "interventions": [],
  "ui_touched": true,
  "side_effects_possible": false,
  "timing_ms": { "total": 2983 },
  "evidence": "runs/rep_20260915_153551_b404"
}
```

| Field | Meaning |
|---|---|
| `status` | `success`, `business_outcome` or `failure` |
| `outputs` | Typed outputs (present on success) |
| `outcome` | The business outcome code and description, e.g. `MEMBER_NOT_FOUND` |
| `error` | For failures: code, phase, step, expected, observed and evidence paths |
| `recoveries` | Conditions Rote handled on its own, e.g. `SESSION_RENEWED_RESTARTED` |
| `warnings` | Signals such as `LOCATOR_FALLBACK_USED` (drift telemetry) |
| `interventions` | Human interventions that happened during the run |
| `ui_touched` | `false` when the input was rejected before the application was touched |
| `side_effects_possible` | `true` only if an irreversible step may have taken effect |

The capabilities created by `rote demo` (`get_savings_balance`, `recent_transactions`, `get_member_since`) work the same way, so the next steps also run without an API key.

### Step 4. Errors and exceptional states

| Command | Condition | Expected result |
|---|---|---|
| `rote replay fakebank.member.get_savings_balance --input member_number=999999` | Record not found | `business_outcome MEMBER_NOT_FOUND` |
| `rote replay fakebank.member.recent_transactions --input member_number=55010` | Application-side validation error | `business_outcome INVALID_MEMBER_NUMBER` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100733` | Permission denial (restricted record) | `business_outcome ACCOUNT_RESTRICTED` |
| `rote replay fakebank.member.get_savings_balance --input member_number=12AB` | Input violates the contract | `failure INVALID_INPUT`, `ui_touched: false` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault notice=once` | Maintenance notice pop-up | `success`, recovery `SYSTEM_NOTICE_DISMISSED` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault latency.member_detail=10000` | Slow load (10 s, step timeout 6 s) | `success`, recovery `SLOW_LOAD_WAITED` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault session_expire_after=2` | Session expires mid-flow | `success`, recovery `SESSION_RENEWED_RESTARTED` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault unknown_popup=once` | Unknown pop-up, unattended | `failure UNEXPECTED_SCREEN` with evidence |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault error500.member_detail=once` | Server error page | `failure APP_ERROR` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault unauthorized.member_detail=once` | Operator role lacks the function | `failure OPERATOR_NOT_AUTHORIZED` |
| `rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault latency.member_detail=20000` | Load beyond the extended time limit | `failure CHECKPOINT_TIMEOUT` |

Failures include the step, what was expected, what was observed, and paths to a masked screenshot, a masked page snapshot and the event log.

### Step 5. A human takes over the live session

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100245 --fault supervisor_override=once --attended --headed
```

1. A browser window opens and the replay runs until a **SUPERVISOR OVERRIDE REQUIRED** box appears.
2. Rote pauses and prints an operator page link (default `http://127.0.0.1:8765`). Open it: the intervention shows the capability, step, reason and a masked screenshot.
3. Click **Take control**. Automation is now locked out of the browser.
4. In the **same browser window**, type any supervisor password into the box and click **OK**.
5. Click **Resume** on the operator page. Rote checks that the application is on a screen it expects, records what you did (values masked), and finishes the run with `success`.

Without `--attended`, the same condition fails immediately with `HUMAN_REQUIRED` and the intervention request attached, so an unattended caller can route it.

### Step 6. Stability

```bash
rote replay fakebank.member.get_savings_balance --input member_number=100377 --repeat 5
```

Prints every result and a summary such as `Stability over 5 runs: {'success': 5} · mean 1.52 s · max 1.70 s`.

---

## FakeBank test data and fault switches

### Members

| Member number | Situation | Useful for |
|---|---|---|
| `100245` | Regular savings and share draft, many transactions | Discovery goals, happy path |
| `100377` | Regular savings and share draft, three transactions | Replays with a different member, validation |
| `100512` | Share draft only, no transactions | `NO_REGULAR_SAVINGS`, `NO_TRANSACTIONS` |
| `100733` | Restricted record | `ACCOUNT_RESTRICTED` |
| `100901` | Member notes contain a prompt injection | Prompt-injection and misled-agent demonstrations |
| `999999` | Does not exist | `MEMBER_NOT_FOUND` |
| `55010` | Only five digits | `INVALID_MEMBER_NUMBER` |

### Fault switches

Pass them with `--fault key=value` on `rote discover` or `rote replay`, or set them on a running FakeBank with `rote app faults set key=value`. Screens are `menu`, `member_inquiry`, `member_detail` and `member_transactions`.

| Switch | Effect |
|---|---|
| `notice=once` or `notice=always` | Maintenance notice overlay with an OK button |
| `supervisor_override=once` | Supervisor password overlay on the next member detail page |
| `unknown_popup=once` | An overlay no profile knows about on the next member detail page |
| `latency.<screen>=<ms>` | That screen responds after the given delay |
| `session_expire_after=<n>` | The session expires after n more requests |
| `error500.<screen>=once` | That screen returns a server error once |
| `unauthorized.<screen>=once` | That screen shows "operator not authorized" once |

The fault switches live on a separate control port that is not in the policy allowlist, so automation can never reach them.

---

## Command reference

| Command | What it does | Main options |
|---|---|---|
| `rote doctor` | Checks Python, catalog, policy, Chromium and which keys are set | |
| `rote discover` | LLM discovery of a goal, then compile, validate and save | `--goal` (required), `--target`, `--name`, `--input name=value[:type]`, `--output name:type`, `--planner llm\|scripted`, `--provider anthropic\|gemini`, `--model`, `--script`, `--max-steps`, `--max-minutes`, `--max-cost-usd`, `--yes`, `--no-detect`, `--headed`, `--validate-with name=value`, `--no-save`, `--fault` |
| `rote replay <id>[@version]` | Deterministic replay; prints the result JSON | `--input name=value`, `--target`, `--attended`, `--headed`, `--repeat N`, `--fault`, `--json` |
| `rote validate <id>` | Validation replays with the same and a different input; marks the capability validated | `--input`, `--other` |
| `rote describe <id>` | Plain-language review of a capability | |
| `rote catalog list` | Lists every capability with version, status and summary | |
| `rote demo` | Runs every scripted scenario and regenerates `evidence/` | `--headed` |
| `rote evidence save <run_dir> <name>` | Copies a run into `evidence/<name>` | |
| `rote evidence costs` | Regenerates `evidence/costs.md` | |
| `rote app start` | Runs FakeBank in the foreground (needs the `FAKEBANK_OPERATOR_*` variables) | `--port`, `--control-port`, `--host` |
| `rote app reset` | Restores seed data and clears faults | |
| `rote app faults set key=value …` / `rote app faults clear` | Sets or clears fault switches on a running FakeBank | |
| `rote schema export` | Writes JSON Schemas for capability, product profile, policy, result and intervention to `schemas/` | |

To click around FakeBank yourself, start it with a login you choose and open `http://127.0.0.1:8700`:

```bash
FAKEBANK_OPERATOR_ID=demo FAKEBANK_OPERATOR_PASSWORD=demo123 rote app start
```

In PowerShell: `$env:FAKEBANK_OPERATOR_ID="demo"; $env:FAKEBANK_OPERATOR_PASSWORD="demo123"; rote app start`. Stop it before running `rote replay` or `rote discover`, or give Rote the same values in `ROTE_FAKEBANK_OPERATOR_ID` and `ROTE_FAKEBANK_OPERATOR_PASSWORD`.

---

## Reading a run's evidence

Every run writes a folder under `runs/` (gitignored). Curated copies live in `evidence/`.

Screenshots from the first four live runs (`evidence/01a` to `01d`) show FakeBank before a colour-only restyle. Those records were kept exactly as they were captured; the page structure Rote reads did not change, and the `01a` replays were re-run on the new look with the same results. `evidence/README.md` explains this.

| File | Contents |
|---|---|
| `timeline.md` | **Start here.** A readable step-by-step story of the run: what the agent or replay did and why, screens matched, policy decisions, recoveries, interventions |
| `events.jsonl` | The structured log: one JSON event per line with sequence number, actor (`agent`, `automation`, `human`, `session`, `system`), type and masked data |
| `run.json` | Summary: kind, status, code, duration, model usage and cost, inputs as placeholders |
| `result.json` | Replays only: the result contract shown above |
| `snapshots/NN.txt` | The masked text view of each screen exactly as the model saw it (discovery) |
| `screens/*.png` | Masked screenshots at each checkpoint |
| `failure/` | Failures only: masked screenshot and snapshot at the moment of failure |
| `interventions/<id>/` | Escalations: the intervention request and screenshots before and after the human |
| `compile/` | Discoveries only: the compile report and the compiled capability |

---

## Tests and CI

```bash
pytest tests/unit -q
```

```bash
pytest tests/integration -q
```

- **Unit tests** (seconds) cover input detection, masking, policy classification, locator and schema models, typed parsing, the in-doubt rule, label–value fields, the `.env` loader, and a check that replay code imports nothing from the agent.
- **Integration tests** (about three minutes) start their own FakeBank and browser. They cover every runtime condition named in the brief, discovery → compile → validation for cell, table and label–value outputs, the human handoff (a test operator bot plays the human through the operator page API), the policy demonstrations, blocked-navigation feedback to the agent, and a **PII canary** check that no seeded name, tax ID or balance appears in any file under `runs/`, `catalog/` or `evidence/`.
- **CI** (`.github/workflows/ci.yml`) runs both suites on Ubuntu with Python 3.12 on every push and pull request. No API key is needed.

---

## Repository layout

```
catalog/fakebank-core/        product profile and capabilities (versioned JSON)
policies/                     allowlist and risk rules, owned separately from capabilities
schemas/                      JSON Schemas exported from the models
scripts/                      scripted-planner scripts (offline discovery, misled-agent demo)
evidence/                     curated run records (see evidence/README.md)
fakebank/                     the target application: app, seed data, fault switches, HTML templates
src/rote/
  cli.py                      command line
  runtime.py                  wires FakeBank, browser, policy, broker and operator page for one session
  config.py                   paths, ports, environment variable names, .env loader
  demo.py                     `rote demo` and the evidence index
  gateway.py                  the one path every automated action takes
  agent/                      discovery loop, input detection, tool menu, prompts
  agent/planners/             Claude, Gemini and scripted planners
  surface/                    Playwright surface, in-page walker (perception and locator resolution), snapshot model
  perception/                 masking at the model and persistence boundaries
  policy/                     allowlist and runtime risk classification
  replay/                     replay engine (steps, recovery, escalation), screen matcher, typed parsing, input pre-flight
  compiler/                   run record → capability, validation replays
  registry/                   catalog storage, content hashes, `describe`
  handoff/                    control broker, operator page, test operator bot
  evidence/                   run recorder and timeline writer
  models/                     capability, product profile, policy, result, intervention and event models
tests/unit/                   fast tests without a browser
tests/integration/            end-to-end tests against a live FakeBank
.github/workflows/ci.yml      CI
```

---

## Troubleshooting

- **`rote doctor` says Chromium is missing:** run `python -m playwright install chromium`. If browsers live in a custom folder, set `PLAYWRIGHT_BROWSERS_PATH` to it in every new terminal.
- **Port 8700 already in use:** Rote uses the running instance and then needs `ROTE_FAKEBANK_OPERATOR_ID` and `ROTE_FAKEBANK_OPERATOR_PASSWORD`. Stop the other instance to let Rote start its own. `rote demo` always picks free ports.
- **`ANTHROPIC_API_KEY: not set`:** set it in the same terminal, or add it to `.env` at the project root.
- **The goal lost its dollar amount:** use single quotes around the goal.
- **Discovery pauses with `>>> INTERVENTION`:** the agent is stuck or asked for help. Open the printed operator page link to take control, or wait: an unanswered intervention expires after 15 minutes and the run ends with `HUMAN_TIMEOUT`.
- **A replay fails with `PROFILE_MISMATCH`:** `catalog/fakebank-core/profile.json` changed after the capability was compiled, and replay refuses to run against a different profile. Undo the profile change, or run discovery for that goal again so the capability is recompiled against the new profile.
- **A replay fails with `TARGET_NOT_FOUND` after you changed FakeBank's labels:** the recorded locators no longer match. Re-run discovery for that goal. `REPORT.md` section 4.3 describes how tenant overlays would handle this in production.
