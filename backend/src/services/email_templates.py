"""Email templates for Jarvis."""


def magic_link_email(verify_url: str, ttl_minutes: int = 15) -> tuple[str, str]:
    """Return (html, plain_text) for a magic link sign-in email."""
    bg = "#171717"
    card = "#262626"
    btn = "#2563eb"
    body_font = (
        "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
    )
    html = (
        f'<!DOCTYPE html>\n<html>\n<head><meta charset="utf-8"></head>\n'
        f'<body style="margin:0;padding:0;background-color:{bg};'
        f'font-family:{body_font};">\n'
        f'<table width="100%" cellpadding="0" cellspacing="0"'
        f' style="background-color:{bg};padding:40px 0;">\n'
        f'<tr><td align="center">\n'
        f'<table width="480" cellpadding="0" cellspacing="0"'
        f' style="background-color:{card};'
        f'border-radius:12px;padding:40px;">\n'
        f'<tr><td style="text-align:center;padding-bottom:24px;">\n'
        f'<h1 style="color:#fff;font-size:24px;margin:0;">Jarvis</h1>\n'
        f'<p style="color:#a3a3a3;font-size:14px;margin:8px 0 0;">'
        f"Personal AI Operating System</p>\n"
        f"</td></tr>\n"
        f'<tr><td style="padding-bottom:24px;">\n'
        f'<p style="color:#d4d4d4;font-size:16px;'
        f'line-height:1.5;margin:0;">\n'
        f"Click the button below to sign in. "
        f"This link expires in {ttl_minutes} minutes.\n"
        f"</p></td></tr>\n"
        f'<tr><td style="text-align:center;padding-bottom:24px;">\n'
        f'<a href="{verify_url}" style="display:inline-block;'
        f"background-color:{btn};color:#fff;font-size:16px;"
        f"font-weight:600;text-decoration:none;"
        f'padding:12px 32px;border-radius:8px;">\n'
        f"Sign in</a>\n"
        f"</td></tr>\n"
        f"<tr><td>\n"
        f'<p style="color:#737373;font-size:12px;'
        f'line-height:1.5;margin:0;">\n'
        f"If the button doesn&#39;t work, copy and paste this URL "
        f"into your browser:<br>\n"
        f'<a href="{verify_url}" style="color:#60a5fa;'
        f'word-break:break-all;">{verify_url}</a>\n'
        f"</p></td></tr>\n"
        f'<tr><td style="padding-top:24px;'
        f'border-top:1px solid #404040;margin-top:24px;">\n'
        f'<p style="color:#525252;font-size:11px;margin:0;">\n'
        f"If you didn&#39;t request this email, "
        f"you can safely ignore it.\n"
        f"</p></td></tr>\n"
        f"</table></td></tr></table>\n"
        f"</body></html>"
    )

    text = (
        f"Sign in to Jarvis\n\n"
        f"Click the link below to sign in (expires in {ttl_minutes} minutes):\n\n"
        f"{verify_url}\n\n"
        f"If you didn't request this email, you can safely ignore it."
    )

    return html, text
