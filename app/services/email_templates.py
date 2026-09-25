"""
قالب‌های ایمیل — the site's visual identity, rebuilt for email clients.

The palette, the gradient and the wordmark are the landing page's, copied from
app/error_pages.py so the two cannot drift: same `#030305` ground, same
`linear-gradient(120deg,#a78bfa,#f0a6ff 40%,#67e8f9 80%)`, same
`'Vazirmatn',Tahoma,…` stack.

Email is not a browser, and three constraints shape everything here:

  * **Outlook renders neither inline SVG nor `background-clip:text`.** The
    brand mark on the site is a gradient-stroked SVG infinity; in email it is a
    text wordmark over a gradient bar that carries a solid `background-color`
    underneath, so Outlook shows flat violet instead of nothing.
  * **No webfont.** Estedad is self-hosted on the site. Mail
    clients block or ignore that, so the stack leads with Vazirmatn for the
    people who have it and falls to Tahoma, which ships on Windows and macOS
    and renders Persian correctly. This is the same stack error_pages.py
    already uses for exactly this reason.
  * **Layout is tables and inline styles.** A `<style>` block survives in Gmail
    web and Apple Mail but not in Gmail's mobile apps, so every rule that
    matters is on the element.

  * **Fluid, not fixed.** The card is `width="100%"` capped by `max-width:600px`,
    with a 600px ghost table only Outlook sees. It used to be `width="600"`,
    and on Android that broke: Gmail's app on a non-Google account ignores
    max-width and every `<style>`, so the attribute won — a 600px card on a
    360px screen, which the app either scrolled sideways or shrank until the
    text was unreadable (Samsung Email's «fit to screen» does the same).
  * **Direction lives on the content, not on `<body>`.** Gmail drops the
    attributes of `<html>` and `<body>`, so `dir="rtl"` there alone let Android
    lay Persian sentences out left-to-right: a full stop at the wrong end,
    numbers hopping across words. Every table and text block carries its own.

A small `<style>` block still ships, for the clients that keep one (Gmail
with a Google account, Apple Mail, Samsung Email): on a narrow screen it
trims the padding, shrinks the heading and the code, and makes the button
full width. Nothing depends on it — the inline styles are already correct.

Persian conventions follow the site: `dir="rtl"`, loose line-height (1.9), and
anything Latin or numeric — a code, an email address, a URL — is wrapped back
to `dir="ltr"` so it is not visually reversed.

Everything a caller hands in is plain text and is escaped here: a visitor
chooses their own name at sign-up, and it lands in these messages.

## Brand, domain and hero images — never hard-coded

CLAUDE.md (multi-office readiness): a template never spells out the brand,
office name or domain itself. Every public function here takes an optional
`site: dict | None` — the same shape `app.services.site_settings.read_site()`
returns (`brandName`, `domain`, `agencyName`, `email`, `phone`, ...). A
caller that has a `db` session fetches it (`await read_site(db)`) and passes
it through; a caller that does not (or a direct test) gets the same
env/default fallback `read_site()` itself falls back to
(`site_settings.defaults()`), so every function stays callable exactly as
before — no required new argument, no changed positional order.

Each template family shows one small isometric PNG hero, rendered ahead of
time by `scripts/render_email_heroes.py` into `app/static/email_assets/` and
served by the backend at `/email-assets/<name>.png` (mounted in app/main.py,
public in api_key_middleware — see tests/test_security_headers.py). The URL
is built absolute from the configured domain (`https://{domain}/email-assets/…`)
rather than embedded inline: Gmail and most other clients strip `data:` URIs
out of HTML mail entirely (a security measure against tracking/phishing
payloads), so an inline image silently disappears in the one client that
matters most. A hosted PNG degrades the same way every marketing email
already does — blocked-by-default remote images show a placeholder the
recipient can choose to load — instead of vanishing outright.
"""
import html as _html
from datetime import datetime, timezone
from typing import Optional

from app.services.site_settings import defaults as _site_defaults

