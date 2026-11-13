# Demo script (~2 min, two speakers)

Numbers are medians from `SMOKE_RESULTS.md` (240 clean task attempts: 5 problems x 3 benchmarks x 2 tool lists x 8 setups).

**A:** Agents are drowning in tools. Every MCP server and plugin adds descriptions, and all of them are sent to the LLM on every turn. At 100 tools that's tens of thousands of tokens before the agent does anything.

**B:** So we asked: can a small decision model in front of the agent fix that? It scores every tool and keeps the top three to eight. The big LLM sees only that shortlist, plus a "request more tools" escape hatch. It only narrows; the LLM still does the reasoning.

**A:** We tested Jev, SubQ Snap and Gemini Decision against Gemini Carbon and Hydrogen alone. We ran them on τ-bench, MedAgentBench and MCPToolBench++, each with its own grader, on a small tool list and a padded list of up to 100 tools.

**B:** We didn't only time the whole task. We measured time to first tool call, and time to first *correct* tool call, meaning one the benchmark's answer key accepts. A fast wrong call doesn't count.

**A:** Here's what we found. Jev was the standout on Hydrogen. Correct went from 87 to 93 percent. On the large tool list, total task time dropped from 11.5 seconds to 5.8, and first correct call went from 3.0 to 2.5.

**B:** And in τ-bench on Carbon, time to first correct call fell from 13 seconds to 9.3.

**A:** But it's not a free win. On Carbon, decision models didn't speed up the first correct call. The 0.2 to 0.7 second decision step is real overhead, and Jev made Carbon's total task time longer, 24.8 seconds to 35.5.

**B:** The pattern: a decision model pays off when the toolbox is big and the model is fast. Hydrogen with the large list got about twice as fast. SubQ Snap and Gemini Decision cut Carbon's total time by 3 to 7 seconds on the large list, but didn't improve accuracy.

**A:** It's a smoke run, 15 to 30 attempts per cell. Accuracy differences are a few points, not proof.

**B:** So the takeaway: as toolboxes grow, sending every tool every turn doesn't scale. Routing helps most where the agent is fast and the list is long, and jevbench lets you measure exactly where it pays off.
