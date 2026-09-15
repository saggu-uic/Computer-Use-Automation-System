# Discovery vs replay cost

| Run | Result | Planner | LLM calls | Tokens | Cost (USD) | Discovery time | Replay (every call) |
|---|---|---|---|---|---|---|---|
| `01-discovery-scripted-goal-not-achievable/discovery` | GOAL_NOT_ACHIEVABLE | scripted/scripted-wire-transfer-not-possible | 2 | 0 | 0.0000 | 4.3 s | no capability |
| `01-discovery-scripted-recent-transactions/discovery` | completed | scripted/scripted-recent-transactions | 6 | 0 | 0.0000 | 7.7 s | 0 LLM calls · $0 · 1.3 s |
| `01-discovery-scripted-savings-balance/discovery` | completed | scripted/scripted-get-savings-balance | 5 | 0 | 0.0000 | 7.6 s | 0 LLM calls · $0 · 2.7 s |
| `01a-discovery-live-savings-balance` | completed | anthropic/claude-opus-5 | 5 | 17084 | 0.0951 | 22.6 s | 0 LLM calls · $0 · 1.6 s |
| `01b-discovery-live-recent-transactions` | completed | anthropic/claude-opus-5 | 6 | 21414 | 0.1181 | 26.0 s | 0 LLM calls · $0 · 1.7 s |
| `01c-discovery-live-goal-not-achievable` | GOAL_NOT_ACHIEVABLE | anthropic/claude-opus-5 | 5 | 17062 | 0.0995 | 26.4 s | no capability |
| `01d-discovery-live-wire-stuck-loop-before-fix` | failed HUMAN_TIMEOUT | anthropic/claude-opus-5 | 7 | 23728 | 0.1362 | 941.6 s | no capability |
| `08-policy/misled-agent-close-share-and-admin` | GOAL_NOT_ACHIEVABLE | scripted/scripted-misled-agent | 6 | 0 | 0.0000 | 9.3 s | no capability |

Scripted discovery rows have zero LLM cost by construction; live rows (`01a`-`01d`) show real model usage, including runs that produced no capability.
