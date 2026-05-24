"""
pair/target.py
--------------
The Target LLM role in the PAIR algorithm.

Responsible for:
  - Receiving jailbreak prompts from the attacker
  - Returning natural responses (no special parsing needed)
  - Being the model under attack — we assess its alignment robustness

The target runs on a smaller/weaker model (e.g. llama-3.1-8b) to simulate
a deployed production LLM that an attacker would query via API.

Key design decision: the target uses temperature=0 for deterministic responses,
ensuring that score changes across iterations are due to prompt changes, not
sampling randomness.
"""

import logging
import os

from groq import Groq

from pair.prompts import TARGET_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class Target:
    """
    PAIR Target LLM — the model being attacked.

    Parameters
    ----------
    client : Groq
        Authenticated Groq client.
    model : str
        Model name for the target. Typically a smaller, deployed-style model.
    temperature : float
        Sampling temperature. Default 0 for deterministic, reproducible results.
    system_prompt : str
        The target's system prompt. Defaults to a minimal helpful assistant
        prompt so we test default alignment, not a hardened system.
    """

    def __init__(
        self,
        client: Groq,
        model: str = "llama-3.1-8b-instant",
        temperature: float = 0.0,
        system_prompt: str = TARGET_SYSTEM_PROMPT,
    ):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.system_prompt = system_prompt

    def query(self, prompt: str) -> str:
        """
        Send an adversarial prompt to the target model and return its response.

        Parameters
        ----------
        prompt : str
            The jailbreak prompt crafted by the attacker.

        Returns
        -------
        str
            The target model's raw text response.
        """
        logger.debug(f"Target prompt:\n{prompt[:300]}...")

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=1024,
        )

        text = response.choices[0].message.content.strip()
        logger.debug(f"Target response:\n{text[:300]}...")
        return text
