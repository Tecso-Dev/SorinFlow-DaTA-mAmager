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
  * **No webfont.** Vazirmatn is a Google Fonts request on the site. Mail
    clients block or ignore that, so the stack leads with Vazirmatn for the
    people who have it and falls to Tahoma, which ships on Windows and macOS
    and renders Persian correctly. This is the same stack error_pages.py
    already uses for exactly this reason.
  * **Layout is tables and inline styles.** A `<style>` block survives in Gmail
    web and Apple Mail but not in Gmail's mobile apps, so every rule that
    matters is on the element.

Persian conventions follow the site: `dir="rtl"`, loose line-height (1.9), and
anything Latin or numeric — a code, an email address, a URL — is wrapped back
to `dir="ltr"` so it is not visually reversed.
"""
from datetime import datetime, timezone

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
FONT = "'Vazirmatn',Tahoma,system-ui,-apple-system,sans-serif"

SITE_URL = "https://sorinflow.com"
BRAND = "سورین‌فلو"


def _year() -> int:
    return datetime.now(timezone.utc).year


def shell(*, title: str, preheader: str, body: str,
          accent: str = VIOLET) -> str:
    """The frame every message shares.

    `preheader` is the grey line a client shows next to the subject in the
    inbox list. Left unset it fills itself with whatever HTML comes first,
    which is usually the word "سورین‌فلو" repeated — so it is set explicitly
    and then hidden.
    """
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="fa" dir="rtl">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="color-scheme" content="dark light" />
<meta name="supported-color-schemes" content="dark light" />
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:{BG};color:{TEXT};font-family:{FONT};direction:rtl;">

<!-- inbox preview line, then hidden from the rendered message -->
<div style="display:none;font-size:1px;color:{BG};line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
{preheader}
</div>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background-color:{BG};margin:0;padding:0;">
  <tr>
    <td align="center" style="padding:32px 16px;">

      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
             style="width:600px;max-width:100%;background-color:{CARD};
                    border:1px solid {LINE};border-radius:20px;overflow:hidden;">

        <!-- the gradient rule: solid colour first so Outlook shows violet
             rather than nothing, gradient layered on for everyone else -->
        <tr>
          <td style="height:4px;line-height:4px;font-size:0;
                     background-color:{VIOLET};background-image:{GRADIENT};">&nbsp;</td>
        </tr>

        <tr>
          <td align="center" style="padding:28px 32px 4px 32px;">
            <span style="font-family:{FONT};font-size:19px;font-weight:800;
                         color:{accent};letter-spacing:-0.2px;">{BRAND}</span>
          </td>
        </tr>

        <tr>
          <td style="padding:8px 32px 32px 32px;font-family:{FONT};
                     font-size:15px;line-height:1.9;color:{TEXT};text-align:right;">
{body}
          </td>
        </tr>

        <tr>
          <td style="padding:20px 32px 26px 32px;border-top:1px solid {LINE};
                     font-family:{FONT};font-size:12px;line-height:1.9;
                     color:{DIM};text-align:center;">
            این ایمیل از سوی <a href="{SITE_URL}" style="color:{VIOLET};text-decoration:none;">سورین‌فلو</a> ارسال شده است.<br />
            اگر این درخواست از طرف شما نبوده، این پیام را نادیده بگیرید.
            <div style="margin-top:12px;color:#565c6b;font-size:11px;">
              © {_year()} املاک سورین — سورین‌فلو
            </div>
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>
</body>
</html>"""


def _button(label: str, url: str, colour: str = VIOLET) -> str:
    """A bulletproof-ish CTA.

    A padded table cell rather than a styled <a>: Outlook ignores padding on
    inline elements, which collapses a button into bare underlined text.
    """
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:22px auto 6px auto;">
  <tr>
    <td align="center" bgcolor="{colour}" style="border-radius:12px;">
      <a href="{url}" target="_blank"
         style="display:inline-block;padding:13px 30px;font-family:{FONT};
                font-size:14px;font-weight:700;color:#0a0a12;
                text-decoration:none;border-radius:12px;">{label}</a>
    </td>
  </tr>
