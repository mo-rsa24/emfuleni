#!/usr/bin/env python3
"""Standalone smoke test for the VLM meter-reading prompt.

Validates three things end-to-end without involving Django, the database,
or RQ:

    1. ANTHROPIC_API_KEY is present and authenticates.
    2. The Sonnet 4.6 vision model can be reached.
    3. The prompt actually extracts a sensible reading from a real photo.

Usage:
    export ANTHROPIC_API_KEY='sk-ant-...'   # or put it in .env
    python scripts/test_vlm.py portal/fixtures/Water-meter.jpg

    # multiple files at once
    python scripts/test_vlm.py portal/fixtures/Water-meter.jpg portal/fixtures/images.jpg

The .env file at the project root is auto-loaded if present, mirroring
the loader in primeserve/settings.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Same model + prompt as vlm/services.py — keep them in sync.
VLM_MODEL = "claude-sonnet-4-6"
VLM_PROMPT = (
    "You are reading the dial of a residential water meter from a photograph. "
    "Return your answer as JSON with three fields:\n"
    "- reading_kl: the dial reading as a single integer in kilolitres (kl). "
    "If the dial shows a decimal portion, round down to the integer kl.\n"
    "- confidence: a float in [0, 1] representing how confident you are. "
    "Lower it if the image is blurry, partially obscured, lit poorly, or "
    "shows something that is not clearly a water meter.\n"
    "- notes: short notes — empty string if nothing to flag.\n"
    "If the image is not a water meter, set confidence to 0 and explain in notes."
)

ACCEPTED_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _load_dotenv(path: Path) -> None:
    """Same minimal loader as settings.py — keeps the script standalone."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _guess_media_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    media_type = mapping.get(suffix, "image/jpeg")
    if media_type not in ACCEPTED_MIMES:
        media_type = "image/jpeg"
    return media_type


def extract_one(client, image_path: Path) -> dict:
    """Hit the Anthropic Messages API with one image. Returns a dict.

    Shape:
        {"ok": True,  "parsed": {...}, "raw_text": "...", "usage": {...}}
        {"ok": False, "error": "..."}
    """
    if not image_path.is_file():
        return {"ok": False, "error": f"file not found: {image_path}"}

    media_type = _guess_media_type(image_path)
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

    try:
        response = client.messages.create(
            model=VLM_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": VLM_PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 — surface anything to the user
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    raw_text = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            raw_text = getattr(block, "text", "") or ""
            break

    # Sonnet sometimes wraps JSON in ```json ... ``` fences even when asked
    # for raw JSON. Strip them before parsing.
    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"model returned non-JSON: {exc}",
            "raw_text": raw_text,
        }

    usage = getattr(response, "usage", None)
    return {
        "ok": True,
        "parsed": parsed,
        "raw_text": raw_text,
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
        if usage
        else {},
    }


def _print_result(image_path: Path, result: dict) -> None:
    bar = "─" * 64
    print(bar)
    print(f"image:  {image_path}")
    if not result["ok"]:
        print(f"ERROR:  {result['error']}")
        if "raw_text" in result:
            print(f"raw:    {result['raw_text'][:200]}")
        return
    parsed = result["parsed"]
    print(f"reading: {parsed.get('reading_kl')} kl")
    print(f"confidence: {parsed.get('confidence')}")
    notes = parsed.get("notes") or "(none)"
    print(f"notes:   {notes}")
    usage = result.get("usage") or {}
    if usage:
        print(
            f"tokens: input={usage.get('input_tokens')} output={usage.get('output_tokens')}"
        )


def main(argv: list[str]) -> int:
    _load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "images",
        nargs="*",
        default=["portal/fixtures/Water-meter.jpg"],
        help="One or more image paths. Defaults to the committed sample.",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "  Copy .env.example to .env and fill in your key, OR\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...' in your shell.",
            file=sys.stderr,
        )
        return 2

    try:
        import anthropic
    except ImportError:
        print(
            "ERROR: anthropic package not installed in this env.\n"
            "  Run: pip install anthropic",
            file=sys.stderr,
        )
        return 2

    client = anthropic.Anthropic()

    any_failure = False
    for path_str in args.images:
        path = Path(path_str)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        result = extract_one(client, path)
        _print_result(path, result)
        if not result["ok"]:
            any_failure = True

    print("─" * 64)
    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
