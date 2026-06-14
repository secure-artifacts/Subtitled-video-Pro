import argparse
from pathlib import Path

from ui_refinement_rules import audit_ui_source, summarize_findings


DEFAULT_PATTERNS = ("main.py", "room_*.py", "app_theme.py", "ui_components.py")


def iter_sources(root: Path, patterns: tuple[str, ...] = DEFAULT_PATTERNS):
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            yield path


def audit_project(root: Path) -> dict[str, list]:
    report = {}
    for path in iter_sources(root):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        findings = audit_ui_source(source)
        if findings:
            report[str(path.relative_to(root))] = findings
    return report


def print_report(report: dict[str, list], limit_per_file: int = 12) -> None:
    total = {"high": 0, "medium": 0, "low": 0}
    for file_name, findings in report.items():
        summary = summarize_findings(findings)
        for key, value in summary.items():
            total[key] += value
        print(f"\n{file_name}  high={summary['high']} medium={summary['medium']} low={summary['low']}")
        for finding in findings[:limit_per_file]:
            loc = f":{finding.line}" if finding.line else ""
            print(f"  [{finding.severity}] {finding.rule}{loc} - {finding.message}")
        if len(findings) > limit_per_file:
            print(f"  ... {len(findings) - limit_per_file} more")
    print(f"\nTOTAL high={total['high']} medium={total['medium']} low={total['low']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit UI density and polish issues.")
    parser.add_argument("root", nargs="?", default=".", help="Project root")
    parser.add_argument("--limit", type=int, default=12, help="Max findings printed per file")
    args = parser.parse_args()
    report = audit_project(Path(args.root).resolve())
    print_report(report, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