</table>"""


def _code_block(code: str) -> str:
    """The one-time code.

    Deliberately Latin digits and `dir="ltr"`: the recipient retypes this into
    a field, and Persian numerals would have to be converted back in their
    head. Letter-spacing is wide enough that 6 and 8 do not blur together.
    """
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:24px auto;">
  <tr>
    <td align="center" bgcolor="#12121a"
        style="border:1px solid {LINE};border-radius:14px;padding:18px 34px;">
      <div dir="ltr" style="font-family:'Courier New',Consolas,monospace;
                            font-size:32px;font-weight:700;color:{TEXT};
                            letter-spacing:10px;line-height:1.2;">{code}</div>
    </td>
  </tr>
</table>"""


def _muted(text: str) -> str:
    return (f'<p style="margin:16px 0 0 0;font-family:{FONT};font-size:13px;'
            f'line-height:1.9;color:{DIM};text-align:right;">{text}</p>')


def _h(text: str) -> str:
    return (f'<h1 style="margin:0 0 14px 0;font-family:{FONT};font-size:21px;'
            f'font-weight:800;color:{TEXT};text-align:right;line-height:1.5;">{text}</h1>')


def _p(text: str) -> str:
    return (f'<p style="margin:0 0 12px 0;font-family:{FONT};font-size:15px;'
            f'line-height:1.9;color:{TEXT};text-align:right;">{text}</p>')


# ── the messages ────────────────────────────────────────────────────────────

def login_code(code: str, *, minutes: int = 5, name: str = "") -> tuple:
    """(subject, html, text) for a sign-in / sign-up code."""
    hello = f"{name} عزیز،" if name else "سلام،"
    body = (
        _h("کد ورود شما")
        + _p(hello)
        + _p("برای ادامهٔ ورود یا ثبت‌نام، کد زیر را در صفحهٔ سورین‌فلو وارد کنید:")
        + _code_block(code)
        + _muted(f"این کد تا {minutes} دقیقهٔ دیگر معتبر است و تنها یک بار قابل استفاده است. "
                 "آن را با هیچ‌کس در میان نگذارید — همکاران سورین‌فلو هرگز این کد را از شما نمی‌پرسند.")
    )
    text = (f"{hello}\n\nکد ورود شما به سورین‌فلو: {code}\n"
            f"این کد تا {minutes} دقیقه معتبر است.\n\n"
            "اگر این درخواست از طرف شما نبوده، این پیام را نادیده بگیرید.")
    return ("کد ورود شما به سورین‌فلو", shell(
        title="کد ورود", preheader=f"کد ورود شما: {code}", body=body), text)


def welcome(name: str, *, portal_url: str = f"{SITE_URL}/portal") -> tuple:
    body = (
        _h(f"{name} عزیز، خوش آمدید 👋")
        + _p("حساب شما در سورین‌فلو ساخته شد.")
        + _p("سورین‌فلو ملک‌هایی را که دنبالشان هستید پیدا می‌کند: کافی است "
             "درخواست خود را ثبت کنید تا مشاوران ما گزینه‌های منطبق را برایتان بفرستند.")
        + _button("ثبت درخواست ملک", portal_url)
        + _muted("اگر سوالی داشتید کافی است به همین ایمیل پاسخ دهید.")
    )
    text = (f"{name} عزیز، خوش آمدید.\n\n"
            "حساب شما در سورین‌فلو ساخته شد.\n"
            f"برای ثبت درخواست ملک: {portal_url}\n")
    return ("به سورین‌فلو خوش آمدید", shell(
        title="خوش آمدید", preheader="حساب شما در سورین‌فلو ساخته شد",
        body=body), text)


