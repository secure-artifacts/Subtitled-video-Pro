from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RenderRange:
    start: float = 0.0
    end: float | None = None

    @property
    def duration(self) -> float:
        if self.end is None:
            return 0.0
        return max(0.0, float(self.end) - max(0.0, float(self.start)))

    def is_open_ended(self) -> bool:
        return self.end is None


@dataclass(frozen=True)
class MediaInput:
    path: str
    kind: str = "video"
    trim: RenderRange = field(default_factory=RenderRange)

    def exists(self) -> bool:
        return Path(self.path).exists()


@dataclass(frozen=True)
class RenderOutput:
    path: str
    format: str = "mp4"

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.lower().lstrip(".")


@dataclass(frozen=True)
class RenderCommandPlan:
    executable: str
    args: tuple[str, ...]
    inputs: tuple[MediaInput, ...] = ()
    output: RenderOutput | None = None
    temp_files: tuple[str, ...] = ()
    label: str = "render"

    def command(self) -> list[str]:
        return [self.executable, *self.args]

    def redacted_command(self) -> str:
        parts = []
        for item in self.command():
            text = str(item)
            if len(text) > 96 and ("/" in text or "\\" in text):
                path = Path(text)
                text = f".../{path.name}"
            parts.append(_shell_quote(text))
        return " ".join(parts)

    def missing_inputs(self) -> list[str]:
        return [item.path for item in self.inputs if item.path and not item.exists()]


@dataclass(frozen=True)
class CanvasLayerRect:
    x: int
    y: int
    width: int
    height: int


def _positive_int(value, default=1) -> int:
    try:
        value = int(float(value))
        return max(1, value)
    except Exception:
        return max(1, int(default or 1))


def canvas_layer_rect(canvas_w, canvas_h, source_w, source_h, scale=1.0, pos_x=0.0, pos_y=0.0, fit="cover") -> CanvasLayerRect:
    canvas_w = _positive_int(canvas_w)
    canvas_h = _positive_int(canvas_h)
    source_w = _positive_int(source_w)
    source_h = _positive_int(source_h)
    try:
        scale = max(0.01, float(scale or 1.0))
    except Exception:
        scale = 1.0
    try:
        pos_x = float(pos_x or 0.0)
    except Exception:
        pos_x = 0.0
    try:
        pos_y = float(pos_y or 0.0)
    except Exception:
        pos_y = 0.0

    fit = str(fit or "cover").lower()
    if fit == "contain":
        fit_scale = min(canvas_w / source_w, canvas_h / source_h)
    elif fit == "original":
        fit_scale = 1.0
    else:
        fit_scale = max(canvas_w / source_w, canvas_h / source_h)
    width = max(1, int(round(source_w * fit_scale * scale)))
    height = max(1, int(round(source_h * fit_scale * scale)))
    x = int(round((canvas_w - width) / 2.0 + (canvas_w * pos_x / 100.0)))
    y = int(round((canvas_h - height) / 2.0 + (canvas_h * pos_y / 100.0)))
    return CanvasLayerRect(x=x, y=y, width=width, height=height)


def ffmpeg_canvas_source(proj_w, proj_h, duration, label="canvas") -> str:
    proj_w = _positive_int(proj_w)
    proj_h = _positive_int(proj_h)
    try:
        duration = max(0.001, float(duration or 0.001))
    except Exception:
        duration = 0.001
    return f"color=c=black:s={proj_w}x{proj_h}:d={duration:.3f},format=rgba[{label}]"


