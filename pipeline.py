#!/usr/bin/env python3
"""
pipeline.py — USMLE SEO Article Generator

Usage:
    python pipeline.py --keyword "usmle step 1 cardiology high yield"
    python pipeline.py --keyword "best anki decks step 2" --model claude-3-5-sonnet-20241022

Requires:
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY set in environment or .env file
"""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading is optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POSTS_DIR = Path(__file__).parent / "posts"
DEFAULT_MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """You are an expert medical education content writer specializing in USMLE exam preparation.
You write detailed, evidence-based articles that genuinely help medical students pass their boards.
Your tone is authoritative yet approachable — like advice from a high-scoring senior resident.
You naturally incorporate affiliate-friendly mentions of top USMLE resources (UWorld, First Aid,
Anki/Anking, Sketchy, Pathoma, Boards & Beyond, Amboss) where relevant and honest.

When mentioning physical books, use these Amazon affiliate links:
- First Aid for Step 1: [First Aid](https://www.amazon.com/First-Aid-USMLE-Step-2024/dp/1264946643?tag=uslmesite-20)
- First Aid for Step 2 CK: [First Aid for Step 2 CK](https://www.amazon.com/First-Aid-USMLE-Step-2/dp/1264855133?tag=uslmesite-20)
- Pathoma: [Pathoma](https://www.amazon.com/Pathoma-Fundamentals-Pathology-Husain-Sattar/dp/0983224633?tag=uslmesite-20)
Include these links naturally the first time each book is mentioned in the article."""

ARTICLE_PROMPT_TEMPLATE = """Write a comprehensive, SEO-optimized article targeting the keyword: "{keyword}"

Requirements:
- Length: approximately 1,500 words
- Structure: Use H2 and H3 headings to break up content (markdown format)
- Tone: Expert, practical, and student-friendly
- Include: actionable tips, specific resource recommendations, and at least one comparison table where appropriate
- Include a brief intro paragraph that hooks the reader and naturally includes the target keyword
- End with a "Final Thoughts" or "Bottom Line" section
- Do NOT include the article title in the body (it will be added as frontmatter)
- Do NOT add an affiliate disclosure paragraph (it is added automatically)

Target keyword: {keyword}

Write the full article body in markdown now:"""

FRONTMATTER_PROMPT_TEMPLATE = """Based on this target keyword, generate SEO-optimized frontmatter fields.

Keyword: "{keyword}"

Return ONLY a JSON object with these exact fields (no markdown code blocks, just raw JSON):
{{
  "title": "...",
  "description": "...",
  "tags": ["tag1", "tag2", "tag3"]
}}

Rules:
- title: compelling, keyword-rich, under 65 characters, title case
- description: 140–160 character meta description, includes keyword naturally
- tags: 2–4 tags from: Step 1, Step 2, Step 3, Qbank, Resources, Study Guide, Pharmacology, Pathology, Biochemistry, Microbiology, Cardiology, Neurology, Psychiatry, Internal Medicine, Surgery, Pediatrics, Anki, Schedule"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].rstrip("-")


def get_frontmatter(client: anthropic.Anthropic, keyword: str, model: str) -> dict:
    """Use Claude to generate SEO frontmatter for the keyword."""
    import json

    response = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": FRONTMATTER_PROMPT_TEMPLATE.format(keyword=keyword),
            }
        ],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if Claude adds them
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)

    data = json.loads(raw)
    return data


def generate_article_body(client: anthropic.Anthropic, keyword: str, model: str) -> str:
    """Use Claude to write the full article body."""
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": ARTICLE_PROMPT_TEMPLATE.format(keyword=keyword),
            }
        ],
    )
    return response.content[0].text.strip()


def build_markdown(frontmatter: dict, slug: str, body: str) -> str:
    """Assemble the full markdown file with YAML frontmatter."""
    today = date.today().isoformat()
    tags_yaml = "\n".join(f'  - "{tag}"' for tag in frontmatter.get("tags", []))

    fm = f"""---
title: "{frontmatter['title']}"
description: "{frontmatter['description']}"
date: "{today}"
slug: "{slug}"
tags:
{tags_yaml}
---"""

    affiliate_footer = (
        "\n\n---\n\n"
        "> **Disclosure:** Some links in this article are affiliate links. "
        "We may earn a commission if you purchase through them, at no extra cost to you. "
        "See our [full disclaimer](/disclaimer)."
    )

    return f"{fm}\n\n{body}{affiliate_footer}\n"


def save_post(slug: str, content: str) -> Path:
    """Write the markdown file to the posts directory."""
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = POSTS_DIR / f"{slug}.md"

    if output_path.exists():
        print(f"[warning] File already exists, overwriting: {output_path}")

    output_path.write_text(content, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an SEO article for a USMLE keyword.")
    parser.add_argument("--keyword", required=True, help="Target SEO keyword for the article")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="Print output without saving to disk")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("[error] ANTHROPIC_API_KEY environment variable not set.")

    client = anthropic.Anthropic(api_key=api_key)
    keyword = args.keyword.strip()
    slug = slugify(keyword)

    print(f"[pipeline] Keyword : {keyword}")
    print(f"[pipeline] Slug    : {slug}")
    print(f"[pipeline] Model   : {args.model}")
    print()

    print("[1/2] Generating frontmatter...")
    try:
        frontmatter = get_frontmatter(client, keyword, args.model)
    except Exception as e:
        sys.exit(f"[error] Frontmatter generation failed: {e}")

    print(f"      Title: {frontmatter.get('title')}")
    print(f"      Tags : {frontmatter.get('tags')}")
    print()

    print("[2/2] Writing article body (~1500 words)...")
    try:
        body = generate_article_body(client, keyword, args.model)
    except Exception as e:
        sys.exit(f"[error] Article generation failed: {e}")

    markdown = build_markdown(frontmatter, slug, body)

    if args.dry_run:
        print("\n" + "=" * 60)
        print(markdown)
        print("=" * 60)
        print("[dry-run] File not saved.")
    else:
        output_path = save_post(slug, markdown)
        print(f"\n[done] Article saved to: {output_path}")
        print(f"       Word count (approx): {len(body.split())}")


if __name__ == "__main__":
    main()
