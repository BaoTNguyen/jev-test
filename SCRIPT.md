# Demo script (~3 min, two speakers)

Numbers are medians from `SMOKE_RESULTS.md` (240 clean task attempts: 5 problems x 3 benchmarks x 2 tool lists x 8 setups).

## Motivation

**A:** Agents are drowning in tools. Every MCP server and plugin adds descriptions, and all of them are sent to the LLM on every turn. At 100 tools that's tens of thousands of tokens before the agent does anything, which means more cost, more latency, and more wrong picks.

**B:** Small "System 1" decision models, like Jev, claim to fix this. They look at the request and pick the right tools before the big LLM reasons. We wanted to test that claim on third-party benchmarks with their own graders, not on a demo we built ourselves.

## How it works

**A:** In a "+ decision model" run, the decision model answers one question over every tool and gives us probabilities. We keep tools until they cover 95 percent of the probability, at least 3 and at most 8, plus a "request more tools" escape hatch. Only that shortlist goes to the Gemini agent.

**B:** That's the whole experiment. We compared Jev, SubQ Snap and Gemini Decision against Gemini 4 Carbon and Hydrogen with no decision model. Prompt, tools, user simulator, judges and step limits all stay fixed.

## Benchmarking style

**A:** We ran three benchmarks: Sierra's τ-bench for customer service, Stanford's MedAgentBench for healthcare, and MCPToolBench++ for browser, maps, finance and payments. Each task runs with its own tools and again padded to up to 100.

**B:** The grader is always the benchmark's own, so a fast wrong answer still counts as wrong. And we compare each run against the same task without a decision model, so a setup can't look fast by failing early.

**A:** Beyond total time, we measure time to first tool call, and time to first *correct* tool call, meaning one the benchmark's answer key accepts. The clock only runs while the decision model or the agent LLM is working, and we throw away one warm-up call per provider.

## Results

**B:** Jev was the standout on Hydrogen. Correct went from 87 to 93 percent. On the large tool list, total time fell from 11.5 seconds to 5.8. In τ-bench on Carbon, first correct call fell from 13 seconds to 9.3.

**A:** But it's not a free win. On Carbon, the decision step is real overhead, and Jev made total time longer, 24.8 seconds to 35.5. SubQ Snap and Gemini Decision cut Carbon's total time by 3 to 7 seconds on the large list, without improving accuracy.

**B:** So routing pays off when the toolbox is big and the model is fast. It's a smoke run, 15 to 30 attempts per cell, so these are signals, not proof.

## Sponsors and how we used them

**A:** Gemini API powers both agents, the Gemini Decision baseline, and the τ-bench user simulator and judges, with streaming so we can timestamp the first function call. Jev and SubQ Snap are called over identical raw HTTP, so the timing is fair.

**B:** Tiger Data stores every run and episode, with an events hypertable and a continuous aggregate that powers the live dashboard. Sentry gives us a transaction per episode, with spans for every decision, LLM and tool call, and we used the Sentry MCP server to inspect traces while building.

**A:** And everything runs on one DigitalOcean droplet in SFO3 with docker compose, plus a probe command that measures the network floor to each provider.

## Close

**B:** As toolboxes grow, sending every tool every turn doesn't scale. jevbench is one command that shows you exactly where routing pays off.