BG = "#030305"
CARD = "#0a0a10"
LINE = "#1c1c22"
TEXT = "#f2f3f8"
DIM = "#8f96a8"
VIOLET = "#a78bfa"
PINK = "#f0a6ff"
CYAN = "#67e8f9"
GOLD = "#fcd34d"
DANGER = "#ef4444"
SUCCESS = "#10b981"

GRADIENT = f"linear-gradient(120deg,{VIOLET},{PINK} 40%,{CYAN} 80%)"
# Estedad first for the few clients that have it installed locally; no
# webfont is requested here and none would load — see the module note.
# Tahoma is the one that actually renders Persian on Windows and Outlook.
FONT = "'Estedad','Vazirmatn',Tahoma,system-ui,-apple-system,sans-serif"


def _site(site: Optional[dict]) -> dict:
    """The effective site config for a template: caller's dict, or the same
    env/default fallback app.services.site_settings.read_site() uses when
    nothing has been saved yet."""
    out = _site_defaults()
    if site:
        out.update({k: v for k, v in site.items() if v})
    return out


def _brand(site: Optional[dict]) -> str:
    return _site(site)["brandName"]


def _site_url(site: Optional[dict]) -> str:
    return f"https://{_site(site)['domain']}"


def _year() -> int:
    return datetime.now(timezone.utc).year


def _esc(text) -> str:
    """Plain text into HTML: escaped, and its line breaks kept."""
    return _html.escape(str(text or ""), quote=True).replace("\r\n", "\n").replace("\n", "<br />")


def _safe_url(url: str) -> str:
    """Only a web link may become a button — never javascript: or data:."""
    u = str(url or "").strip()
    return _html.escape(u, quote=True) if u.lower().startswith(("https://", "http://")) else ""


# For the clients that keep a <style>. Never load-bearing: every rule here
# only improves what the inline styles already get right.
_RESPONSIVE_CSS = """
  body, table, td, a { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
  table, td { mso-table-lspace:0pt; mso-table-rspace:0pt; }
  a[x-apple-data-detectors] { color:inherit !important; text-decoration:none !important; }
  @media only screen and (max-width:620px) {
    .sf-outer { padding:14px 8px !important; }
    .sf-card { border-radius:14px !important; }
    .sf-pad { padding-left:18px !important; padding-right:18px !important; }
    .sf-h1 { font-size:18px !important; }
    .sf-text { font-size:14px !important; }
    .sf-codebox { padding:14px 16px !important; }
    .sf-code { font-size:26px !important; letter-spacing:6px !important; }
    .sf-btn, .sf-btn td { width:100% !important; }
    .sf-btn a { display:block !important; }
  }
"""


# Isometric hero PNGs, one per template family — rendered by
# scripts/render_email_heroes.py, served from app/static/email_assets/
# (mounted at /email-assets/, see app/main.py) and referenced by an absolute
# URL built from the site's own domain. 1200×420 source (2x for a 600×210
# display) so it stays sharp on a retina screen.
HERO_FILES = {
    "auth": ("hero-auth.png", "یک قفل و کلید ایزومتریک"),
    "welcome": ("hero-welcome.png", "دری باز و یک دست ایزومتریک در حال خوش‌آمدگویی"),
    "decision": ("hero-decision.png", "یک برگهٔ تاییدشدهٔ ایزومتریک"),
    "request": ("hero-request.png", "یک پوشهٔ درخواست و ذره‌بین ایزومتریک"),
    "notification": ("hero-notification.png", "یک زنگولهٔ اطلاع‌رسانی ایزومتریک"),
}


def _hero_img(hero: Optional[str], site: Optional[dict]) -> str:
    """A fluid hero banner, `width="600"` only for Outlook.

    Same reasoning as the card itself (see the module note on Android/Gmail):
    a plain `width="600"` attribute would win over every `<style>` and every
    `max-width` on Gmail's Android app and shrink or side-scroll a 600px
    image on a 360px screen, so the pixel width is confined to the `mso`
    branch Outlook alone reads, and everyone else gets a fluid `<img>`.
    """
    if not hero or hero not in HERO_FILES:
        return ""
    filename, alt = HERO_FILES[hero]
    src = f"{_site_url(site)}/email-assets/{filename}"
    alt_esc = _html.escape(alt)
    return f"""
<tr>
  <td style="line-height:0;font-size:0;">
    <!--[if mso]>
    <img src="{src}" width="600" height="210" alt="{alt_esc}" style="display:block;border:0;" />
    <![endif]-->
    <!--[if !mso]><!-->
    <img src="{src}" alt="{alt_esc}"
         style="display:block;width:100%;max-width:600px;height:auto;border:0;outline:none;" />
    <!--<![endif]-->
  </td>
</tr>"""