def ticket_decision(name: str, approved: bool, note: str = "") -> tuple:
    if approved:
        body = (
            _h("درخواست شما تایید شد ✅")
            + _p(f"{name} عزیز، درخواست دسترسی شما به پنل سورین‌فلو تایید شد.")
            + _p("از این پس می‌توانید با همان ایمیل یا شمارهٔ خود وارد پنل شوید.")
            + (_muted(f"یادداشت مدیر: {note}") if note else "")
            + _button("ورود به پنل", f"{SITE_URL}/dashboard/", SUCCESS)
        )
        subject = "درخواست دسترسی شما تایید شد"
        text = f"{name} عزیز، درخواست دسترسی شما تایید شد.\n{SITE_URL}/dashboard/"
        accent = SUCCESS
    else:
        body = (
            _h("درخواست شما پذیرفته نشد")
            + _p(f"{name} عزیز، درخواست دسترسی شما به پنل در این مرحله پذیرفته نشد.")
            + (_muted(f"دلیل: {note}") if note else "")
            + _muted("می‌توانید بعداً دوباره درخواست دهید یا برای توضیح بیشتر با ما تماس بگیرید.")
        )
        subject = "نتیجهٔ درخواست دسترسی شما"
        text = f"{name} عزیز، درخواست دسترسی شما پذیرفته نشد.\n{note}"
        accent = GOLD
    return (subject, shell(title=subject, preheader=subject, body=body, accent=accent), text)


def request_received(name: str, summary: str) -> tuple:
    body = (
        _h("درخواست شما ثبت شد")
        + _p(f"{name} عزیز، درخواست ملک شما ثبت شد و در حال بررسی است.")
        + f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="margin:18px 0;background-color:#12121a;border:1px solid {LINE};border-radius:14px;">
  <tr><td style="padding:16px 18px;font-family:{FONT};font-size:14px;
                 line-height:1.9;color:{DIM};text-align:right;">{summary}</td></tr>
</table>"""
        + _muted("به‌محض پیدا شدن گزینهٔ مناسب، از همین طریق به شما اطلاع می‌دهیم.")
    )
    text = f"{name} عزیز، درخواست ملک شما ثبت شد.\n\n{summary}"
    return ("درخواست ملک شما ثبت شد", shell(
        title="درخواست ثبت شد", preheader="درخواست ملک شما ثبت شد و در حال بررسی است",
        body=body), text)


def notification(title: str, message: str, *, cta_label: str = "",
                 cta_url: str = "", accent: str = VIOLET) -> tuple:
    """The generic one, for anything without a dedicated template."""
    body = _h(title) + _p(message) + (
        _button(cta_label, cta_url, accent) if cta_label and cta_url else "")
    text = f"{title}\n\n{message}" + (f"\n\n{cta_url}" if cta_url else "")
    return (title, shell(title=title, preheader=message[:120],
                         body=body, accent=accent), text)


def test_message() -> tuple:
    """Proves the whole path: SMTP, templates, Persian, RTL and the palette."""
    body = (
        _h("اتصال ایمیل برقرار است ✅")
        + _p("این یک پیام آزمایشی از پنل سورین‌فلو است.")
        + _p("اگر این ایمیل را می‌بینید، تنظیمات SMTP درست است و سامانه می‌تواند "
             "کد ورود، پیام خوش‌آمد و اطلاع‌رسانی‌ها را ارسال کند.")
        + _code_block("123456")
        + _muted("کد بالا فقط نمونهٔ نمایشی است و کاربردی ندارد.")
    )
    text = ("پیام آزمایشی سورین‌فلو.\n"
            "اگر این را می‌بینید، تنظیمات SMTP درست است.")
    return ("پیام آزمایشی سورین‌فلو", shell(
        title="پیام آزمایشی", preheader="تنظیمات ایمیل درست کار می‌کند",
        body=body), text)


# Everything the panel can send by name, so the UI can list them without
# knowing what each one needs.
CATALOG = {
    "login_code": "کد ورود",
    "welcome": "خوش‌آمدگویی",
    "ticket_decision": "نتیجهٔ درخواست دسترسی",
    "request_received": "تایید ثبت درخواست ملک",
    "notification": "اطلاع‌رسانی عمومی",
}
