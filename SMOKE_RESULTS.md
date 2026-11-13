# Smoke run: 240 clean task attempts (5 problems x 3 benchmarks x 2 tool lists x 8 setups)

Medians. Brackets = change vs the same Gemini model alone (correct: percentage points; times: seconds, negative = faster).

## All benchmarks, both tool lists

| Model | Setup | Attempts | Correct | First tool call | First correct tool call | Total task time |
|---|---|---|---|---|---|---|
| Carbon | LLM alone | 30 | 80% | 6.6 s | 8.3 s | 24.8 s |
| Carbon | + Jev | 30 | 83% (+3) | 7.7 s (+1.2) | 8.3 s (-0.1) | 35.5 s (+10.7) |
| Carbon | + SubQ Snap | 30 | 77% (-3) | 6.8 s (+0.3) | 8.5 s (+0.2) | 21.6 s (-3.2) |
| Carbon | + Gemini Decision | 30 | 80% (+0) | 7.5 s (+1.0) | 9.2 s (+0.9) | 20.2 s (-4.6) |
| Hydrogen | LLM alone | 30 | 87% | 2.2 s | 2.6 s | 7.6 s |
| Hydrogen | + Jev | 30 | 93% (+7) | 2.2 s (-0.0) | 2.5 s (-0.1) | 5.5 s (-2.1) |
| Hydrogen | + SubQ Snap | 30 | 90% (+3) | 3.6 s (+1.3) | 3.8 s (+1.3) | 6.3 s (-1.3) |
| Hydrogen | + Gemini Decision | 30 | 83% (-3) | 3.1 s (+0.8) | 3.2 s (+0.6) | 6.2 s (-1.4) |

## Small tool list (each problem's own tools)

| Model | Setup | Attempts | Correct | First tool call | First correct tool call | Total task time |
|---|---|---|---|---|---|---|
| Carbon | LLM alone | 15 | 80% | 4.9 s | 7.6 s | 22.7 s |
| Carbon | + Jev | 15 | 87% (+7) | 7.1 s (+2.1) | 7.8 s (+0.1) | 30.1 s (+7.3) |
| Carbon | + SubQ Snap | 15 | 80% (+0) | 5.3 s (+0.4) | 8.4 s (+0.7) | 20.6 s (-2.2) |
| Carbon | + Gemini Decision | 15 | 80% (+0) | 6.9 s (+2.0) | 7.1 s (-0.5) | 15.6 s (-7.1) |
| Hydrogen | LLM alone | 15 | 87% | 1.9 s | 2.0 s | 6.4 s |
| Hydrogen | + Jev | 15 | 93% (+7) | 2.1 s (+0.2) | 2.3 s (+0.3) | 5.3 s (-1.1) |
| Hydrogen | + SubQ Snap | 15 | 93% (+7) | 3.1 s (+1.2) | 3.2 s (+1.2) | 5.7 s (-0.7) |
| Hydrogen | + Gemini Decision | 15 | 87% (+0) | 3.0 s (+1.1) | 3.1 s (+1.0) | 6.5 s (+0.1) |

## Large tool list (~55-100 tools)

| Model | Setup | Attempts | Correct | First tool call | First correct tool call | Total task time |
|---|---|---|---|---|---|---|
| Carbon | LLM alone | 15 | 80% | 7.0 s | 9.2 s | 29.4 s |
| Carbon | + Jev | 15 | 80% (+0) | 8.0 s (+1.1) | 10.4 s (+1.2) | 37.2 s (+7.8) |
| Carbon | + SubQ Snap | 15 | 73% (-7) | 7.0 s (+0.0) | 8.5 s (-0.7) | 22.6 s (-6.8) |
| Carbon | + Gemini Decision | 15 | 80% (+0) | 9.0 s (+2.0) | 9.9 s (+0.7) | 22.7 s (-6.7) |
| Hydrogen | LLM alone | 15 | 87% | 3.0 s | 3.0 s | 11.5 s |
| Hydrogen | + Jev | 15 | 93% (+7) | 2.3 s (-0.6) | 2.5 s (-0.5) | 5.8 s (-5.7) |
| Hydrogen | + SubQ Snap | 15 | 87% (+0) | 3.8 s (+0.9) | 4.0 s (+1.0) | 6.4 s (-5.1) |
| Hydrogen | + Gemini Decision | 15 | 80% (-7) | 3.2 s (+0.2) | 3.2 s (+0.3) | 6.0 s (-5.5) |

## Correct (%) by benchmark, both tool lists

| Benchmark | Model | LLM alone | + Jev | + SubQ Snap | + Gemini Decision |
|---|---|---|---|---|---|
| τ-bench (customer service) | Carbon | 70% | 80% | 60% | 70% |
| τ-bench (customer service) | Hydrogen | 80% | 80% | 80% | 60% |
| MCPToolBench++ (apps & APIs) | Carbon | 70% | 70% | 70% | 70% |
| MCPToolBench++ (apps & APIs) | Hydrogen | 90% | 100% | 90% | 90% |
| MedAgentBench (healthcare) | Carbon | 100% | 100% | 100% | 100% |
| MedAgentBench (healthcare) | Hydrogen | 90% | 100% | 100% | 100% |

## Time to first correct tool call (median) by benchmark, both tool lists

| Benchmark | Model | LLM alone | + Jev | + SubQ Snap | + Gemini Decision |
|---|---|---|---|---|---|
| τ-bench (customer service) | Carbon | 13.0 s | 9.3 s | 12.3 s | 12.0 s |
| τ-bench (customer service) | Hydrogen | 4.5 s | 4.1 s | 6.4 s | 5.8 s |
| MCPToolBench++ (apps & APIs) | Carbon | 5.0 s | 8.0 s | 6.5 s | 5.4 s |
| MCPToolBench++ (apps & APIs) | Hydrogen | 2.1 s | 2.0 s | 2.9 s | 2.7 s |
| MedAgentBench (healthcare) | Carbon | 8.6 s | 11.7 s | 7.5 s | 7.5 s |
| MedAgentBench (healthcare) | Hydrogen | 2.6 s | 2.5 s | 3.5 s | 2.7 s |