def shell(*, title: str, preheader: str, body: str,
          accent: str = VIOLET, site: Optional[dict] = None,
          hero: Optional[str] = None) -> str:
    """The frame every message shares.

    `preheader` is the grey line a client shows next to the subject in the
    inbox list. Left unset it fills itself with whatever HTML comes first,
    which is usually the brand name repeated — so it is set explicitly and
    then hidden, with a run of zero-width fillers after it so the body does
    not leak into the preview behind it.

    `site` is the office's brand/domain/contact config (see the module
    docstring); `hero` names one of HERO_FILES, shown as a banner image
    under the gradient rule.
    """
    cfg = _site(site)
    brand = _html.escape(cfg["brandName"])
    agency = _html.escape(cfg["agencyName"])
    site_url = _site_url(site)
    title = _html.escape(str(title or ""))
    preheader = _html.escape(str(preheader or ""))
    filler = "&zwnj;&nbsp;" * 40
    copyright_line = f"{agency} — {brand}" if agency else brand
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="fa" dir="rtl">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="x-apple-disable-message-reformatting" />
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no" />
<meta name="color-scheme" content="dark light" />
<meta name="supported-color-schemes" content="dark light" />
<title>{title}</title>
<style type="text/css">{_RESPONSIVE_CSS}</style>
</head>
<body style="margin:0;padding:0;width:100%;background-color:{BG};color:{TEXT};font-family:{FONT};direction:rtl;">
<!-- Gmail strips <body>'s attributes: the direction and the ground are
     repeated on a wrapper it keeps. -->
<div dir="rtl" lang="fa" style="direction:rtl;background-color:{BG};margin:0;padding:0;width:100%;">

<!-- inbox preview line, then hidden from the rendered message -->
<div style="display:none;font-size:1px;color:{BG};line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">
{preheader}{filler}
</div>

<table role="presentation" dir="rtl" cellpadding="0" cellspacing="0" border="0" width="100%"
       bgcolor="{BG}" style="width:100%;background-color:{BG};margin:0;padding:0;">
  <tr>
    <td align="center" class="sf-outer" style="padding:28px 12px;">

      <!--[if mso]><table role="presentation" align="center" cellpadding="0" cellspacing="0" border="0" width="600"><tr><td><![endif]-->
      <table role="presentation" dir="rtl" class="sf-card" cellpadding="0" cellspacing="0" border="0" width="100%"
             bgcolor="{CARD}"
             style="width:100%;max-width:600px;margin:0 auto;background-color:{CARD};
                    border:1px solid {LINE};border-radius:18px;overflow:hidden;">

        <!-- the gradient rule: solid colour first so Outlook shows violet
             rather than nothing, gradient layered on for everyone else -->
        <tr>
          <td style="height:4px;line-height:4px;font-size:0;
                     background-color:{VIOLET};background-image:{GRADIENT};">&nbsp;</td>
        </tr>
{_hero_img(hero, site)}
        <tr>
          <td align="center" class="sf-pad" dir="rtl" style="padding:26px 28px 2px 28px;">
            <span style="font-family:{FONT};font-size:19px;font-weight:800;
                         color:{accent};letter-spacing:-0.2px;">{brand}</span>
            {f'<div style="font-family:{FONT};font-size:11px;color:{DIM};margin-top:2px;">{agency}</div>' if agency else ""}
          </td>
        </tr>

        <tr>
          <td class="sf-pad sf-text" dir="rtl"
              style="padding:14px 28px 30px 28px;font-family:{FONT};
                     font-size:15px;line-height:1.9;color:{TEXT};text-align:right;
                     direction:rtl;word-break:break-word;overflow-wrap:anywhere;">
{body}
          </td>
        </tr>

        <tr>
          <td class="sf-pad" dir="rtl"
              style="padding:18px 28px 24px 28px;border-top:1px solid {LINE};
                     font-family:{FONT};font-size:12px;line-height:1.9;
                     color:{DIM};text-align:center;direction:rtl;">
            این ایمیل از سوی <a href="{site_url}" style="color:{VIOLET};text-decoration:none;">{brand}</a> ارسال شده است.<br />
            اگر این درخواست از طرف شما نبوده، این پیام را نادیده بگیرید.
            <div style="margin-top:10px;">
              <a href="{site_url}/portal" style="color:{DIM};text-decoration:none;">پورتال مشتریان</a>
              <span style="color:#3a3f4b;">&nbsp;·&nbsp;</span>
              <a href="{site_url}/dashboard/" style="color:{DIM};text-decoration:none;">پنل</a>
            </div>
            <div style="margin-top:10px;color:#565c6b;font-size:11px;">
              © {_year()} {copyright_line}
            </div>
          </td>
        </tr>

      </table>
      <!--[if mso]></td></tr></table><![endif]-->

    </td>
  </tr>
