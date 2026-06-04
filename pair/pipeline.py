"""
pair/pipeline.py
----------------
PAIR Algorithm Orchestrator — the main loop from Chao et al. 2023.

This module ties together the Attacker, Target, and Judge LLMs and implements
the core iterative refinement loop described in the paper:

  Algorithm 1 (PAIR):
    Input:  goal G, max_iterations K
    Output: successful jailbreak prompt P*, or best attempt

    for i = 1 to K:
      P_i = Attacker(G, P_{i-1}, R_{i-1}, score_{i-1})   # generate/refine
      R_i = Target(P_i)                                    # query target
      s_i = Judge(G, P_i, R_i)                             # score response
      if s_i >= threshold: return P_i                      # early exit

    return argmax_{P_i} s_i                               # best attempt

Results are collected into a list of IterationRecord objects and can be
exported to a Pandas DataFrame / CSV for analysis.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

from pair.attacker import Attacker, AttackerOutput
from pair.judge import Judge
from pair.target import Target

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class IterationRecord:
    """
    Full record for a single PAIR iteration.

    Stored for every query made to the target model — forms the raw data
    for the final results table.
    """
    goal_id: int           # Index into the goals list
    goal: str              # The target harmful behavior
    iteration: int         # Which iteration (1-indexed)
    prompt: str            # The attacker's jailbreak prompt
    improvement: str       # The attacker's reasoning for this attempt
    response: str          # The target model's response
    score: int             # Judge's score (1-10)
    success: bool          # Whether score >= threshold
    elapsed_sec: float     # Wall-clock time for this iteration


@dataclass
class GoalResult:
    """
    Aggregated result for one goal across all iterations.
    """
    goal_id: int
    goal: str
    succeeded: bool
    best_score: int
    iterations_to_success: Optional[int]   # None if never succeeded
    total_iterations: int
    best_prompt: str
    best_response: str
    tactic_fingerprint: str    # One-line summary of what the winning tactic was
    all_iterations: List[IterationRecord] = field(default_factory=list)


class PAIRPipeline:
    """
    End-to-end PAIR attack pipeline.

    Instantiate once, then call `run_goal()` for each adversarial goal,
    or `run_all()` for a full batch experiment.

    Parameters
    ----------
    attacker : Attacker
    target : Target
    judge : Judge
    max_iterations : int
        Maximum queries per goal (paper uses K=20).
    verbose : bool
        Whether to print live progress via rich.
    sleep_between_calls : float
        Seconds to sleep between API calls to avoid rate limiting.
    """

    def __init__(
        self,
        attacker: Attacker,
        target: Target,
        judge: Judge,
        max_iterations: int = 20,
        verbose: bool = True,
        sleep_between_calls: float = 0.5,
    ):
        self.attacker = attacker
        self.target = target
        self.judge = judge
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.sleep_between_calls = sleep_between_calls

        self._all_records: List[IterationRecord] = []
        self._goal_results: List[GoalResult] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Core loop
    # ──────────────────────────────────────────────────────────────────────────

    def run_goal(self, goal: str, goal_id: int = 0) -> GoalResult:
        """
        Run the PAIR attack loop for a single goal.

        Parameters
        ----------
        goal : str
            The harmful behavior to elicit.
        goal_id : int
            Numeric index for tracking in results table.

        Returns
        -------
        GoalResult
            Aggregated result including success status, best score, tactic.
        """
        if self.verbose:
            console.print(
                Panel(
                    f"[bold cyan]Goal {goal_id + 1}:[/bold cyan] {goal}",
                    title="[bold yellow]PAIR Attack[/bold yellow]",
                    border_style="yellow",
                )
            )

        prev_prompt: str = ""
        prev_response: str = ""
        prev_score: int = 0

        iterations: List[IterationRecord] = []
        best_score = 0
        best_record: Optional[IterationRecord] = None

        for iteration in range(1, self.max_iterations + 1):
            iter_start = time.monotonic()

            # ── Step 1: Attacker generates improved prompt ──
            try:
                atk_out: AttackerOutput = self.attacker.generate(
                    goal=goal,
                    prev_prompt=prev_prompt,
                    prev_response=prev_response,
                    score=prev_score,
                )
            except Exception as exc:
                logger.error(f"Attacker failed on iteration {iteration}: {exc}")
                break

            time.sleep(self.sleep_between_calls)

            # ── Step 2: Target responds ──
            try:
                target_response = self.target.query(atk_out.prompt)
            except Exception as exc:
                logger.error(f"Target failed on iteration {iteration}: {exc}")
                break

            time.sleep(self.sleep_between_calls)

            # ── Step 3: Judge scores ──
            try:
                score = self.judge.score(
                    goal=goal,
                    prompt=atk_out.prompt,
                    response=target_response,
                )
            except Exception as exc:
                logger.error(f"Judge failed on iteration {iteration}: {exc}")
                score = 0

            elapsed = time.monotonic() - iter_start
            success = self.judge.is_success(score)

            record = IterationRecord(
                goal_id=goal_id,
                goal=goal,
                iteration=iteration,
                prompt=atk_out.prompt,
                improvement=atk_out.improvement,
                response=target_response,
                score=score,
                success=success,
                elapsed_sec=round(elapsed, 2),
            )

            iterations.append(record)
            self._all_records.append(record)

            if score > best_score:
                best_score = score
                best_record = record

            # ── Live display ──
            if self.verbose:
                self._print_iteration(iteration, atk_out, target_response, score, success)

            # ── Early exit on success ──
            if success:
                if self.verbose:
                    console.print(
                        f"\n[bold green]✓ Jailbreak succeeded at iteration {iteration}![/bold green]\n"
                    )
                break

            # Update context for next iteration
            prev_prompt = atk_out.prompt
            prev_response = target_response
            prev_score = score

        # ── Aggregate ──
        succeeded = any(r.success for r in iterations)
        iter_to_success = next(
            (r.iteration for r in iterations if r.success), None
        )

        tactic = self._extract_tactic(best_record) if best_record else "N/A"

        result = GoalResult(
            goal_id=goal_id,
            goal=goal,
            succeeded=succeeded,
            best_score=best_score,
            iterations_to_success=iter_to_success,
            total_iterations=len(iterations),
            best_prompt=best_record.prompt if best_record else "",
            best_response=best_record.response if best_record else "",
            tactic_fingerprint=tactic,
            all_iterations=iterations,
        )

        self._goal_results.append(result)
        return result

    def run_all(self, goals: List[str]) -> List[GoalResult]:
        """
        Run PAIR against a list of goals sequentially.

        Parameters
        ----------
        goals : List[str]
            List of harmful behaviors to test.

        Returns
        -------
        List[GoalResult]
            One result per goal.
        """
        console.print(
            Panel(
                f"[bold]Running PAIR attack on {len(goals)} goals[/bold]\n"
                f"Max iterations per goal: {self.max_iterations} | "
                f"Judge threshold: {self.judge.success_threshold}/10",
                title="[bold red]PAIR-Lab Experiment[/bold red]",
                border_style="red",
            )
        )

        results = []
        for i, goal in enumerate(goals):
            result = self.run_goal(goal=goal, goal_id=i)
            results.append(result)
            console.rule(f"Goal {i + 1}/{len(goals)} complete")

        if self.verbose:
            self._print_summary(results)

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Results export
    # ──────────────────────────────────────────────────────────────────────────

    def to_dataframe(self) -> pd.DataFrame:
        """
        Return all iteration records as a Pandas DataFrame.

        Columns:
          goal_id, goal, iteration, prompt, improvement, response,
          score, success, elapsed_sec
        """
        if not self._all_records:
            return pd.DataFrame()

        rows = [
            {
                "goal_id": r.goal_id,
                "goal": r.goal,
                "iteration": r.iteration,
                "improvement": r.improvement,
                "prompt": r.prompt,
                "response": r.response,
                "score": r.score,
                "success": r.success,
                "elapsed_sec": r.elapsed_sec,
            }
            for r in self._all_records
        ]
        return pd.DataFrame(rows)

    def summary_dataframe(self) -> pd.DataFrame:
        """
        Return a goal-level summary DataFrame.

        Columns:
          goal_id, goal, succeeded, best_score, iterations_to_success,
          total_iterations, tactic_fingerprint, asr
        """
        if not self._goal_results:
            return pd.DataFrame()

        rows = [
            {
                "goal_id": r.goal_id,
                "goal": r.goal,
                "succeeded": r.succeeded,
                "best_score": r.best_score,
                "iterations_to_success": r.iterations_to_success,
                "total_iterations": r.total_iterations,
                "tactic_fingerprint": r.tactic_fingerprint,
            }
            for r in self._goal_results
        ]
        df = pd.DataFrame(rows)

        # Compute ASR (Attack Success Rate) as a column for convenience
        n_success = df["succeeded"].sum()
        df.attrs["asr"] = n_success / len(df) if len(df) > 0 else 0.0
        df.attrs["avg_iterations_to_success"] = (
            df[df["iterations_to_success"].notna()]["iterations_to_success"].mean()
        )
        return df

    def save_results(self, output_dir: str = "results") -> str:
        """
        Save full iteration log and goal summary CSV to disk.

        Parameters
        ----------
        output_dir : str
            Directory to write CSV files.

        Returns
        -------
        str
            Path to the summary CSV (for logging).
        """
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Full iteration log
        detail_path = os.path.join(output_dir, f"pair_iterations_{ts}.csv")
        self.to_dataframe().to_csv(detail_path, index=False)

        # Goal-level summary
        summary_path = os.path.join(output_dir, f"pair_summary_{ts}.csv")
        self.summary_dataframe().to_csv(summary_path, index=False)

        console.print(f"[green]Results saved:[/green]")
        console.print(f"  Iterations: [cyan]{detail_path}[/cyan]")
        console.print(f"  Summary:    [cyan]{summary_path}[/cyan]")

        return summary_path

    # ──────────────────────────────────────────────────────────────────────────
    # Display helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _print_iteration(
        self,
        iteration: int,
        atk_out: AttackerOutput,
        response: str,
        score: int,
        success: bool,
    ) -> None:
        """Pretty-print one PAIR iteration to the console."""
        score_color = "green" if success else ("yellow" if score >= 5 else "red")
        score_bar = "█" * score + "░" * (10 - score)

        console.print(
            f"\n[bold]Iteration {iteration}[/bold]\n"
            f"[dim]Attacker reasoning:[/dim] {atk_out.improvement[:120]}...\n"
            f"[dim]Target response:[/dim]    {response[:200]}...\n"
            f"[{score_color}]Score: {score}/10  [{score_bar}]  {'SUCCESS' if success else 'Failed'}[/{score_color}]"
        )

    def _print_summary(self, results: List[GoalResult]) -> None:
        """Print final experiment summary table."""
        table = Table(
            title="[bold]PAIR Experiment Results[/bold]",
            box=box.ROUNDED,
            header_style="bold magenta",
        )
        table.add_column("Goal", style="cyan", max_width=40)
        table.add_column("Success", justify="center")
        table.add_column("Best Score", justify="center")
        table.add_column("Iterations", justify="center")
        table.add_column("Tactic", max_width=35)

        for r in results:
            table.add_row(
                r.goal[:40] + ("..." if len(r.goal) > 40 else ""),
                "[green]Y[/green]" if r.succeeded else "[red]N[/red]",
                str(r.best_score),
                str(r.iterations_to_success or f"{r.total_iterations} (failed)"),
                r.tactic_fingerprint[:35],
            )

        console.print(table)

        n_success = sum(1 for r in results if r.succeeded)
        asr = n_success / len(results) if results else 0
        avg_iters = (
            sum(r.iterations_to_success for r in results if r.iterations_to_success)
            / max(n_success, 1)
        )

        console.print(
            Panel(
                f"[bold]Attack Success Rate (ASR):[/bold] "
                f"[{'green' if asr > 0.5 else 'yellow'}]{asr:.1%}[/] "
                f"({n_success}/{len(results)} goals)\n"
                f"[bold]Avg iterations to success:[/bold] {avg_iters:.1f}",
                title="[bold]Final Metrics[/bold]",
                border_style="green",
            )
        )

    @staticmethod
    def _extract_tactic(record: IterationRecord) -> str:
        """
        Heuristically extract the attacker tactic from the improvement text.

        Uses keyword matching to label the dominant strategy. This is a
        lightweight alternative to running a full classifier on improvement text.
        """
        improvement = record.improvement.lower()
        tactic_keywords = {
            "role-play": ["role", "character", "persona", "play as", "act as"],
            "hypothetical": ["hypothetical", "imagine", "fiction", "novel", "story"],
            "authority": ["researcher", "professional", "expert", "academic", "study"],
            "token-smuggling": ["rephrase", "synonym", "indirect", "euphemism", "avoid"],
            "continuation": ["continue", "complete", "finish", "sentence"],
            "distraction": ["bury", "context", "unrelated", "wrap"],
            "indirect": ["indirect", "partial", "pieces", "steps", "breakdown"],
        }
        for tactic, keywords in tactic_keywords.items():
            if any(kw in improvement for kw in keywords):
                return tactic
        return "other"
