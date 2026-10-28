"""Daily email edition via SMTP (default: Gmail with an app password —
free, ~500 messages/day for personal accounts). The email carries the day's
headlines and links to the canonical web edition.

Honesty rule: we record that the SMTP server ACCEPTED the message. SMTP
acceptance is not proof of inbox delivery, and the status page says so."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage

from . import config, timeutil


def compose(ed: dict, lang: str, base_url: str) -> EmailMessage:
    st = config.settings()
    S = ed["stories"]
    n = st["email"].get("max_headlines", 8)
    date = ed["date"]
    url = f"{base_url.rstrip('/')}/{date}/{lang}/index.html" if base_url else ""
    title = st["site"]["title"]
    subj = f"{title} — {timeutil.fmt_date_long(timeutil.parse_iso(date + 'T12:00:00+00:00').date(), lang)}"
    ids = [i for i in ed["international"]["front"] if i in S]
    extra = sorted((s for s in S.values() if s["kind"] == "article" and s["id"] not in ids), key=lambda s: -s["score"])
    ids += [s["id"] for s in extra][: max(0, n - len(ids))]
    lines_txt, lines_html = [], []
    for sid in ids[:n]:
        s = S[sid]
        c = s["content"][lang]
        link = (f"{base_url.rstrip('/')}/{date}/{lang}/s/{sid}.html" if base_url and s["kind"] != "brief"
                else s["evidence"][0]["url"])
        lines_txt.append(f"• {c['headline']}\n  {c.get('subheadline', '')}\n  {link}")
        lines_html.append(f'<li style="margin:0 0 12px"><a href="{html.escape(link)}" style="color:#1B2430;font-weight:600">'
                          f'{html.escape(c["headline"])}</a><br><span style="color:#4A5660">{html.escape(c.get("subheadline", ""))}</span></li>')
    note = ed.get("editor_note", {}).get(lang, "")
    intro = "Le notizie principali di oggi" if lang == "it" else "Today's main stories"
    full = "Leggi l'edizione completa" if lang == "it" else "Read the full edition"
    msg = EmailMessage()
    msg["Subject"] = subj
    msg.set_content(f"{intro}\n\n" + "\n\n".join(lines_txt) + (f"\n\n{full}: {url}" if url else "") + f"\n\n{note}")
    msg.add_alternative(
        f'<div style="font-family:Georgia,serif;max-width:620px;margin:auto;color:#1B2430">'
        f'<h1 style="font-size:28px;margin:0 0 4px">{html.escape(title)}</h1>'
        f'<p style="color:#4A5660;margin:0 0 20px">{html.escape(subj.split("— ", 1)[-1])}</p>'
        f'<ol style="padding-left:18px">{"".join(lines_html)}</ol>'
        + (f'<p><a href="{html.escape(url)}" style="color:#2F5D50">{full}</a></p>' if url else "")
        + f'<p style="color:#66737B;font-size:13px">{html.escape(note)}</p></div>', subtype="html")
    return msg


def send(ed: dict) -> dict:
    st = config.settings()
    if not st["email"].get("enabled"):
        return {"state": "disabled", "detail": "email.enabled is false in settings"}
    user, pwd = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    to = [a.strip() for a in os.environ.get("EMAIL_TO", "").split(",") if a.strip()]
    if not (user and pwd and to):
        return {"state": "not_configured", "detail": "SMTP_USER, SMTP_PASSWORD and EMAIL_TO secrets are required"}
    lang = os.environ.get("EMAIL_LANG", st["site"].get("default_language", "it"))
    base_url = os.environ.get("SITE_URL", st["site"].get("base_url", ""))
    msg = compose(ed, lang, base_url)
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = ", ".join(to)
    try:
        with smtplib.SMTP(st["email"]["smtp_host"], int(st["email"]["smtp_port"]), timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pwd)
            refused = s.send_message(msg)
        if refused:
            return {"state": "partially_refused", "detail": f"refused recipients: {len(refused)}",
                    "at": timeutil.iso(timeutil.now_utc())}
        return {"state": "accepted_by_smtp", "detail": f"accepted by {st['email']['smtp_host']} for {len(to)} recipient(s); inbox delivery cannot be confirmed by SMTP",
                "at": timeutil.iso(timeutil.now_utc())}
    except (smtplib.SMTPException, OSError) as ex:
        return {"state": "failed", "detail": f"{type(ex).__name__}: {str(ex)[:200]}", "at": timeutil.iso(timeutil.now_utc())}
