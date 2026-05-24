# Architecture

## Component Overview

```
pair-lab/
│
├── pair/                     Core library (importable as a package)
│   ├── prompts.py            All system prompts and message templates
│   ├── attacker.py           Attacker LLM wrapper + JSON output parser
│   ├── target.py             Target LLM wrapper
│   ├── judge.py              Judge LLM wrapper + score parser
│   └── pipeline.py           PAIR loop + results collection
│
├── goals/
│   └── goals.json            Curated test goals (id, category, difficulty, goal)
│
├── analysis/
│   └── analyze_results.py    Standalone analysis script for saved CSVs
│
├── tests/
│   └── test_pipeline.py      Unit tests (all mocked, no API needed)
│
├── results/                  Git-ignored output directory
│   ├── pair_iterations_*.csv  Row per iteration (full audit log)
│   ├── pair_summary_*.csv     Row per goal (aggregated metrics)
│   └── score_trajectory.png   Score plot across iterations
│
├── docs/
│   ├── ARCHITECTURE.md        This file
│   ├── ALGORITHM.md           Deep dive into the PAIR algorithm
│   └── RESULTS.md             Placeholder for your experimental results
│
├── run_pair.py               CLI entry point (uses Click)
├── .env.example              Config template (copy to .env)
└── requirements.txt
```

## Data Flow

```
┌─────────────┐
│  goals.json │
└──────┬──────┘
       │ list[str]
       ▼
┌──────────────────┐
│   PAIRPipeline   │  orchestrates the loop
│   run_all(goals) │
└──────┬───────────┘
       │
       │ for each goal:
       ▼
  ┌────────────────────────────────────────────────────┐
  │                   PAIR Loop (K iterations)          │
  │                                                    │
  │  ┌──────────┐  prompt  ┌──────────┐  response     │
  │  │ Attacker │ ──────►  │  Target  │ ──────────►   │
  │  │   LLM    │          │   LLM    │               │
  │  └────▲─────┘          └──────────┘               │
  │       │ score + improvement                        │
  │  ┌────┴─────┐                                      │
  │  │  Judge   │ ◄── response + goal + prompt         │
  │  │   LLM    │                                      │
  │  └──────────┘                                      │
  └────────────────────────────────────────────────────┘
       │
       │ List[IterationRecord]
       ▼
┌──────────────────┐
│  GoalResult      │  succeeded, best_score, tactic, ...
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────────┐
│  pair_iterations_TIMESTAMP.csv   │  full audit log
│  pair_summary_TIMESTAMP.csv      │  aggregated metrics
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│  analyze_results.py      │  ASR, tactic table, plot
└──────────────────────────┘
```

## Model Configuration Strategy

| Role | Recommended Model | Why |
|------|------------------|-----|
| Attacker | `llama-3.1-70b-versatile` | Needs strong reasoning + creativity |
| Target | `llama-3.1-8b-instant` | Simulates a deployed, smaller model |
| Judge | `llama-3.1-70b-versatile` | Needs careful instruction following |

You can vary the target to measure ASR across different models:
- Larger target → expect lower ASR
- Smaller/older target → expect higher ASR
- Target = Attacker → interesting adversarial dynamic

## Extension Points

### Adding a new provider (e.g., OpenAI)

Replace the Groq client with `openai.OpenAI()` and update the `.create()` call signature — the rest of the code is provider-agnostic.

### Adding conversation history to the attacker

Currently the attacker only sees one previous iteration. You can extend `Attacker.generate()` to pass the full history of (prompt, response, score) tuples via the messages array, similar to the paper's full implementation.

### Adding a PAIR-Tree variant

Instead of a single chain, run multiple parallel attack branches and pick the best one at each step. This is the "PAIR-Tree" variant mentioned in the paper.

### Adding semantic similarity filtering

Before querying the target, check if the new prompt is semantically too similar to the previous one (cosine similarity on embeddings). If similar, force the attacker to regenerate — avoids wasted queries.