</table>
</div>
</body>
</html>"""


def _button(label: str, url: str, colour: str = VIOLET) -> str:
    """A bulletproof-ish CTA.

    A padded table cell rather than a styled <a>: Outlook ignores padding on
    inline elements, which collapses a button into bare underlined text. On a
    narrow screen the stylesheet makes it full width, a thumb's target.
    """
    href = _safe_url(url)
    if not href:
        return ""
    return f"""
<table role="presentation" class="sf-btn" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:22px auto 6px auto;">
  <tr>
    <td align="center" bgcolor="{colour}" style="border-radius:12px;background-color:{colour};">
      <a href="{href}" target="_blank"
         style="display:inline-block;padding:13px 30px;font-family:{FONT};
                font-size:14px;font-weight:700;color:#0a0a12;text-align:center;
                text-decoration:none;border-radius:12px;">{_esc(label)}</a>
    </td>
  </tr>
</table>"""


def _code_block(code: str) -> str:
    """The one-time code.

    Deliberately Latin digits and `dir="ltr"`: the recipient retypes this into
    a field, and Persian numerals would have to be converted back in their
    head. Letter-spacing is wide enough that 6 and 8 do not blur together —
    and small enough that eight digits still fit a 320px screen without the
    stylesheet's help.
    """
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:22px auto;">
  <tr>
    <td align="center" bgcolor="#12121a" class="sf-codebox"
        style="background-color:#12121a;border:1px solid {LINE};border-radius:14px;padding:16px 26px;">
      <div dir="ltr" class="sf-code" style="font-family:'Courier New',Consolas,monospace;font-size:30px;
           font-weight:700;color:{TEXT};direction:ltr;letter-spacing:8px;line-height:1.2;white-space:nowrap;">{_esc(code)}</div>
    </td>
  </tr>
</table>"""


def _muted(text: str) -> str:
    return (f'<p dir="rtl" style="margin:16px 0 0 0;font-family:{FONT};font-size:13px;'
            f'line-height:1.9;color:{DIM};text-align:right;direction:rtl;">{text}</p>')


def _h(text: str) -> str:
    return (f'<h1 dir="rtl" class="sf-h1" style="margin:0 0 14px 0;font-family:{FONT};font-size:21px;'
            f'font-weight:800;color:{TEXT};text-align:right;direction:rtl;line-height:1.5;">{text}</h1>')


def _p(text: str) -> str:
    return (f'<p dir="rtl" class="sf-text" style="margin:0 0 12px 0;font-family:{FONT};font-size:15px;'
            f'line-height:1.9;color:{TEXT};text-align:right;direction:rtl;">{text}</p>')


# ── the messages ────────────────────────────────────────────────────────────

