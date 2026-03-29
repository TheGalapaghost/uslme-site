#!/usr/bin/env python3
"""
reddit_monitor.py — Reddit-driven USMLE content pipeline

Monitors USMLE subreddits for high-engagement questions, generates
targeted articles, and drafts Reddit comments for manual posting.

Usage:
    # Scan subreddits and show opportunities (no article generation)
    python reddit_monitor.py --scan

    # Scan and auto-generate articles + draft comments for top matches
    python reddit_monitor.py --generate --limit 3

    # Scan a specific subreddit only
    python reddit_monitor.py --scan --subreddits step1

    # Use a different model
    python reddit_monitor.py --generate --model claude-sonnet-4-6

Setup:
    1. pip install praw anthropic python-dotenv
    2. Create a Reddit app at https://www.reddit.com/prefs/apps/
       - Choose "script" type
       - Redirect URI: http://localhost:8080
    3. Add to .env:
       REDDIT_CLIENT_ID=your_client_id
       REDDIT_CLIENT_SECRET=your_client_secret
       REDDIT_USER_AGENT=usmleprep-monitor/1.0
       ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import praw
except ImportError:
    sys.exit("Missing dependency: pip install praw")

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SITE_URL = "https://usmleprep.guide"
POSTS_DIR = Path(__file__).parent / "posts"
DRAFTS_DIR = Path(__file__).parent / "reddit_drafts"
SEEN_FILE = Path(__file__).parent / ".reddit_seen.json"
DEFAULT_MODEL = "claude-sonnet-4-6"

SUBREDDITS = {
    "step1": "USMLE Step 1",
    "step2": "USMLE Step 2 CK",
    "step3": "USMLE Step 3",
    "medicalschool": "Medical school general",
    "medschool": "Med school community",
}

# Keywords that signal a post is asking for study advice/resources
TRIGGER_KEYWORDS = [
    r"best\s+resource",
    r"how\s+to\s+study",
    r"study\s+(schedule|plan|strategy|tips)",
    r"what\s+(should|do)\s+I\s+(use|study)",
    r"recommend",
    r"high[\s-]?yield",
    r"anki|anking",
    r"uworld|amboss|sketchy|pathoma|first\s+aid|boards\s+and\s+beyond",
    r"qbank",
    r"step\s+[123]\s+(prep|study|resource|advice|help|score)",
    r"dedicated\s+(period|study)",
    r"how\s+(long|many\s+weeks)",
    r"pass(ing)?\s+step",
    r"score\s+\d{3}",
    r"improving?\s+(my\s+)?score",
    r"weak\s+(area|subject|in)",
    r"struggling\s+with",
    r"failed?\s+step",
    r"retake",
    r"nbme\s+(score|practice)",
    r"cardiology|pharmacology|pathology|biochemistry|microbiology|neurology",
    r"mnemonics?",
]

TRIGGER_PATTERN = re.compile("|".join(TRIGGER_KEYWORDS), re.IGNORECASE)

COMMENT_SYSTEM_PROMPT = """You are a helpful medical student who runs usmleprep.guide. You write
genuine, helpful Reddit comments that answer the person's question directly. You happen to have
a relevant article on your site that goes deeper.

Rules:
- Answer the question FIRST with real, useful advice (3-5 sentences minimum)
- Be conversational and authentic — you're a fellow med student, not a marketer
- Only mention your article at the end as a "I wrote a deeper guide on this" reference
- Never be pushy or salesy
- Use Reddit-appropriate tone (casual, helpful, first-person)
- Include specific, actionable tips from your own experience"""

COMMENT_PROMPT_TEMPLATE = """A Reddit user posted this in r/{subreddit}:

Title: {title}
Body: {body}

I have a relevant article on my site:
- Article title: "{article_title}"
- Article URL: {article_url}
- Article summary: {article_description}

Write a genuine, helpful Reddit comment that:
1. Directly answers their question with real advice
2. Naturally mentions the article at the end (not the main focus)
3. Feels like a real med student helping out, not a bot

Write the comment now:"""

KEYWORD_EXTRACT_PROMPT = """Analyze this Reddit post and extract a single SEO keyword phrase
(3-6 words) that would make a good article topic for a USMLE prep website.

The keyword should be something a med student would search on Google.

Reddit post title: {title}
Reddit post body: {body}
Subreddit: r/{subreddit}

