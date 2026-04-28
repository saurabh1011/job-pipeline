#!/usr/bin/env python3
"""Story ingestion CLI.

Usage:
    # Process new files from inbox/ → review/ (generates Claude headers)
    ANTHROPIC_API_KEY=... python3 ingest.py

    # After editing headers in review/, push everything to stories/ + rebuild index
    ANTHROPIC_API_KEY=... python3 ingest.py --finalize

    # Do both in one step (process inbox then finalize)
    ANTHROPIC_API_KEY=... python3 ingest.py --all

Workflow:
    1. Export your Google Doc as Word (.docx) via File → Download → Microsoft Word
    2. Drop the .docx file into profile/inbox/
    3. Run: python3 ingest.py
    4. Open profile/review/<filename>.md and edit the generated header if needed
    5. Run: python3 ingest.py --finalize
       → files are copied to profile/stories/ and profile/stories_index.md is rebuilt
"""
import os
import sys
import click
import yaml
from pipeline.ingester import StoryIngester
from pipeline.llm import create_provider

PROFILE_DIR = "profile"
INBOX_DIR = os.path.join(PROFILE_DIR, "inbox")
REVIEW_DIR = os.path.join(PROFILE_DIR, "review")
STORIES_DIR = os.path.join(PROFILE_DIR, "stories")


def _load_preferences() -> dict:
    try:
        with open("config/preferences.yaml") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _make_ingester(provider) -> StoryIngester:
    return StoryIngester(
        inbox_dir=INBOX_DIR,
        review_dir=REVIEW_DIR,
        stories_dir=STORIES_DIR,
        profile_dir=PROFILE_DIR,
        provider=provider,
    )


@click.command()
@click.option("--finalize", is_flag=True, help="Copy review/ → stories/ and rebuild index")
@click.option("--all", "run_all", is_flag=True, help="Process inbox then finalize in one step")
def main(finalize, run_all):
    """Story ingestion pipeline — inbox → review → stories."""
    prefs = _load_preferences()
    try:
        provider = create_provider(prefs)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("\nFor Gemini (default): export GEMINI_API_KEY=your-key", err=True)
        sys.exit(1)

    ingester = _make_ingester(provider)

    if not finalize or run_all:
        # Process inbox → review
        click.echo(f"\nScanning {INBOX_DIR} for new files...")
        stats = ingester.process_inbox()
        click.echo(f"  Processed : {stats['processed']} new files → {REVIEW_DIR}/")
        click.echo(f"  Skipped   : {stats['skipped']} (already processed or unsupported)")
        if stats["errors"]:
            click.echo(f"  Errors    : {stats['errors']} (check logs)", err=True)

        if stats["processed"] > 0 and not run_all:
            click.echo(f"\nNext step: review and edit headers in {REVIEW_DIR}/")
            click.echo("Then run:  python3 ingest.py --finalize")
            return

    if finalize or run_all:
        # Finalize review → stories + rebuild index
        click.echo(f"\nFinalizing {REVIEW_DIR}/ → {STORIES_DIR}/...")
        result = ingester.finalize()
        click.echo(f"  Copied    : {result['copied']} files to {STORIES_DIR}/")
        click.echo(f"  Index     : {result['index_entries']} entries → profile/stories_index.md")
        click.echo("\nDone. Stories are ready for use in job matching and generation.")


if __name__ == "__main__":
    main()


def run_ingestion_for_pipeline(provider) -> dict:
    """Called by run.py daily pipeline. Processes inbox and finalizes in one step.

    Input:  provider (LLMProvider)
    Output: { processed, skipped, errors, copied, index_entries }
    """
    ingester = _make_ingester(provider)
    inbox_stats = ingester.process_inbox()
    finalize_stats = ingester.finalize()
    return {**inbox_stats, **finalize_stats}
