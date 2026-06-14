import hashlib
import json


def stable_overlay_hash(subtitles, signature_html, design_html):
    payload = {
        "subs": subtitles or [],
        "signature": signature_html or "",
        "design": design_html or "",
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def escape_template_literal(value):
    return str(value or "").replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")


def subtitles_json(subtitles):
    return json.dumps(subtitles or [], ensure_ascii=False)


def build_overlay_sync_script(subtitles, signature_html, design_html):
    safe_json = escape_template_literal(subtitles_json(subtitles))
    safe_sig = escape_template_literal(signature_html)
    safe_design = escape_template_literal(design_html)
    return (
        "if(typeof syncDesign === 'function') syncDesign(`"
        + safe_design
        + "`); if(typeof syncSubs === 'function') syncSubs(`"
        + safe_json
        + "`); if(typeof syncSignature === 'function') syncSignature(`"
        + safe_sig
        + "`);"
    )
