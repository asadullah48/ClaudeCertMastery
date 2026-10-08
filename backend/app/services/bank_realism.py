"""Format-leak statistics for an authored question bank.

A practice bank can give its answers away through format alone: the key is the longest
option, it sits on the same letter, or the distractors are the ones saying "always".
A candidate who learns those tells scores well here and learns nothing about the exam,
so the practice score overstates readiness. These are the measurements that catch it.

Pure functions over the YAML specs -- no database -- so the CI gate
(tests/test_bank_realism.py) and the README's published figures come from one
implementation:

    python -m app.services.bank_realism
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SEED_DIR = Path(__file__).resolve().parents[2] / "seed_data" / "ccao_f"

# Gate thresholds (see tests/test_bank_realism.py).
MAX_LONGEST_IS_KEY = 0.35   # chance with 4 options is 0.25
MAX_LABEL_SHARE = 0.35      # chance with 4 labels is 0.25
MAX_ABSOLUTE_WRONG = 0.75   # absolute wording should not reliably mark a distractor

ABSOLUTE_WORDS = re.compile(r"\b(always|never|only|all)\b", re.IGNORECASE)


@dataclass
class BankStats:
    scope: str
    items: int = 0
    single_answer: int = 0
    mr_items: int = 0
    longest_is_key: int = 0
    label_counts: Counter = field(default_factory=Counter)
    absolute_options: int = 0
    absolute_wrong: int = 0
    mr_missing_select_count: list[str] = field(default_factory=list)
    option_lengths: list[int] = field(default_factory=list)
    table_items: int = 0

    @property
    def longest_is_key_rate(self) -> float:
        return self.longest_is_key / self.single_answer if self.single_answer else 0.0

    @property
    def max_label_share(self) -> tuple[str, float]:
        total = sum(self.label_counts.values())
        if not total:
            return ("-", 0.0)
        label, n = max(sorted(self.label_counts.items()), key=lambda kv: kv[1])
        return (label, n / total)

    @property
    def absolute_wrong_rate(self) -> float:
        return self.absolute_wrong / self.absolute_options if self.absolute_options else 0.0

    @property
    def median_option_length(self) -> float:
        xs = sorted(self.option_lengths)
        if not xs:
            return 0.0
        mid = len(xs) // 2
        return float(xs[mid]) if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2

    def violations(self) -> list[str]:
        out = []
        if self.longest_is_key_rate > MAX_LONGEST_IS_KEY:
            out.append(
                f"{self.scope}: key is the uniquely longest option in "
                f"{self.longest_is_key}/{self.single_answer} single-answer items "
                f"({self.longest_is_key_rate:.0%} > {MAX_LONGEST_IS_KEY:.0%})"
            )
        label, share = self.max_label_share
        if share > MAX_LABEL_SHARE:
            out.append(
                f"{self.scope}: label {label} holds {share:.0%} of correct options "
                f"(> {MAX_LABEL_SHARE:.0%}; {dict(sorted(self.label_counts.items()))})"
            )
        if self.absolute_wrong_rate > MAX_ABSOLUTE_WRONG:
            out.append(
                f"{self.scope}: absolute-word options wrong "
                f"{self.absolute_wrong}/{self.absolute_options} "
                f"({self.absolute_wrong_rate:.0%} > {MAX_ABSOLUTE_WRONG:.0%})"
            )
        if self.mr_missing_select_count:
            out.append(
                f"{self.scope}: MR items without select_count: "
                + ", ".join(self.mr_missing_select_count)
            )
        return out


def _add(stats: BankStats, q: dict) -> None:
    options = q["options"]
    stats.items += 1
    if q.get("table"):
        stats.table_items += 1
    for o in options:
        stats.option_lengths.append(len(o["text"]))
        if o.get("correct"):
            stats.label_counts[o["label"]] += 1
        if ABSOLUTE_WORDS.search(o["text"]):
            stats.absolute_options += 1
            stats.absolute_wrong += not o.get("correct")

    if q["type"] == "mr":
        stats.mr_items += 1
        if not q.get("select_count"):
            stats.mr_missing_select_count.append(q["external_id"])
        return

    stats.single_answer += 1
    key = next(o for o in options if o.get("correct"))
    others = [len(o["text"]) for o in options if o is not key]
    if len(key["text"]) > max(others):
        stats.longest_is_key += 1


def measure(seed_dir: Path = SEED_DIR) -> tuple[BankStats, list[BankStats]]:
    """Return (overall, [per-domain]) statistics for every YAML file in seed_dir."""
    overall = BankStats("overall")
    per_domain = []
    for path in sorted(seed_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        domain = BankStats(data["domain"]["code"])
        for q in data["questions"]:
            _add(domain, q)
            _add(overall, q)
        per_domain.append(domain)
    return overall, per_domain


def _row(s: BankStats) -> str:
    label, share = s.max_label_share
    return (
        f"{s.scope:<8}{s.items:>6}{s.single_answer:>6}{s.mr_items:>4}{s.table_items:>6}"
        f"  {s.longest_is_key:>3}/{s.single_answer:<3} {s.longest_is_key_rate:>4.0%}"
        f"   {label} {share:>4.0%}"
        f"   {s.absolute_wrong:>3}/{s.absolute_options:<3} {s.absolute_wrong_rate:>4.0%}"
        f"  {s.median_option_length:>6.0f}"
    )


def main() -> int:
    overall, per_domain = measure()
    print(
        f"{'scope':<8}{'items':>6}{'mcq':>6}{'mr':>4}{'table':>6}"
        f"  {'longest=key':>13}   {'top label':>8}   {'absolute wrong':>14}  {'median':>6}"
    )
    for s in [*per_domain, overall]:
        print(_row(s))
    print("correct-option labels (overall):", dict(sorted(overall.label_counts.items())))
    problems = [v for s in [*per_domain, overall] for v in s.violations()]
    print(f"violations: {len(problems)}")
    for v in problems:
        print("  -", v)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
