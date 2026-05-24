"""
pair/attacker.py
----------------
The Attacker LLM role in the PAIR algorithm.

Responsible for:
  - Receiving the goal, previous prompt, target's response, and judge score
  - Using chain-of-thought reasoning to generate an improved jailbreak prompt
  - Parsing its own structured JSON output (improvement + new prompt)

The attacker runs on a high-capability model (e.g. llama-3.1-70b) because it
needs creative reasoning to craft and refine adversarial prompts. It is the
"brain" of the attack loop.
"""

import json
import logging
import os
import re
from dataclasses import dataclass

from groq import Groq

from pair.prompts import ATTACKER_SYSTEM_PROMPT, ATTACKER_USER_TEMPLATE

logger = logging.getLogger(__name__)


@dataclass
class AttackerOutput:
    """Structured output from one attacker call."""
    improvement: str   # Reasoning about what changed and why
    prompt: str        # The new jailbreak prompt to send to target


class Attacker:
    """
    PAIR Attacker LLM.

    Given a goal and feedback from the previous iteration (target response +
    judge score), generates a refined adversarial prompt.

    Parameters
    ----------
    client : Groq
        Authenticated Groq client.
    model : str
        Model name for the attacker. Should be a large, capable model.
    temperature : float
        Sampling temperature. Higher = more creative/varied prompts.
    """

    def __init__(
        self,
        client: Groq,
        model: str = "llama-3.1-70b-versatile",
        temperature: float = 1.0,
    ):
        self.client = client
        self.model = model
        self.temperature = temperature

    def generate(
        self,
        goal: str,
        prev_prompt: str = "",
        prev_response: str = "",
        score: int = 0,
    ) -> AttackerOutput:
        """
        Generate an improved adversarial prompt.

        Parameters
        ----------
        goal : str
            The harmful behavior we are trying to elicit.
        prev_prompt : str
            The prompt sent in the previous iteration (empty on first call).
        prev_response : str
            The target's response from the previous iteration.
        score : int
            Judge's score (1-10) for the previous attempt. 0 on first call.

        Returns
        -------
        AttackerOutput
            Parsed improvement reasoning + new prompt.
        """
        user_msg = ATTACKER_USER_TEMPLATE.format(
            goal=goal,
            prev_prompt=prev_prompt if prev_prompt else "[First iteration — no previous prompt]",
            prev_response=prev_response if prev_response else "[No previous response]",
            score=score if score > 0 else "N/A (first iteration)",
        )

        logger.debug(f"Attacker user message:\n{user_msg}")

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": ATTACKER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=self.temperature,
            max_tokens=1024,
        )

        raw = response.choices[0].message.content.strip()
        logger.debug(f"Attacker raw output:\n{raw}")

        return self._parse(raw)

    def _parse(self, raw: str) -> AttackerOutput:
        """
        Parse the attacker's JSON output.

        The attacker is instructed to return:
          {"improvement": "...", "prompt": "..."}

        Falls back gracefully if JSON is malformed (wraps raw text as prompt).
        """
        # Strip markdown code fences if the model wrapped it
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            data = json.loads(cleaned)
            improvement = data.get("improvement", "").strip()
            prompt = data.get("prompt", "").strip()

            if not prompt:
                raise ValueError("Empty 'prompt' field in attacker output.")

            return AttackerOutput(improvement=improvement, prompt=prompt)

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"Attacker output parse failed ({exc}). Using raw as prompt.")
            # Graceful degradation: treat entire output as the prompt
            return AttackerOutput(
                improvement="[Parse error — used raw attacker output as prompt]",
                prompt=raw,
            )
