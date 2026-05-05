from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_KEYS = {
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
    "GETXAPI_API_KEY",
    "TWITTER_CLIENT_ID",
    "TWITTER_CLIENT_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_BEARER_TOKEN",
    "TWITTER_REFRESH_TOKEN",
    "JINA_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "FIRECRAWL_API_KEY",
    "BROWSER_USE_API_KEY",
    "BROWSERBASE_API_KEY",
    "SCRAPINGBEE_API_KEY",
    "DATAFORSEO_LOGIN",
    "DATAFORSEO_USERNAME",
    "DATAFORSEO_PASSWORD",
    "EXA_API_KEY",
    "SUPADATA_API_KEY",
    "dataforseo_login",
    "dataforseo_username",
    "dataforseo_password",
}
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    ^\s*
    ["']?
    (?P<key>[A-Za-z0-9_]+)
    ["']?
    \s*[:=]\s*
    ["']?
    (?P<value>[^"',\s#}]*)
    """,
)
HIGH_CONFIDENCE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]
PLACEHOLDERS = {
    "",
    '""',
    "''",
    "<your-key>",
    "<password>",
    "<token>",
    "<secret>",
    "your-key",
    "your-token",
    "your-secret",
    "your-password",
}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_allowed_placeholder(value: str) -> bool:
    clean = value.strip().strip('"').strip("'")
    return clean.lower() in PLACEHOLDERS or clean.startswith("$")


def scan_file(path: Path) -> list[str]:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[str] = []
    rel = path.relative_to(ROOT)
    for pattern in HIGH_CONFIDENCE_PATTERNS:
        for match in pattern.finditer(text):
            if "your" in match.group(0).lower():
                continue
            findings.append(f"{rel}: high-confidence secret pattern near byte {match.start()}")

    if path.suffix.lower() != ".py":
        for lineno, line in enumerate(text.splitlines(), 1):
            match = SECRET_ASSIGNMENT.match(line)
            if not match:
                continue
            if match.group("key") not in SECRET_KEYS:
                continue
            value = match.group("value")
            if not is_allowed_placeholder(value):
                findings.append(f"{rel}:{lineno}: non-empty secret-like assignment for {match.group('key')}")

    return findings


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        findings.extend(scan_file(path))

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
