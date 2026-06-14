import html


DEFAULT_FONT_FALLBACKS = (
    "TikTok Sans",
    "Noto Sans SC",
    "Noto Sans",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "Arial",
)


def escape_css_single_quoted(value):
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def css_font_stack(family, fallbacks=DEFAULT_FONT_FALLBACKS):
    primary = escape_css_single_quoted(family or "Arial")
    stack = [f"'{primary}'"]
    for fallback in fallbacks:
        if fallback.casefold() != primary.casefold():
            stack.append(f"'{fallback}'")
    stack.append("sans-serif")
    return ", ".join(stack)


def html_text(value):
    return html.escape(str(value or ""), quote=False)


def html_attr(value):
    return html.escape(str(value or ""), quote=True)


def html_multiline_text(value):
    return html_text(value).replace("\n", "<br>")