Return ONLY the keyword phrase, nothing else. Examples of good keywords:
- "usmle step 1 cardiology high yield"
- "best anki decks step 2 ck"
- "how to improve uworld scores"
- "usmle step 1 study schedule 6 weeks"
"""


# ---------------------------------------------------------------------------
# Reddit client
# ---------------------------------------------------------------------------

def get_reddit() -> praw.Reddit:
    """Initialize Reddit API client."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "usmleprep-monitor/1.0")

    if not client_id or not client_secret:
        sys.exit(
            "[error] Reddit API credentials not set.\n"
            "  1. Create an app at https://www.reddit.com/prefs/apps/\n"
            "  2. Add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to .env"
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def load_seen() -> set:
    """Load previously seen post IDs."""
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set) -> None:
    """Persist seen post IDs."""
    SEEN_FILE.write_text(json.dumps(list(seen)[-500:]))  # Keep last 500


def get_existing_slugs() -> set:
    """Get slugs of articles we already have."""
    slugs = set()
    for f in POSTS_DIR.glob("*.md"):
        slugs.add(f.stem)
    return slugs


def scan_subreddits(
    reddit: praw.Reddit,
    subreddit_names: list[str],
    time_filter: str = "week",
    post_limit: int = 50,
) -> list[dict]:
    """Scan subreddits for relevant USMLE questions."""
    seen = load_seen()
    opportunities = []

    for sub_name in subreddit_names:
        if sub_name not in SUBREDDITS:
            print(f"[warn] Unknown subreddit: r/{sub_name}, skipping")
            continue

        print(f"[scan] Scanning r/{sub_name}...")
        subreddit = reddit.subreddit(sub_name)

        for post in subreddit.top(time_filter=time_filter, limit=post_limit):
            if post.id in seen:
                continue

            text = f"{post.title} {post.selftext}"
            if not TRIGGER_PATTERN.search(text):
                continue

            # Score = upvotes + comments (engagement proxy)
            engagement = post.score + post.num_comments

            opportunities.append({
                "id": post.id,
                "subreddit": sub_name,
                "title": post.title,
                "body": post.selftext[:1500],  # Truncate long posts
                "url": f"https://reddit.com{post.permalink}",
                "score": post.score,
                "comments": post.num_comments,
                "engagement": engagement,
                "created": datetime.fromtimestamp(
                    post.created_utc, tz=timezone.utc
                ).isoformat(),
            })

            seen.add(post.id)

        print(f"  Found {sum(1 for o in opportunities if o['subreddit'] == sub_name)} opportunities")

    save_seen(seen)

    # Sort by engagement
    opportunities.sort(key=lambda x: x["engagement"], reverse=True)
    return opportunities


# ---------------------------------------------------------------------------
# Article + comment generation
# ---------------------------------------------------------------------------

def extract_keyword(
    client: anthropic.Anthropic, post: dict, model: str
) -> str:
    """Use Claude to extract a target keyword from a Reddit post."""
    response = client.messages.create(
        model=model,
        max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": KEYWORD_EXTRACT_PROMPT.format(
                    title=post["title"],
                    body=post["body"][:800],
                    subreddit=post["subreddit"],
                ),
            }
        ],
    )
    return response.content[0].text.strip().strip('"')


