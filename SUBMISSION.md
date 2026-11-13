# MLH submission: AIE x MLH Hackathon @ Code Summit

## Project name
jevbench

## Tagline / elevator pitch
Does a decision model in front of an agent LLM make tool use faster, cheaper, or more accurate? One command runs every combination on real agent benchmarks and streams the answer to a live dashboard.

## Description / About the project

### Inspiration
Agents slow down and get confused as their tool lists grow. "System 1" decision models like Jev claim to fix this by picking the right tools before the big LLM reasons. We wanted to measure that claim on existing third-party benchmarks with their own graders, not on a demo we built ourselves.

### What it does
jevbench runs a full matrix: Gemini 4 Carbon and Gemini 4 Hydrogen, each alone and with one of three decision models (Jev, SubQ Snap, or Gemini Decision). Every cell runs on three industry benchmarks:
- **τ-bench (Sierra):** airline, retail, and telecom customer service, with a simulated user
- **MedAgentBench (Stanford):** healthcare tasks on a FHIR EHR
- **MCPToolBench++:** browser, files, search, maps, finance, and payments

Each task runs twice: once with only the task's own tools and once padded to about 100 tools. In a "+ decision model" episode, the decision model answers one `choice` question over every tool. We keep tools until they cover 95% of the probability mass (min 3, max 8), plus a `request_more_tools` escape hatch. Only that shortlist goes to the Gemini agent. Everything else stays fixed.

We measure time to first tool call, time to first *correct* tool call, agent time, decision overhead, tokens, USD cost, and the benchmark's own success grade. Deltas are paired against the same task run without a decision model, so a condition can't look fast by failing early.

### Early results (40 live episodes, τ-bench + MCPToolBench++)
| Condition | Success | Time to first correct call (p50 / p95) | Input tokens / ep | Cost / ep |
|---|---|---|---|---|
| LLM alone | 60% | 4.8s / 19.3s | 16.0k | $0.032 |
| + Jev | **80%** | **4.0s / 10.2s** | 11.6k | $0.021 |
| + SubQ Snap | 80% | 4.7s / 12.1s | 10.5k | $0.021 |
| + Gemini Decision | 70% | 6.8s / 29.7s | 9.4k | $0.018 |

Jev added the least overhead (0.16s median decision time, versus 0.39s for SubQ and 0.69s for Gemini Decision). All three kept the correct tool in 100% of shortlists while cutting the list to about 3 tools. In τ-bench on Carbon, the first correct call came at 8.4s with Jev versus 27.4s alone. This sample is small (10 episodes per condition). The full matrix is 1,200 episodes, and the harness is built to scale to it with a budget guard and resume.

### How we built it
- **Gemini API:** both agent LLMs (streaming, with first-chunk and first-function-call timestamps), the Gemini Decision baseline, and the τ-bench user simulator and judges
- **Jev / SubQ Snap:** raw HTTP clients with identical timing, so the decision models compete fairly
- **Tiger Data (Tiger Cloud / TimescaleDB):** runs, episodes, and an `events` hypertable with a continuous aggregate (`call_latency_1m`) that powers the live dashboard
- **Sentry:** one transaction per episode, with spans for each decision, LLM, and tool call. We used the Sentry MCP server during the build to inspect traces.
- **DigitalOcean:** everything runs on one SFO3 droplet (docker compose: FHIR server, FastAPI dashboard, runner), provisioned with scripts and cloud-init. A `probe` command measures the network floor to each provider. The DigitalOcean MCP server is wired into the repo.
- **Python, FastAPI, uv, Docker, pytest**

### Challenges we ran into
- τ-bench's natural-language-assertion judge crashed on Gemini because Gemini wraps JSON in a ```json fence unless JSON mode is on, and τ parses with a bare `json.loads`. We patched it.
- Defining a "correct call" fairly across three benchmarks. τ and MCPToolBench++ have answer keys, but MedAgentBench doesn't, so we annotated the first required call per task type and still grade success only with Stanford's `refsol.py`.
- Keeping the clock honest: tool execution, the user simulator, and judges are excluded from agent time, and there's a discarded warm-up call per provider.

### Accomplishments we're proud of
- A fair, reproducible A/B harness on third-party benchmarks with their own graders, built in one day
- Paired deltas, so "faster" never means "failed sooner"
- A live dashboard that updates while the matrix runs

### What we learned
Decision models mostly help at the tail. Median gains are modest, but p95 time to the first correct call nearly halves with Jev, and input tokens drop by about 30%, because the agent sees 3 tools instead of 100.

### What's next
Run the full 1,200-episode matrix, including MedAgentBench on the droplet's FHIR server, add more reps for confidence intervals, and publish the dashboard.

## Built with
python, fastapi, gemini-api, jev, subq, tiger-data, timescaledb, postgresql, sentry, digitalocean, docker, uv, pytest, mcp

## Links
- GitHub: https://github.com/BaoTNguyen/jev-test
- Demo / dashboard: <droplet IP>:8000 (or a video link)

## Prize categories to opt into
- Best Use of System One Models (Jev / decision models: this is the core of the project)
- Best Overall Hack
- Best Use of Gemini API
- Best Use of Tiger Data
- Sentry Raffle
- Best Use of DigitalOcean
- (Optional) Cleanest Use of a Software Factory, if it covers agent-built code (built with Claude Code + MCP)
