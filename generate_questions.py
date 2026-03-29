#!/usr/bin/env python3
"""
generate_questions.py — Bulk USMLE question generator

Usage:
    python generate_questions.py --step 1 --count 10
    python generate_questions.py --step 2 --count 10 --model claude-sonnet-4-6

Appends generated questions to src/data/questions.json.

Requires:
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY set in environment or .env file
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

QUESTIONS_FILE = Path(__file__).parent / "src" / "data" / "questions.json"
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a USMLE question writer with expertise in creating high-quality,
NBME-style multiple choice questions. Your questions should:
- Use clinical vignette format with realistic patient presentations
- Have exactly 5 answer choices (one correct, four plausible distractors)
- Test application of knowledge, not just recall
- Include detailed explanations that teach the underlying concept
- Include a concise "high yield" takeaway fact"""

QUESTION_PROMPT = """Generate {count} USMLE Step {step} style multiple-choice questions.

Each question must cover a DIFFERENT topic. Vary across these areas:
- Step 1: cardiology, pulmonology, GI, renal, endocrine, neurology, psychiatry, hematology,
  immunology, microbiology, pharmacology, biochemistry, pathology, anatomy, embryology
- Step 2: internal medicine, surgery, OB/GYN, pediatrics, psychiatry, emergency medicine,
  preventive medicine, ethics, biostatistics

Return ONLY a JSON array (no markdown code blocks) with this exact structure:
[
  {{
    "id": "s{step}-XXX",
    "stem": "Clinical vignette question text...",
    "choices": ["Choice A", "Choice B", "Choice C", "Choice D", "Choice E"],
    "answer": 0,
    "explanation": "Detailed explanation of the correct answer and why others are wrong...",
    "topic": "Topic Name",
    "highYield": "One-line high-yield takeaway fact."
  }}
]

Important:
- "answer" is a 0-based index (0=A, 1=B, 2=C, 3=D, 4=E)
- Vary the correct answer position (don't always make it A or B)
- Make distractors plausible — avoid obviously wrong choices
- Each question should be self-contained and clinically accurate
- Number IDs sequentially starting from s{step}-{start_id:03d}

Generate exactly {count} questions now:"""


def load_questions() -> dict:
    """Load existing question bank."""
    if QUESTIONS_FILE.exists():
        return json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    return {"step1": [], "step2": []}


def save_questions(data: dict) -> None:
    """Save question bank."""
    QUESTIONS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generate_questions(
    client: anthropic.Anthropic,
    step: int,
    count: int,
    start_id: int,
    model: str,
) -> list:
    """Generate questions via Claude API."""
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": QUESTION_PROMPT.format(
                    step=step, count=count, start_id=start_id
                ),
            }
        ],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    import re
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)

    return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate USMLE practice questions.")
    parser.add_argument("--step", type=int, required=True, choices=[1, 2], help="Step 1 or Step 2")
    parser.add_argument("--count", type=int, default=10, help="Number of questions to generate")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("[error] ANTHROPIC_API_KEY environment variable not set.")

    client = anthropic.Anthropic(api_key=api_key)
    data = load_questions()

    step_key = f"step{args.step}"
    existing = len(data.get(step_key, []))
    start_id = existing + 1

    print(f"[generate] Step {args.step} — generating {args.count} questions (starting ID: {start_id})")
    print(f"[generate] Model: {args.model}")
    print()

    try:
        new_questions = generate_questions(client, args.step, args.count, start_id, args.model)
    except Exception as e:
        sys.exit(f"[error] Generation failed: {e}")

    print(f"[generate] Generated {len(new_questions)} questions:")
    for q in new_questions:
        print(f"  - [{q['topic']}] {q['stem'][:80]}...")

    data[step_key] = data.get(step_key, []) + new_questions
    save_questions(data)

    print(f"\n[done] Question bank now has {len(data['step1'])} Step 1 and {len(data['step2'])} Step 2 questions.")
    print(f"       Saved to: {QUESTIONS_FILE}")


if __name__ == "__main__":
    main()