def ffmpeg_layer_scale_filter(scale=1.0, canvas_w=None, canvas_h=None, fit="cover") -> str:
    try:
        scale = max(0.01, float(scale or 1.0))
    except Exception:
        scale = 1.0
    if canvas_w is not None and canvas_h is not None:
        canvas_w = _positive_int(canvas_w)
        canvas_h = _positive_int(canvas_h)
        target_w = max(1, int(round(canvas_w * scale)))
        target_h = max(1, int(round(canvas_h * scale)))
        fit = str(fit or "cover").lower()
        aspect_mode = "decrease" if fit == "contain" else "increase"
        return f"scale={target_w}:{target_h}:force_original_aspect_ratio={aspect_mode}:flags=lanczos"
    if abs(scale - 1.0) < 0.000001:
        return "scale=iw:ih:flags=lanczos"
    return f"scale=iw*{scale:.6f}:ih*{scale:.6f}:flags=lanczos"


def ffmpeg_layer_overlay_xy(pos_x=0.0, pos_y=0.0) -> tuple[str, str]:
    try:
        pos_x = float(pos_x or 0.0)
    except Exception:
        pos_x = 0.0
    try:
        pos_y = float(pos_y or 0.0)
    except Exception:
        pos_y = 0.0
    return (
        f"(W-w)/2+(W*{pos_x:.3f}/100)",
        f"(H-h)/2+(H*{pos_y:.3f}/100)",
    )


def _shell_quote(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() for ch in value) or any(ch in value for ch in ('"', "'", "&", "(", ")")):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def normalize_render_range(start=0.0, end=None, fallback_duration=0.0) -> RenderRange:
    start = max(0.0, float(start or 0.0))
    if end is None:
        end_value = float(fallback_duration or 0.0)
        end = end_value if end_value > start else None
    else:
        end = max(start, float(end or 0.0))
    return RenderRange(start, end)


def ffconcat_escape(path: str) -> str:
    text = str(path).replace("\\", "/")
    return text.replace("'", "'\\''")


def ffconcat_file_entry(path: str, duration=None) -> str:
    lines = [f"file '{ffconcat_escape(path)}'"]
    if duration is not None:
        try:
            duration = max(0.001, float(duration))
        except Exception:
            duration = 0.001
        lines.append(f"duration {duration:.3f}")
    return "\n".join(lines) + "\n"


def ffconcat_inout_entry(path: str, inpoint=0.0, outpoint=None) -> str:
    lines = [f"file '{ffconcat_escape(path)}'"]
    try:
        inpoint = max(0.0, float(inpoint or 0.0))
    except Exception:
        inpoint = 0.0
    lines.append(f"inpoint {inpoint:.3f}")
    if outpoint is not None:
        try:
            outpoint = max(inpoint, float(outpoint))
        except Exception:
            outpoint = inpoint
        lines.append(f"outpoint {outpoint:.3f}")
    return "\n".join(lines) + "\n"


def build_ffconcat(paths: Iterable[str]) -> str:
    lines = ["ffconcat version 1.0"]
    for path in paths:
        if not path:
            continue
        lines.append(ffconcat_file_entry(path).rstrip("\n"))
    return "\n".join(lines) + "\n"


def build_dry_run_plan(executable: str, inputs: Iterable[MediaInput], output: RenderOutput, extra_args=None) -> RenderCommandPlan:
    inputs = tuple(inputs or ())
    args: list[str] = ["-hide_banner", "-y"]
    for item in inputs:
        if item.trim.start > 0:
            args.extend(["-ss", f"{item.trim.start:.3f}"])
        args.extend(["-i", item.path])
        if item.trim.end is not None and item.trim.duration > 0:
            args.extend(["-t", f"{item.trim.duration:.3f}"])
    args.extend(list(extra_args or []))
    args.append(output.path)
    return RenderCommandPlan(executable=executable, args=tuple(args), inputs=inputs, output=output, label="dry-run")


def summarize_plan(plan: RenderCommandPlan) -> dict:
    return {
        "label": plan.label,
        "command": plan.command(),
        "redacted": plan.redacted_command(),
        "inputs": [item.path for item in plan.inputs],
        "missing_inputs": plan.missing_inputs(),
        "output": plan.output.path if plan.output else "",
        "temp_files": list(plan.temp_files),
    }