def login_code(code: str, *, minutes: int = 5, name: str = "",
              site: Optional[dict] = None) -> tuple:
    """(subject, html, text) for a sign-in / sign-up code."""
    brand = _brand(site)
    hello = f"{_esc(name)} عزیز،" if name else "سلام،"
    body = (
        _h("کد ورود شما")
        + _p(hello)
        + _p(f"برای ادامهٔ ورود یا ثبت‌نام، کد زیر را در صفحهٔ {brand} وارد کنید:")
        + _code_block(code)
        + _muted(f"این کد تا {minutes} دقیقهٔ دیگر معتبر است و تنها یک بار قابل استفاده است. "
                 f"آن را با هیچ‌کس در میان نگذارید — همکاران {brand} هرگز این کد را از شما نمی‌پرسند.")
    )
    text = (f"{name + ' عزیز،' if name else 'سلام،'}\n\nکد ورود شما به {brand}: {code}\n"
            f"این کد تا {minutes} دقیقه معتبر است.\n\n"
            "اگر این درخواست از طرف شما نبوده، این پیام را نادیده بگیرید.")
    return (f"کد ورود شما به {brand}", shell(
        title="کد ورود", preheader=f"کد ورود شما: {code}", body=body,
        site=site, hero="auth"), text)


def verify_email_code(code: str, *, minutes: int = 5, name: str = "",
                      site: Optional[dict] = None) -> tuple:
    """(subject, html, text) for proving an address from the profile page."""
    brand = _brand(site)
    hello = f"{_esc(name)} عزیز،" if name else "سلام،"
    body = (
        _h("تأیید ایمیل شما")
        + _p(hello)
        + _p(f"برای تأیید این ایمیل در {brand}، کد زیر را در صفحهٔ پروفایل وارد کنید:")
        + _code_block(code)
        + _muted(f"این کد تا {minutes} دقیقهٔ دیگر معتبر است و تنها یک بار قابل استفاده است. "
                 "اگر شما این درخواست را نداده‌اید، این پیام را نادیده بگیرید.")
    )
    text = (f"{name + ' عزیز،' if name else 'سلام،'}\n\nکد تأیید ایمیل شما در {brand}: {code}\n"
            f"این کد تا {minutes} دقیقه معتبر است.\n\n"
            "اگر این درخواست از طرف شما نبوده، این پیام را نادیده بگیرید.")
    return (f"تأیید ایمیل شما در {brand}", shell(
        title="تأیید ایمیل", preheader=f"کد تأیید ایمیل: {code}", body=body,
        site=site, hero="auth"), text)


def welcome(name: str, *, portal_url: str = "", site: Optional[dict] = None) -> tuple:
    brand = _brand(site)
    portal_url = portal_url or f"{_site_url(site)}/portal"
    body = (
        _h(f"{_esc(name)} عزیز، خوش آمدید 👋")
        + _p(f"حساب شما در {brand} ساخته شد.")
        + _p(f"{brand} ملک‌هایی را که دنبالشان هستید پیدا می‌کند: کافی است "
             "درخواست خود را ثبت کنید تا مشاوران ما گزینه‌های منطبق را برایتان بفرستند.")
        + _button("ثبت درخواست ملک", portal_url)
        + _muted("اگر سوالی داشتید کافی است به همین ایمیل پاسخ دهید.")
    )
    text = (f"{name} عزیز، خوش آمدید.\n\n"
            f"حساب شما در {brand} ساخته شد.\n"
            f"برای ثبت درخواست ملک: {portal_url}\n")
    return (f"به {brand} خوش آمدید", shell(
        title="خوش آمدید", preheader=f"حساب شما در {brand} ساخته شد",
        body=body, site=site, hero="welcome"), text)


