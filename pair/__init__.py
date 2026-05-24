"""
PAIR-Lab: Prompt Automatic Iterative Refinement
================================================
A faithful Python implementation of the PAIR attack algorithm from:
  Chao et al. (2023). "Jailbreaking Black Box Large Language Models in Twenty Queries"
  https://arxiv.org/abs/2310.08419

Package structure:
  pair/
  ├── prompts.py   - System prompts for attacker, target, judge
  ├── attacker.py  - Attacker LLM: proposes and refines jailbreaks
  ├── target.py    - Target LLM: model under attack
  ├── judge.py     - Judge LLM: scores attack success (1-10)
  └── pipeline.py  - PAIR loop orchestrator + results collector
"""
