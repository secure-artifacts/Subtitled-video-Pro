import ctypes
import json
import os
import platform
import subprocess
from datetime import datetime

from app_config import CONFIG_FILE, load_app_config, save_app_config
from core import get_ffmpeg_cmd


def _creation_flags():
    return 0x08000000 if os.name == "nt" else 0


def _load_config():
    return load_app_config()


def _save_config(config):
    save_app_config(config)


def _run_text(cmd, timeout=8):
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def _memory_gb():
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _detect_gpus_windows():
    if os.name != "nt":
        return []
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
    ]
    code, out, _ = _run_text(cmd, timeout=6)
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []
    gpus = []
    for item in data or []:
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        try:
            ram_gb = round(float(item.get("AdapterRAM") or 0) / (1024 ** 3), 1)
        except Exception:
            ram_gb = 0.0
        gpus.append({"name": name, "memory_gb": ram_gb})
    return gpus


def _detect_gpus():
    gpus = _detect_gpus_windows()
    if gpus:
        return gpus
    if platform.system().lower() == "darwin":
        code, out, _ = _run_text(["system_profiler", "SPDisplaysDataType"], timeout=8)
        if code == 0:
            names = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    names.append(line.split(":", 1)[1].strip())
            return [{"name": n, "memory_gb": 0.0} for n in names]
    return []


def _ffmpeg_encoders():
    code, out, err = _run_text([get_ffmpeg_cmd(), "-hide_banner", "-encoders"], timeout=8)
    text = (out + "\n" + err).lower()
    encoders = []
    for name in ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "libx264"]:
        if name in text:
            encoders.append(name)
    return encoders


def _encoder_smoke_test(encoder):
    if encoder == "libx264":
        return True, ""
    cmd = [
        get_ffmpeg_cmd(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:d=0.15",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-f",
        "null",
        "-",
    ]
    code, _, err = _run_text(cmd, timeout=8)
    return code == 0, err.strip()


def _choose_encoder(gpus, encoders):
    gpu_text = " ".join(g.get("name", "") for g in gpus).lower()
    candidates = []
    if any(k in gpu_text for k in ["nvidia", "geforce", "rtx", "gtx", "quadro"]) and "h264_nvenc" in encoders:
        candidates.append(("h264_nvenc", "NVIDIA NVENC"))
    if any(k in gpu_text for k in ["intel", "arc", "uhd", "iris"]) and "h264_qsv" in encoders:
        candidates.append(("h264_qsv", "Intel Quick Sync"))
    if any(k in gpu_text for k in ["amd", "radeon"]) and "h264_amf" in encoders:
        candidates.append(("h264_amf", "AMD AMF"))
    if platform.system().lower() == "darwin" and "h264_videotoolbox" in encoders:
        candidates.append(("h264_videotoolbox", "Apple VideoToolbox"))

    test_notes = []
    for encoder, label in candidates:
        ok, err = _encoder_smoke_test(encoder)
        if ok:
            return encoder, label, test_notes
        test_notes.append(f"{label} 测试失败: {err[:160]}")
    return "libx264", "CPU x264", test_notes


def detect_hardware_profile(save=True):
    cpu_count = os.cpu_count() or 4
    memory_gb = _memory_gb()
    gpus = _detect_gpus()
    encoders = _ffmpeg_encoders()
    encoder, encoder_label, notes = _choose_encoder(gpus, encoders)
    cpu_threads = max(1, min(16, cpu_count - 1 if cpu_count > 2 else cpu_count))
    profile = {
        "enabled": True,
        "mode": "auto",
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "system": platform.platform(),
        "cpu_count": cpu_count,
        "cpu_threads": cpu_threads,
        "memory_gb": memory_gb,
        "gpus": gpus,
        "ffmpeg_encoders": encoders,
        "encoder": encoder,
        "encoder_label": encoder_label,
        "notes": notes,
    }
    if save:
        save_render_profile(profile)
    return profile


def cpu_safe_profile(save=True):
    cpu_count = os.cpu_count() or 4
    profile = {
        "enabled": True,
        "mode": "cpu",
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "system": platform.platform(),
        "cpu_count": cpu_count,
        "cpu_threads": max(1, min(16, cpu_count - 1 if cpu_count > 2 else cpu_count)),
        "memory_gb": _memory_gb(),
        "gpus": _detect_gpus(),
        "ffmpeg_encoders": _ffmpeg_encoders(),
        "encoder": "libx264",
        "encoder_label": "CPU x264",
        "notes": ["已手动切换到 CPU 安全模式。"],
    }
    if save:
        save_render_profile(profile)
    return profile


def save_render_profile(profile):
    config = _load_config()
    config["render_profile"] = profile or {}
    _save_config(config)
    return profile


def get_render_profile():
    profile = _load_config().get("render_profile", {})
    if not isinstance(profile, dict) or not profile.get("encoder"):
        profile = detect_hardware_profile(save=True)
    return profile


def peek_render_profile():
    profile = _load_config().get("render_profile", {})
    return profile if isinstance(profile, dict) else {}


def describe_render_profile(profile=None):
    profile = profile or get_render_profile()
    gpu_names = ", ".join(g.get("name", "") for g in profile.get("gpus", []) if g.get("name")) or "未检测到独立显卡"
    return (
        f"编码器: {profile.get('encoder_label', profile.get('encoder', 'CPU'))}\n"
        f"CPU: {profile.get('cpu_count', 0)} 核 / 渲染线程 {profile.get('cpu_threads', 0)}\n"
        f"内存: {profile.get('memory_gb', 0)} GB\n"
        f"显卡: {gpu_names}\n"
        f"扫描时间: {profile.get('scanned_at', '未扫描')}"
    )


def build_video_encoder_args(profile=None, quality="deliver"):
    profile = profile or get_render_profile()
    encoder = profile.get("encoder", "libx264")
    fast_quality = str(quality or "").lower() in {"batch_fast", "deliver_fast", "fast", "speed", "极速出片"}
    batch_quality = str(quality or "").lower() == "batch"
    if encoder == "h264_nvenc":
        cq = "31" if fast_quality else ("23" if batch_quality else "24")
        preset = "p1" if fast_quality else "p4"
        return ["-c:v", "h264_nvenc", "-preset", preset, "-cq", cq, "-b:v", "0", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        q = "31" if fast_quality else ("23" if batch_quality else "24")
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", q, "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        qp = "31" if fast_quality else ("22" if batch_quality else "24")
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp, "-pix_fmt", "yuv420p"]
    if encoder == "h264_videotoolbox":
        qv = "70" if fast_quality else "55"
        return ["-c:v", "h264_videotoolbox", "-q:v", qv, "-pix_fmt", "yuv420p"]

    threads = str(max(1, int(profile.get("cpu_threads", os.cpu_count() or 4))))
    crf = "30" if fast_quality else ("22" if batch_quality else "24")
    preset = "ultrafast" if fast_quality else "superfast"
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-threads", threads, "-pix_fmt", "yuv420p"]