def generate_article(keyword: str, model: str) -> Path | None:
    """Run the article pipeline for a keyword. Returns the output path."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "pipeline.py"),
            "--keyword", keyword,
            "--model", model,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  [error] Article generation failed: {result.stderr}")
        return None

    print(result.stdout)

    # Find the generated file
    slug = slugify(keyword)
    path = POSTS_DIR / f"{slug}.md"
    return path if path.exists() else None


def get_article_meta(path: Path) -> dict:
    """Extract frontmatter from a markdown file."""
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not match:
        return {}

    meta = {}
    for line in match.group(1).split("\n"):
        if ":" in line and not line.startswith("  "):
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip('"')
    return meta


def draft_comment(
    client: anthropic.Anthropic,
    post: dict,
    article_path: Path,
    model: str,
) -> str:
    """Generate a Reddit comment draft."""
    meta = get_article_meta(article_path)
    slug = meta.get("slug", article_path.stem)
    article_url = f"{SITE_URL}/blog/{slug}"

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=COMMENT_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": COMMENT_PROMPT_TEMPLATE.format(
                    subreddit=post["subreddit"],
                    title=post["title"],
                    body=post["body"][:800],
                    article_title=meta.get("title", "USMLE Study Guide"),
                    article_url=article_url,
                    article_description=meta.get("description", ""),
                ),
            }
        ],
    )
    return response.content[0].text.strip()


def save_draft(post: dict, keyword: str, article_path: Path | None, comment: str) -> Path:
    """Save a draft file with all the info needed for manual posting."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    draft_path = DRAFTS_DIR / f"{timestamp}_{post['subreddit']}_{post['id']}.md"

    meta = get_article_meta(article_path) if article_path else {}
    slug = meta.get("slug", slugify(keyword))

    content = f"""# Reddit Draft — {post['subreddit']}

## Reddit Post
- **Subreddit:** r/{post['subreddit']}
- **Title:** {post['title']}
- **URL:** {post['url']}
- **Score:** {post['score']} upvotes, {post['comments']} comments
- **Posted:** {post['created']}

## Generated Article
- **Keyword:** {keyword}
- **Article:** {meta.get('title', 'N/A')}
- **URL:** {SITE_URL}/blog/{slug}
- **File:** {article_path or 'N/A'}

## Draft Comment (copy-paste to Reddit)

{comment}

---
*Review this comment before posting. Edit for tone and accuracy.*
"""

    draft_path.write_text(content, encoding="utf-8")
    return draft_path


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].rstrip("-")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor Reddit for USMLE content opportunities."
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan subreddits and show opportunities",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Scan + generate articles + draft comments",
    )
    parser.add_argument(
        "--subreddits", nargs="+", default=list(SUBREDDITS.keys()),
        help=f"Subreddits to scan (default: {' '.join(SUBREDDITS.keys())})",
    )
    parser.add_argument(
        "--limit", type=int, default=3,
        help="Max articles to generate per run (default: 3)",
    )
    parser.add_argument(
        "--time", default="week",
        choices=["day", "week", "month"],
        help="Time filter for top posts (default: week)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Claude model (default: {DEFAULT_MODEL})",
    )

    args = parser.parse_args()

    if not args.scan and not args.generate:
        parser.error("Specify --scan or --generate")

    # Init Reddit
    reddit = get_reddit()
    print(f"[reddit] Connected (read-only)\n")

    # Scan
    opportunities = scan_subreddits(
        reddit, args.subreddits, time_filter=args.time
    )

    if not opportunities:
        print("\n[done] No new opportunities found.")
        return

    print(f"\n{'='*70}")
    print(f" Found {len(opportunities)} opportunities (sorted by engagement)")
    print(f"{'='*70}\n")

    for i, opp in enumerate(opportunities, 1):
        print(f"  {i}. [{opp['engagement']:>4} pts] r/{opp['subreddit']}")
        print(f"     {opp['title'][:80]}")
        print(f"     {opp['url']}")
        print()

    if not args.generate:
        print("[done] Use --generate to create articles and draft comments.")
        return

    # Generate articles for top opportunities
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("[error] ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=api_key)
    existing_slugs = get_existing_slugs()
    generated = 0

    for opp in opportunities:
        if generated >= args.limit:
            break

        print(f"\n{'─'*70}")
        print(f"Processing: {opp['title'][:70]}...")
        print(f"{'─'*70}")

        # Extract keyword
        print("[1/3] Extracting keyword...")
        keyword = extract_keyword(client, opp, args.model)
        slug = slugify(keyword)
        print(f"  Keyword: {keyword}")
        print(f"  Slug: {slug}")

        # Check if we already have this article
        if slug in existing_slugs:
            print(f"  [skip] Article already exists: {slug}.md")
            # Still draft a comment pointing to existing article
            article_path = POSTS_DIR / f"{slug}.md"
            print("[2/3] Skipping article generation (exists)")
        else:
            # Generate article
            print("[2/3] Generating article...")
            article_path = generate_article(keyword, args.model)
            if not article_path:
                print("  [skip] Article generation failed")
                continue
            existing_slugs.add(slug)

        # Draft comment
        print("[3/3] Drafting Reddit comment...")
        comment = draft_comment(client, opp, article_path, args.model)

        # Save draft
        draft_path = save_draft(opp, keyword, article_path, comment)
        print(f"\n  Draft saved: {draft_path}")
        print(f"  Comment preview:")
        print(f"  {'·'*50}")
        for line in comment.split("\n")[:6]:
            print(f"    {line}")
        print(f"    ...")
        print(f"  {'·'*50}")

        generated += 1

    print(f"\n{'='*70}")
    print(f" Generated {generated} article(s) + comment drafts")
    print(f" Review drafts in: {DRAFTS_DIR}/")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