def ticket_decision(name: str, approved: bool, note: str = "",
                    site: Optional[dict] = None) -> tuple:
    brand = _brand(site)
    dashboard_url = f"{_site_url(site)}/dashboard/"
    if approved:
        body = (
            _h("درخواست شما تایید شد ✅")
            + _p(f"{_esc(name)} عزیز، درخواست دسترسی شما به پنل {brand} تایید شد.")
            + _p("از این پس می‌توانید با همان ایمیل یا شمارهٔ خود وارد پنل شوید.")
            + (_muted(f"یادداشت مدیر: {_esc(note)}") if note else "")
            + _button("ورود به پنل", dashboard_url, SUCCESS)
        )
        subject = "درخواست دسترسی شما تایید شد"
        text = f"{name} عزیز، درخواست دسترسی شما تایید شد.\n{dashboard_url}"
        accent = SUCCESS
    else:
        body = (
            _h("درخواست شما پذیرفته نشد")
            + _p(f"{_esc(name)} عزیز، درخواست دسترسی شما به پنل در این مرحله پذیرفته نشد.")
            + (_muted(f"دلیل: {_esc(note)}") if note else "")
            + _muted("می‌توانید بعداً دوباره درخواست دهید یا برای توضیح بیشتر با ما تماس بگیرید.")
        )
        subject = "نتیجهٔ درخواست دسترسی شما"
        text = f"{name} عزیز، درخواست دسترسی شما پذیرفته نشد.\n{note}"
        accent = GOLD
    return (subject, shell(title=subject, preheader=subject, body=body, accent=accent,
                           site=site, hero="decision"), text)


def request_received(name: str, summary: str, site: Optional[dict] = None) -> tuple:
    body = (
        _h("درخواست شما ثبت شد")
        + _p(f"{_esc(name)} عزیز، درخواست ملک شما ثبت شد و در حال بررسی است.")
        + f"""
<table role="presentation" dir="rtl" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="width:100%;margin:18px 0;background-color:#12121a;border:1px solid {LINE};border-radius:14px;">
  <tr><td dir="rtl" style="padding:14px 16px;font-family:{FONT};font-size:14px;direction:rtl;
                 line-height:1.9;color:{DIM};text-align:right;">{_esc(summary)}</td></tr>
</table>"""
        + _muted("به‌محض پیدا شدن گزینهٔ مناسب، از همین طریق به شما اطلاع می‌دهیم.")
    )
    text = f"{name} عزیز، درخواست ملک شما ثبت شد.\n\n{summary}"
    return ("درخواست ملک شما ثبت شد", shell(
        title="درخواست ثبت شد", preheader="درخواست ملک شما ثبت شد و در حال بررسی است",
        body=body, site=site, hero="request"), text)


def notification(title: str, message: str, *, cta_label: str = "",
                 cta_url: str = "", accent: str = VIOLET,
                 site: Optional[dict] = None) -> tuple:
    """The generic one, for anything without a dedicated template.

    `message` is plain text, and its line breaks are kept: the identity-check
    and forwarder alerts are numbered steps, and they used to arrive run
    together into one paragraph.
    """
    body = _h(_esc(title)) + _p(_esc(message)) + (
        _button(cta_label, cta_url, accent) if cta_label and cta_url else "")
    text = f"{title}\n\n{message}" + (f"\n\n{cta_url}" if cta_url else "")
    return (title, shell(title=title, preheader=message[:120],
                         body=body, accent=accent, site=site,
                         hero="notification"), text)


def test_message(site: Optional[dict] = None) -> tuple:
    """Proves the whole path: SMTP, templates, Persian, RTL and the palette."""
    brand = _brand(site)
    body = (
        _h("اتصال ایمیل برقرار است ✅")
        + _p(f"این یک پیام آزمایشی از پنل {brand} است.")
        + _p("اگر این ایمیل را می‌بینید، تنظیمات SMTP درست است و سامانه می‌تواند "
             "کد ورود، پیام خوش‌آمد و اطلاع‌رسانی‌ها را ارسال کند.")
        + _code_block("123456")
        + _muted("کد بالا فقط نمونهٔ نمایشی است و کاربردی ندارد.")
    )
    text = (f"پیام آزمایشی {brand}.\n"
            "اگر این را می‌بینید، تنظیمات SMTP درست است.")
    return (f"پیام آزمایشی {brand}", shell(
        title="پیام آزمایشی", preheader="تنظیمات ایمیل درست کار می‌کند",
        body=body, site=site, hero="notification"), text)


# Everything the panel can send by name, so the UI can list them without
# knowing what each one needs.
CATALOG = {
    "login_code": "کد ورود",
    "welcome": "خوش‌آمدگویی",
    "ticket_decision": "نتیجهٔ درخواست دسترسی",
    "request_received": "تایید ثبت درخواست ملک",
    "notification": "اطلاع‌رسانی عمومی",
}
