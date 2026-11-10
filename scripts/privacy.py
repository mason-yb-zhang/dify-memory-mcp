"""Privacy filtering for memory and session writes."""

from __future__ import annotations

import re


PRIVATE_BLOCK_RE = re.compile(
    r"<private\b[^>]*>.*?</private>|<private\b[^>]*>.*$",
    re.IGNORECASE | re.DOTALL,
)

SENSITIVE_PATTERNS = (
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|cookie|authorization)\b\s*[:=]\s*[^\s,;]+"), "credential_assignment"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "bearer_token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "api_token"),
    (re.compile(r"\bdataset-[A-Za-z0-9]{8,}"), "mify_dataset_key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "private_key"),
    (re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]\b"), "cn_national_id"),
)


def sanitize_memory_content(content: str) -> tuple[str | None, list[str]]:
    findings: list[str] = []
    cleaned = PRIVATE_BLOCK_RE.sub(" ", content)

    for pattern, label in SENSITIVE_PATTERNS:
        if label == "credential_assignment":
            def replace_assignment(match: re.Match[str]) -> str:
                findings.append(label)
                return f"{match.group(1)}=[REDACTED]"

            cleaned = pattern.sub(replace_assignment, cleaned)
        else:
            if pattern.search(cleaned):
                findings.append(label)
            cleaned = pattern.sub("[REDACTED]", cleaned)

    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return (cleaned or None), sorted(set(findings))
