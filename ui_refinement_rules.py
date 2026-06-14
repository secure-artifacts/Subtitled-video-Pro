from dataclasses import dataclass
import re


@dataclass(frozen=True)
class UiFinding:
    rule: str
    severity: str
    message: str
    line: int = 0


STYLE_CALL_RE = re.compile(r"setStyleSheet\(")
FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(\d+)px")
RADIUS_RE = re.compile(r"border-radius\s*:\s*(\d+)px")
PADDING_RE = re.compile(r"padding\s*:\s*([^;]+)")
FIXED_SIZE_RE = re.compile(r"setFixed(?:Width|Height|Size)\(")
EMOJI_HINT_RE = re.compile(r"[\\U0001F300-\\U0001FAFF]")


def audit_ui_source(source: str) -> list[UiFinding]:
    findings: list[UiFinding] = []
    lines = source.splitlines()
    style_calls = 0
    fixed_size_calls = 0
    emoji_lines = 0

    for line_no, line in enumerate(lines, start=1):
        if STYLE_CALL_RE.search(line):
            style_calls += 1
        if FIXED_SIZE_RE.search(line):
            fixed_size_calls += 1
        if EMOJI_HINT_RE.search(line):
            emoji_lines += 1
            findings.append(
                UiFinding(
                    "emoji_density",
                    "low",
                    "Emoji-like symbols in dense tool surfaces can make the UI feel less professional.",
                    line_no,
                )
            )
        for match in FONT_SIZE_RE.finditer(line):
            value = int(match.group(1))
            if value > 18:
                findings.append(
                    UiFinding("font_scale", "medium", f"Large font-size {value}px may crowd compact panels.", line_no)
                )
            elif value < 10:
                findings.append(
                    UiFinding("font_scale", "medium", f"Very small font-size {value}px may reduce legibility.", line_no)
                )
        for match in RADIUS_RE.finditer(line):
            value = int(match.group(1))
            if value > 8:
                findings.append(
                    UiFinding("radius", "low", f"border-radius {value}px is above the compact professional target.", line_no)
                )
        for match in PADDING_RE.finditer(line):
            value = match.group(1)
            if "20" in value or "24" in value or "30" in value:
                findings.append(
                    UiFinding("spacing", "low", f"Large padding '{value}' may waste space in production panels.", line_no)
                )

    if style_calls > 80:
        findings.append(
            UiFinding(
                "style_fragmentation",
                "high",
                f"{style_calls} inline setStyleSheet calls found; centralize styles for consistency.",
                0,
            )
        )
    if fixed_size_calls > 80:
        findings.append(
            UiFinding(
                "fixed_size_overuse",
                "medium",
                f"{fixed_size_calls} fixed-size calls found; prefer min/max and responsive layout where possible.",
                0,
            )
        )
    if emoji_lines > 40:
        findings.append(
            UiFinding(
                "emoji_density",
                "medium",
                f"{emoji_lines} emoji-like lines found; prefer consistent icons or text labels in dense tools.",
                0,
            )
        )
    return findings


def summarize_findings(findings: list[UiFinding]) -> dict[str, int]:
    summary = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        if finding.severity in summary:
            summary[finding.severity] += 1
    return summary
