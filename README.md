# jevbench: LLM alone vs LLM + decision model

Does putting a fast decision model in front of an agent LLM make tool calls faster, cheaper or more accurate?
jevbench runs Gemini 4 Carbon and Gemini 4 Hydrogen, each alone and with one of three decision models (Jev, SubQ Snap, Gemini Decision), on three third-party agent benchmarks. Each benchmark's own grader decides what counts as correct.

**[Full results report →](https://baotnguyen.github.io/jev-test/)** It has every chart, the filters, the per-benchmark tables and all 547 task attempts from the main run.
It is one HTML file with the run's data built in, [`docs/index.html`](docs/index.html), so you can also download it and open it in a browser (the charts load Chart.js from a CDN).

## Why we built this

Agents get slower and less reliable as their tool lists grow: every extra tool means more input tokens and one more way to pick wrong.
"System 1" decision models claim to fix this by shortlisting the right tools in a fraction of a second, before the big LLM starts reasoning.
We wanted to test that claim first on third-party benchmarks with their own graders. Later we want to build targeted benchmarks of our own, to understand the whole agentic tool-call path end to end.

**What we optimize for:**

- **Less token and cost bloat.** Give the LLM only the tools that are relevant to the request, so it spends its context on the task instead of a 100-tool catalogue, and stays as accurate as possible.
- **The right call on the first try.** Help the model make the correct tool call from its very first call. This is built for physical agents and for industries where a tool call makes a real change to the outside world, such as a refund issued, an order placed or a medical record written. In those settings, a wrong call can't just be retried.

## Results

**Main run:** `run-20261113-132025`, Nov 13, 2026. It tested 2 Gemini models × 4 setups on τ-bench, MedAgentBench and MCPToolBench++, each with a small tool list (1–32 tools) and a large one (87–100 tools).
The run stopped at 547 of 1,200 planned task attempts when the Gemini prepaid credits ran out. The 502 clean attempts are counted below. The other 45 hit rate limits or provider errors and are left out. All figures are medians unless noted.

### Decision step (time per call, both models)

- **Jev:** 0.16 s (slowest 5%: 0.42 s)
- **SubQ Snap:** 0.39 s (slowest 5%: 0.77 s)
- **Gemini Decision:** 0.52 s (slowest 5%: 1.07 s)

All three kept the right tool in the shortlist 96–98% of the time, while cutting the list from up to 100 tools to about 3.

### Gemini 4 Carbon

- **Time to first correct tool call:** 8.5 s alone → 5.5 s with Jev, 6.4 s with SubQ Snap, 6.5 s with Gemini Decision
- **Whole task (decision model + Gemini time):** 29.0 s alone → 21.3 s with Jev, 27.1 s with SubQ Snap, 24.6 s with Gemini Decision
- **Correct:** 74% alone → 81% with Jev, 73% with SubQ Snap, 78% with Gemini Decision
- **First tool call was the correct one:** 61% alone → 75% with Jev, 75% with SubQ Snap, 79% with Gemini Decision
- **Cost per attempt:** $0.145 alone → $0.085 with Jev, $0.089 with SubQ Snap, $0.060 with Gemini Decision

### Gemini 4 Hydrogen

- **Time to first correct tool call:** 2.6 s alone → 2.5 s with Jev, 2.8 s with SubQ Snap, 3.0 s with Gemini Decision
- **Whole task (decision model + Gemini time):** 5.6 s alone → 4.8 s with Jev, 6.0 s with SubQ Snap, 5.2 s with Gemini Decision
- **Correct:** 79% alone → 88% with Jev, 86% with SubQ Snap, 82% with Gemini Decision
- **First tool call was the correct one:** 75% alone → 67% with Jev, 70% with SubQ Snap, 67% with Gemini Decision (a task-mix effect: on matched tasks there was no change, see below)
- **Cost per attempt:** $0.081 alone → $0.047 with Jev, $0.049 with SubQ Snap, $0.039 with Gemini Decision

### What holds up

The run stopped partway through a shuffled plan, so each setup above saw a different mix of tasks. To control for that, every attempt with a decision model was also compared with **the same task, model and tool list run alone**:

- **Cost is the clear win.** On paired tasks, every decision model cut input tokens by 29–68% and cost by 35–76%, on both models.
- **Decision models helped Carbon get the first call right, and never hurt it.** On matched tasks, no decision model turned a correct first call into a wrong one. On Carbon each one fixed 2–3 first calls (+7 to +9 points). On Hydrogen nothing changed. The counts are small, but they point the right way for the first-call goal.
- **No decision model is clearly the most accurate.** Jev had the highest raw success rate on both models: 84% across both, against 80% for Gemini Decision, 79% for SubQ Snap and 77% alone. But the 95% intervals overlap heavily (Jev 77–89%, the others 69–86%). On matched tasks, SubQ Snap gained as much as Jev (+5 vs +4 points overall), and on Carbon Jev did slightly worse than the LLM alone (1 task fixed, 2 broken). Picking the right tool is a three-way tie too, with 96.5–97.9% shortlist recall. Hydrogen + Jev (+13 points on 23 matched tasks) is the result most worth rerunning with more repetitions.
- **Speed barely changes.** Paired, the time to the first correct tool call moved by less than 0.6 s either way. For example, Carbon + Jev was 0.4 s faster on the same tasks, not the 3.0 s gap the medians above suggest. The decision step pays for itself through shorter prompts, but it doesn't make the agent noticeably faster.
- **Hydrogen beats Carbon at this task.** Alone, it reached the right call about 3× sooner (2.6 s vs 8.5 s), was more accurate (79% vs 74%) and cost about half as much.

<details>
<summary>Paired comparison table</summary>

| Model | Setup | Paired tasks | Correct vs alone | First call correct vs alone | First correct call vs alone | Input tokens vs alone | Cost vs alone |
|---|---|---|---|---|---|---|---|
| Carbon | + Jev | 29 | −3 pts | +7 pts | −0.4 s | −39% | −48% |
| Carbon | + SubQ Snap | 31 | +3 pts | +6 pts | −0.6 s | −29% | −35% |
| Carbon | + Gemini Decision | 32 | +3 pts | +9 pts | −0.5 s | −47% | −57% |
| Hydrogen | + Jev | 23 | +13 pts | 0 pts | −0.1 s | −65% | −76% |
| Hydrogen | + SubQ Snap | 27 | +7 pts | 0 pts | −0.2 s | −48% | −50% |
| Hydrogen | + Gemini Decision | 36 | −3 pts | 0 pts | +0.4 s | −68% | −74% |

Time, token and cost changes are medians over the pairs. Times count only pairs where both runs reached a correct call.
</details>

**All charts for this run** (response time over the run, results by model and tool-list size, per-benchmark bars, the full table and every attempt) are in the [full results report](https://baotnguyen.github.io/jev-test/) ([`docs/index.html`](docs/index.html)).

**Limits:** one repetition per task, a run that stopped partway, and no confidence intervals yet. τ-bench conversations are long-tailed (Carbon's first correct call took anywhere from 7 s to 230 s), so the τ-bench cells on the results page swing a lot.

**Earlier smoke run:** a complete but smaller run of 240 attempts (5 problems per benchmark), in [SMOKE_RESULTS.md](SMOKE_RESULTS.md). Jev was again the fastest decision step (145 ms) and again had the highest raw success rate on both models (83% vs 80% alone on Carbon, 93% vs 87% on Hydrogen). That is within the error bars again: across 60 matched tasks it fixed 3 and broke none.

## What's being tested

|  | LLM alone | + Jev | + SubQ Snap | + Gemini Decision |
|---|---|---|---|---|
| **Gemini 4 Carbon** | ✓ | ✓ | ✓ | ✓ |
| **Gemini 4 Hydrogen** | ✓ | ✓ | ✓ | ✓ |

Each cell runs on:

| Benchmark | Industry | What gets scored | Small vs large tool list |
|---|---|---|---|
| [τ-bench](https://github.com/sierra-research/tau2-bench) (Sierra) | airline, retail, telecom customer service | τ's own reward (final DB state and facts communicated), simulated user | domain tools (13–16) vs 100 |
| [MedAgentBench](https://github.com/stanfordmlgroup/MedAgentBench) (Stanford) | healthcare (FHIR EHR) | Stanford's `refsol.py` grader | 9 FHIR tools + finish vs 100 |
| [MCPToolBench++](https://github.com/mcp-tool-bench/MCPToolBenchPP) | browser, files, search, maps, finance, payments | the benchmark's AST check (exact match, then its LLM-judge prompt) | the item's own tools vs all 87 |

### How a "+ decision model" episode works

```
user request ─► decision model: one `choice` question over every tool ──► probabilities
                └─ keep tools until 95% of the probability mass (min 3, max 8)
                   + tools already used + `request_more_tools` escape hatch
             ─► same Gemini agent loop, but only the shortlist is attached
```

Everything except the decision model stays fixed: prompt, tools, thinking level, user simulator, judges, step limits.
On τ-bench the decision model runs again on every new user message.
Jev, SubQ and Gemini Decision get identical questions and the same shortlist rule (`jevbench/router.py`).

### Time to first tool call vs time to first *correct* tool call

Both are measured on the **agent clock**, which runs only while the decision model or the agent LLM is working. Tool execution, the τ user simulator and the judges are excluded, so every setup is timed the same way.

| | `ttftc_s` (time to first tool call) | `ttfctc_s` (time to first *correct* tool call) |
|---|---|---|
| Stops when | the first function-call chunk streams in from Gemini, right or wrong | the first call the benchmark's answer key accepts |
| Tells you | how fast the agent commits to an action | how long a user waits for the right action |
| Weakness | a fast wrong call looks good | none: a wrong call keeps the clock running |

Episodes keep going after a wrong call until a correct one lands (or the step limit hits), so `ttfctc_s` includes the cost of every wasted call. That makes it the main speed metric here: a decision model that shaves the first call but sends the agent down a wrong path scores worse, not better.
A "+ decision model" episode's clock includes the decision call, so its overhead is charged against it. Each provider gets one discarded warm-up call first, and `python -m jevbench probe` measures the network floor so decision overhead can be read net of latency.

What counts as a "correct call":

- **τ:** matches one of the task's expected actions, using τ's own `compare_with_tool_call`.
- **MCPToolBench++:** matches the labelled call (or one of its `similar_tools`).
- **MedAgentBench** has no per-call key, so `EXPECTED` in `jevbench/benches/medagentbench.py` is our annotation of the first call each task type needs. Task success still comes only from `refsol.py`.

## What the numbers mean

A guide to every term on the [results page](https://baotnguyen.github.io/jev-test/).

| Term | Meaning |
|---|---|
| Task attempt | One benchmark problem, tried once by one setup. Example: "Gemini Carbon + Jev handles one airline customer request." |
| LLM alone | Gemini gets the whole tool list and decides by itself which tool to use. |
| + Jev / + SubQ Snap / + Gemini Decision | A fast decision model reads the request first and picks the 3–8 most likely tools, with a "give me more tools" escape hatch. Gemini then sees only those. Everything else stays the same. |
| Small / large tool list | Small = only the tools that belong to the problem (1–32). Large = padded with tools from other apps (87–100), the way a real assistant connected to many services looks. |
| Correct | The benchmark's own grader says the job was done right. τ-bench checks the final database and what the customer was told, MCPToolBench++ checks the tool call against its answer key, and MedAgentBench uses Stanford's `refsol.py`. |
| Time to first tool call | How long until the agent takes its first action, right or wrong. |
| Time to first correct tool call | How long until it takes the right action. This is the wait a user actually feels. |
| Slowest 5% | The 95th percentile: the tail a user sometimes hits. |
| Total task time | All the AI's thinking time for the task (decision model + Gemini). Tool execution and the simulated customer's replies are not counted, so every setup is timed the same way. |
| vs alone | The same problem, model and tool list run without a decision model. Times count only pairs where both reached a correct call, so a setup can't look fast by failing early. The bracketed numbers on the result tiles compare whole groups and are not paired. |
| Decision model time | How long one decision-model call took (the median per call). |
| Gemini first byte | Time until Gemini streamed its first chunk of a reply. |
| Wrong calls first | Average number of tool calls the agent made before its first correct one. |
| Right tool shortlisted | How often the decision model's shortlist contained the tool the answer key needed. |
| Tools shortlisted / asked for more tools | Average shortlist size, and how often the agent used the escape hatch to see the full list. |
| Errors / excluded | Attempts that crashed or hit a provider error such as a 429 rate limit. They measure the account's quota, not the models, so they are left out of every number. |
| Response time charts | The average time each provider took to answer one call, minute by minute during the run. The jump of about 30 minutes in the time axis is where the run paused and was resumed. Times are in your local time zone. |

## Metrics: field reference (per episode, then p50/p95 per cell)

| Metric | Meaning |
|---|---|
| `ttftc_s` | time to first tool call: agent clock until the first function-call chunk streams in, right or wrong |
| `ttfctc_s` | time to first *correct* tool call: until the first call the benchmark's answer key accepts |
| `agent_time_s`, `wall_time_s` | whole episode on the agent clock, and real time |
| `decision_time_s`, `first_byte_s` | time spent in the decision model; LLM time to first streamed chunk |
| `score` / `success` | the benchmark's own grade, so a fast answer that is wrong still counts as wrong |
| `calls_before_correct`, `wrong_calls`, `distractor_calls` | wasted calls |
| `shortlist_recall`, `avg_shortlist_size`, `hatch_used` | whether the decision model kept the right tool |
| tokens, `cost_usd` | input/output/cached tokens and USD (prices in `config/matrix.yaml`) |

## Hosting: everything on one DigitalOcean droplet

None of these models can be self-hosted: Gemini, Jev and SubQ Snap are vendor APIs, and DigitalOcean doesn't serve them.
"Local" therefore means running the runner close to the APIs and measuring the network on its own:

```
DigitalOcean droplet, SFO3 (s-4vcpu-8gb)
  docker compose:
    fhir       MedAgentBench FHIR server (jyxsu6/medagentbench)
    dashboard  FastAPI on :8000, reads Tiger Data
    runner     python -m jevbench run   ──► Gemini API (Carbon, Hydrogen, Decision 1)
                                          ──► api.typesafe.ai (Jev; or OpenRouter)
                                          ──► api.subq.ai (SubQ Snap)
  ──► Tiger Data (Tiger Cloud, Postgres + TimescaleDB): runs, episodes, events hypertable
  ──► Sentry: one transaction per episode, spans per decision / LLM / tool call
```

Run `python -m jevbench probe` on the droplet first. It measures the TLS handshake and warm-call latency to each provider, so you can subtract the network floor from decision overhead. The runner also makes one discarded warm-up call per provider before measuring.

## Setup

### 1. Keys (`cp .env.example .env`)

| Variable | Needed for | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` | **required.** Carbon, Hydrogen, Gemini Decision, the τ user simulator and judges | aistudio.google.com/apikey (paid project: Carbon has no free tier) |
| `TYPESAFE_API_KEY` *or* `OPENROUTER_API_KEY` | **required for Jev**: TypeSafe direct (`api.typesafe.ai`, model `jev-1.14.0`) is used when only the TypeSafe key is set | docs.typesafe.ai / openrouter.ai/keys |
| `SUBQ_API_KEY` | **required for SubQ Snap** | docs.subq.ai |
| `DATABASE_URL` | dashboard storage (Tiger Data) | Tiger console → service → Connect |
| `SENTRY_DSN` | tracing (Sentry raffle) | Sentry → Project Settings → Client Keys |
| `JEV_BASE_URL`, `JEV_MODEL` | optional overrides for the TypeSafe direct route | |
| `OPENAI_API_KEY` | optional: only if you set `tau.judge_llm: null` to keep τ's OpenAI judges | platform.openai.com |
| `DIGITALOCEAN_API_TOKEN` | deploy scripts, doctl, DigitalOcean MCP | DO console → API |
| `DASHBOARD_PASSWORD` | optional basic auth on the dashboard (user `jev`) | you choose |

### 2. MedAgentBench grader

Download `refsol.py` from the link in the MedAgentBench README into `refsol/refsol.py` (it's gitignored).
Without it, MedAgentBench episodes still run and are timed, but their score is null.

### 3. Run it on DigitalOcean

```bash
doctl auth init                                  # uses DIGITALOCEAN_API_TOKEN
scripts/do_create_droplet.sh jevbench            # SFO3, Ubuntu 24.04, Docker via cloud-init
ssh root@<ip>
git clone git@github.com:BaoTNguyen/jev-test.git && cd jev-test
cp .env.example .env && nano .env
mkdir -p refsol && nano refsol/refsol.py
docker compose up -d --build                     # FHIR + dashboard at http://<ip>:8000
docker compose run --rm runner check             # keys + benchmarks + episode plan
docker compose run --rm runner probe             # network floor per provider from SFO3
docker compose run --rm runner run --dry-run     # plan only
docker compose run --rm runner                   # the whole matrix, live on the dashboard
docker compose run --rm runner report            # final table, also written to results/<run>/summary.json
```

Optional: host only the dashboard on App Platform with `doctl apps create --spec deploy/app.yaml`. It reads the same Tiger database.

### Run locally (any OS)

```bash
uv sync --extra dev
scripts/setup_benchmarks.sh        # fetches the 3 benchmarks at pinned commits into vendor/ and installs tau2
docker run -d -p 8080:8080 jyxsu6/medagentbench:latest   # FHIR server for MedAgentBench
export FHIR_API_BASE=http://localhost:8080/fhir/ TAU2_DATA_DIR=$PWD/vendor/tau2-bench/data
export PYTHONUTF8=1                # Windows only: tau reads its data as UTF-8
export VENDOR_DIR=$PWD/vendor      # .env holds the Docker paths; shell values win over .env
# use `uv run --no-sync`: a plain `uv run` re-syncs to uv.lock and uninstalls the editable tau2
uv run --no-sync pytest -q                                                        # offline tests
uv run --no-sync python -m jevbench --config config/smoke.yaml run --no-warmup    # offline smoke run (mock LLM + mock router)
uv run --no-sync python -m jevbench --config config/live_mini.yaml run            # 40 real episodes, ~$1, every condition
uv run --no-sync python -m jevbench dashboard                                     # http://localhost:8000
uv run --no-sync python -m jevbench export-static <run_id> --note '...'           # freeze a run into docs/index.html (reads results/<run_id>/, not the database)
```

## Size, time and cost

The defaults plan **1,200 episodes** (τ 240, MedAgentBench 480, MCPToolBench++ 480):

- τ: 3 domains × 5 tasks
- MedAgentBench: 10 task types × 3
- MCPToolBench++: 6 categories × 5

Each task runs on 2 surfaces × 2 LLMs × 4 conditions.

Cost is dominated by τ, because every turn re-sends the policy and tools. Expect very roughly $0.2–0.6 per τ episode on Carbon, and cents for the other two benchmarks.
`BUDGET_USD` (default $150) stops the run cleanly, and `run --resume <run_id>` continues it. Shrink or grow the run with `per_domain`, `per_task_type`, `per_category` and `reps`, or limit benchmarks with `run --only tau,mcptoolbench`.

## Tooling for the build (MCP)

`.mcp.json` registers the hosted **DigitalOcean** MCP server (`https://mcp.digitalocean.com/mcp`) and the **Sentry** MCP server (`https://mcp.sentry.dev/mcp`), both with OAuth.
In Claude Code, run `/mcp` to sign in. Then you can ask it to check the droplet, App Platform logs or Sentry traces.

## Repo map

```
config/matrix.yaml          the experiment: models, decision models, router rule, benchmarks, prices
config/smoke.yaml           offline smoke test (mock LLM + mock decision model)
jevbench/decision.py        Jev / SubQ / Gemini Decision / mock clients (raw HTTP, same timing for all)
jevbench/router.py          probabilities -> shortlist (mass / min_k / max_k, sticky, escape hatch, 2-stage >255 tools)
jevbench/llm.py             streaming Gemini conversation with first-chunk / first-call timestamps
jevbench/episode.py         agent clock + metrics recorder, generic tool loop
jevbench/benches/           tau.py, medagentbench.py, mcptoolbench.py adapters
jevbench/runner.py          plans and runs the whole matrix concurrently; budget guard, hard stop, resume
jevbench/store.py, schema.sql   JSONL + Tiger Data (hypertable `events`, continuous aggregate `call_latency_1m`)
jevbench/dashboard.py, static/index.html   live scoreboard (same page renders the static snapshot)
jevbench/export.py          export-static: one run -> docs/index.html with the data embedded
docs/                       the published results page (GitHub Pages)
jevbench/probe.py           network floor + warm-call latency per provider
scripts/, deploy/           benchmark setup, droplet creation, cloud-init, App Platform spec
```

## Credits

The method follows [time-to-first-tool-call](https://github.com/nikhilkulkarni1755/time-to-first-tool-call): streamed timestamps, episodes that keep going until a call is correct, mock execution kept off the clock, loud halts, and resume.

The benchmarks are MIT-licensed by their authors (Sierra Research, Stanford ML Group, mcp-tool-bench). They are fetched at pinned commits, not vendored into this repo.
