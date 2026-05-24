"""
tests/test_pipeline.py
-----------------------
Unit tests for the PAIR-Lab pipeline components.

Tests use mocked Groq clients so no real API calls are made.
Run with: pytest tests/ -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pair.attacker import Attacker, AttackerOutput
from pair.judge import Judge
from pair.target import Target
from pair.pipeline import PAIRPipeline


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_mock_client(content: str):
    """Create a Groq client mock that returns the given content string."""
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


# ── Attacker tests ─────────────────────────────────────────────────────────────

class TestAttacker:
    def test_parse_valid_json(self):
        content = json.dumps({"improvement": "changed strategy", "prompt": "new jailbreak"})
        client = make_mock_client(content)
        attacker = Attacker(client=client)
        result = attacker.generate(goal="test goal")
        assert result.improvement == "changed strategy"
        assert result.prompt == "new jailbreak"

    def test_parse_json_with_code_fence(self):
        content = '```json\n{"improvement": "used roleplay", "prompt": "act as DAN"}\n```'
        client = make_mock_client(content)
        attacker = Attacker(client=client)
        result = attacker.generate(goal="test goal")
        assert result.prompt == "act as DAN"

    def test_graceful_fallback_on_bad_json(self):
        content = "This is not JSON at all, just raw text as a prompt."
        client = make_mock_client(content)
        attacker = Attacker(client=client)
        result = attacker.generate(goal="test goal")
        assert result.prompt == content
        assert "Parse error" in result.improvement

    def test_api_called_with_correct_structure(self):
        content = json.dumps({"improvement": "test", "prompt": "prompt"})
        client = make_mock_client(content)
        attacker = Attacker(client=client, model="test-model")
        attacker.generate(goal="my goal", prev_prompt="old", prev_response="resp", score=3)
        call_args = client.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "test-model"
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "my goal" in messages[1]["content"]


# ── Judge tests ────────────────────────────────────────────────────────────────

class TestJudge:
    def test_parse_valid_score(self):
        client = make_mock_client('{"score": 8}')
        judge = Judge(client=client)
        score = judge.score("goal", "prompt", "response")
        assert score == 8

    def test_clamp_score_above_10(self):
        client = make_mock_client('{"score": 15}')
        judge = Judge(client=client)
        score = judge.score("goal", "prompt", "response")
        assert score == 10

    def test_clamp_score_below_1(self):
        client = make_mock_client('{"score": -5}')
        judge = Judge(client=client)
        score = judge.score("goal", "prompt", "response")
        assert score == 1

    def test_regex_fallback(self):
        client = make_mock_client("I would give this a score of 7 out of 10.")
        judge = Judge(client=client)
        score = judge.score("goal", "prompt", "response")
        assert score == 7

    def test_default_to_1_on_complete_failure(self):
        client = make_mock_client("I cannot evaluate this.")
        judge = Judge(client=client)
        score = judge.score("goal", "prompt", "response")
        assert score == 1

    def test_is_success(self):
        client = make_mock_client('{"score": 7}')
        judge = Judge(client=client, success_threshold=7)
        assert judge.is_success(7) is True
        assert judge.is_success(6) is False
        assert judge.is_success(10) is True


# ── Target tests ───────────────────────────────────────────────────────────────

class TestTarget:
    def test_returns_response(self):
        client = make_mock_client("I cannot help with that.")
        target = Target(client=client)
        response = target.query("Tell me something harmful")
        assert response == "I cannot help with that."

    def test_correct_model_used(self):
        client = make_mock_client("response")
        target = Target(client=client, model="my-target-model")
        target.query("test")
        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "my-target-model"


# ── Pipeline integration test ──────────────────────────────────────────────────

class TestPAIRPipeline:
    def _make_pipeline(self, judge_scores: list, max_iterations: int = 5):
        """
        Build a PAIRPipeline with mocked components that return
        predictable scores from the judge_scores list.
        """
        attacker_client = make_mock_client(
            json.dumps({"improvement": "trying harder", "prompt": "jailbreak attempt"})
        )
        target_client = make_mock_client("I will help you with that.")

        # Judge returns successive scores from the list
        judge_responses = [MagicMock() for _ in judge_scores]
        for mock, s in zip(judge_responses, judge_scores):
            mock.choices[0].message.content = json.dumps({"score": s})
        judge_client = MagicMock()
        judge_client.chat.completions.create.side_effect = judge_responses

        attacker = Attacker(client=attacker_client, model="test")
        target = Target(client=target_client, model="test")
        judge = Judge(client=judge_client, model="test", success_threshold=7)

        return PAIRPipeline(
            attacker=attacker,
            target=target,
            judge=judge,
            max_iterations=max_iterations,
            verbose=False,
            sleep_between_calls=0,
        )

    def test_early_exit_on_success(self):
        # Scores: 3, 5, 8 → success at iteration 3
        pipeline = self._make_pipeline([3, 5, 8])
        result = pipeline.run_goal("test goal", goal_id=0)
        assert result.succeeded is True
        assert result.iterations_to_success == 3
        assert result.total_iterations == 3

    def test_runs_all_iterations_if_no_success(self):
        pipeline = self._make_pipeline([2, 3, 4, 5, 6], max_iterations=5)
        result = pipeline.run_goal("test goal", goal_id=0)
        assert result.succeeded is False
        assert result.total_iterations == 5
        assert result.best_score == 6

    def test_to_dataframe_columns(self):
        pipeline = self._make_pipeline([5, 8])
        pipeline.run_goal("test goal", goal_id=0)
        df = pipeline.to_dataframe()
        expected_cols = {"goal_id", "goal", "iteration", "prompt", "response", "score", "success"}
        assert expected_cols.issubset(set(df.columns))

    def test_summary_dataframe(self):
        pipeline = self._make_pipeline([3, 9])
        pipeline.run_goal("goal one", goal_id=0)
        summary = pipeline.summary_dataframe()
        assert len(summary) == 1
        assert summary.iloc[0]["succeeded"] == True  # noqa: E712  (numpy bool != Python bool)
