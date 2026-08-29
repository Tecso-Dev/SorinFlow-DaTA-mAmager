"""
The public pages that are not the app: maintenance, 404 and 500.

One shell, three variants, so they read as the same product as the landing page
rather than three different accidents. The palette, the gradient and the glass
panel are lifted from frontend/landing.html — same tokens, same font stack.

Self-contained by rule: no CDN stylesheet, no webfont request, no script from
anywhere. Two of these three pages exist *because* something is already broken,
and a dependency that has to load before the error page renders is a way for the
error page to fail too. The landing page pulls Vazirmatn from Google Fonts; here
it is named first in the stack and falls back to Tahoma, so a visitor who has it
cached gets it and nobody waits on a request that may not complete.
"""
from html import escape
from typing import Optional

# Straight from frontend/landing.html so the family is obvious at a glance.
_SHELL = """<!doctype html><html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex">
<title>__TITLE__</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{
  --bg:#030305; --text:#f2f3f8; --dim:#8f96a8;
  --line:rgba(255,255,255,.08); --glass:rgba(255,255,255,.035);
  --indigo:#6366f1; --violet:#a78bfa; --cyan:#67e8f9; --pink:#f0a6ff; --gold:#fcd34d;
  --grad:linear-gradient(120deg,#a78bfa,#f0a6ff 40%,#67e8f9 80%);
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);
 font-family:'Vazirmatn',Tahoma,system-ui,-apple-system,sans-serif;line-height:1.9;
 display:flex;align-items:center;justify-content:center;padding:1.5rem;overflow:hidden}
::selection{background:rgba(167,139,250,.35)}

/* the cosmos behind the glass — the landing page does this with WebGL; here it
   is three blurred gradients, which costs nothing and cannot fail to load */
.sky{position:fixed;inset:0;z-index:0;overflow:hidden}
.orb{position:absolute;border-radius:50%;filter:blur(90px);opacity:.5;
 animation:float 26s ease-in-out infinite}
.o1{width:46vmax;height:46vmax;left:-14vmax;top:-16vmax;
 background:radial-gradient(circle,var(--violet),transparent 65%)}
.o2{width:38vmax;height:38vmax;right:-12vmax;top:-6vmax;
 background:radial-gradient(circle,var(--cyan),transparent 65%);animation-duration:32s;animation-direction:reverse;opacity:.35}
.o3{width:42vmax;height:42vmax;right:-10vmax;bottom:-18vmax;
 background:radial-gradient(circle,var(--pink),transparent 65%);animation-duration:29s;opacity:.4}
.vignette{position:fixed;inset:0;z-index:0;pointer-events:none;
 background:radial-gradient(ellipse 90% 75% at 50% 45%,transparent 55%,rgba(0,0,0,.78) 100%)}
@keyframes float{
  0%,100%{transform:translate(0,0) scale(1)}
  33%{transform:translate(3vmax,-2vmax) scale(1.06)}
  66%{transform:translate(-2vmax,3vmax) scale(.96)}
}

.card{position:relative;z-index:1;width:100%;max-width:560px;text-align:center;
 background:rgba(10,10,16,.55);border:1px solid var(--line);border-radius:26px;
 backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
 padding:2.6rem 1.9rem 1.9rem;box-shadow:0 30px 90px rgba(0,0,0,.55);
 max-height:95vh;overflow:auto}

.eyebrow{display:inline-flex;align-items:center;gap:10px;font-size:.72rem;
 letter-spacing:.14em;color:var(--dim);margin-bottom:.6rem}
.eyebrow::after{content:'';height:1px;width:70px;
 background:linear-gradient(90deg,var(--violet),transparent)}

h1{margin:.1rem 0 .55rem;font-size:1.75rem;font-weight:800;letter-spacing:-.02em}
h1 .g{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.code{font-size:4.2rem;font-weight:900;line-height:1;letter-spacing:-.04em;margin:.2rem 0 .3rem;
 background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
p{margin:.3rem 0;color:var(--dim);font-size:.93rem}

.art{width:132px;height:132px;margin:0 auto .6rem;display:block}
.spin{transform-origin:center;animation:spin 9s linear infinite}
.spin-r{transform-origin:center;animation:spin 6.5s linear infinite reverse}
.pulse{animation:pulse 2.6s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}

.actions{display:flex;gap:.6rem;justify-content:center;flex-wrap:wrap;margin-top:1.6rem}
.btn{display:inline-flex;align-items:center;gap:8px;padding:.62rem 1.25rem;border-radius:12px;
 font-size:.87rem;text-decoration:none;border:1px solid transparent;cursor:pointer;
 transition:transform .15s,box-shadow .15s}
.btn.light{background:var(--text);color:#0a0a12}
.btn.line{background:transparent;color:var(--text);border-color:var(--line);
 backdrop-filter:blur(8px)}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 26px rgba(167,139,250,.22)}

.count{display:flex;gap:.5rem;justify-content:center;margin:1.4rem 0 .3rem;direction:ltr}
.unit{min-width:70px;border-radius:15px;padding:.6rem .3rem;
 background:var(--glass);border:1px solid var(--line)}
.unit b{display:block;font-size:1.5rem;font-weight:800;line-height:1.2;
 font-variant-numeric:tabular-nums;
 background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.unit span{display:block;font-size:.65rem;color:var(--dim)}
.bar{height:5px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden;margin:1rem 0 .2rem}
.bar>i{display:block;height:100%;width:0;border-radius:99px;background:var(--grad);
 transition:width .6s ease}

.contact{margin-top:1.5rem;padding-top:1.2rem;border-top:1px solid var(--line)}
.contact .lead{font-size:.78rem;color:var(--dim);margin-bottom:.6rem}
.links{display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap}
.links a{display:inline-flex;align-items:center;gap:.4rem;padding:.5rem 1rem;
 border-radius:12px;text-decoration:none;font-size:.84rem;color:var(--text);
 background:var(--glass);border:1px solid var(--line);transition:border-color .15s,transform .15s}
.links a:hover{border-color:var(--violet);transform:translateY(-1px)}
.links a b{direction:ltr;font-weight:600}
.foot{margin-top:1.5rem;font-size:.7rem;color:var(--dim)}
.ref{margin-top:.5rem;font-size:.66rem;color:var(--dim);opacity:.7;direction:ltr}

@media (prefers-reduced-motion: reduce){.orb,.spin,.spin-r,.pulse{animation:none}
 .bar>i{transition:none} .btn:hover{transform:none}}
@media (max-width:420px){.card{padding:2rem 1.2rem 1.5rem}.code{font-size:3.2rem}
 .unit{min-width:60px}.unit b{font-size:1.25rem}}
</style></head><body>
<div class="sky" aria-hidden="true">
  <div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div>
</div>
<div class="vignette" aria-hidden="true"></div>
<div class="card">__BODY__</div>
__SCRIPT__
</body></html>"""


def _shell(title: str, body: str, script: str = "") -> str:
    return (_SHELL.replace("__TITLE__", escape(title))
                  .replace("__BODY__", body)
                  .replace("__SCRIPT__", script))


_FOOT = ('<div class="foot">© <span id="yr">۱۴۰۴</span> '
         'املاک سورین — سورین‌فلو</div>')

# Sets the copyright year in the Jalali calendar from the browser, so it is
# never a year out of date and never needs a deploy to correct.
_YEAR_JS = """<script>
try{document.getElementById('yr').textContent=
 new Intl.DateTimeFormat('fa-IR-u-ca-persian',{year:'numeric'})
   .format(new Date()).replace(/[^۰-۹]/g,'');}catch(e){}
</script>"""


def render_not_found(path: str = "") -> str:
    """404 — a page that does not exist."""
    body = f"""
    <div class="eyebrow">خطای ۴۰۴</div>
    <svg class="art" viewBox="0 0 120 120" fill="none" aria-hidden="true">
      <defs><linearGradient id="g4" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#a78bfa"/><stop offset=".5" stop-color="#f0a6ff"/>
        <stop offset="1" stop-color="#67e8f9"/></linearGradient></defs>
      <circle cx="60" cy="60" r="46" stroke="url(#g4)" stroke-opacity=".18" stroke-width="2"/>
      <g class="spin"><circle cx="60" cy="60" r="34" stroke="url(#g4)" stroke-width="2.5"
        stroke-dasharray="18 12" fill="none"/></g>
      <circle cx="54" cy="54" r="17" stroke="url(#g4)" stroke-width="4" fill="none"/>
      <path d="M67 67l16 16" stroke="url(#g4)" stroke-width="5" stroke-linecap="round"/>
      <g class="pulse" fill="url(#g4)"><circle cx="92" cy="34" r="3"/><circle cx="28" cy="86" r="2.5"/></g>
    </svg>
    <div class="code">۴۰۴</div>
    <h1>این صفحه <span class="g">پیدا نشد</span></h1>
    <p>ممکن است نشانی اشتباه باشد یا صفحه جابه‌جا شده باشد.</p>
    <div class="actions">
      <a class="btn light" href="/">بازگشت به صفحهٔ اصلی</a>
      <a class="btn line" href="/dashboard">ورود به پنل</a>
    </div>
    {_FOOT}"""
    return _shell("سورین‌فلو — صفحه پیدا نشد", body, _YEAR_JS)


def render_server_error(ref: Optional[str] = None) -> str:
    """500 — something broke on our side.

    Carries a reference id when one is available. It is the same id in the
    server log, which turns "the site broke" into a line somebody can actually
    find.
    """
    ref_html = f'<div class="ref">کد پیگیری: {escape(ref)}</div>' if ref else ""
    body = f"""
    <div class="eyebrow">خطای ۵۰۰</div>
    <svg class="art" viewBox="0 0 120 120" fill="none" aria-hidden="true">
      <defs><linearGradient id="g5" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#a78bfa"/><stop offset=".5" stop-color="#f0a6ff"/>
        <stop offset="1" stop-color="#fcd34d"/></linearGradient></defs>
      <circle cx="60" cy="60" r="46" stroke="url(#g5)" stroke-opacity=".18" stroke-width="2"/>
      <g class="spin-r"><circle cx="60" cy="60" r="34" stroke="url(#g5)" stroke-width="2.5"
        stroke-dasharray="10 14" fill="none"/></g>
      <path d="M60 38v30" stroke="url(#g5)" stroke-width="6" stroke-linecap="round"/>
      <circle cx="60" cy="80" r="4.5" fill="url(#g5)"/>
      <g class="pulse" fill="url(#g5)"><circle cx="94" cy="40" r="3"/><circle cx="26" cy="80" r="2.5"/></g>
    </svg>
    <div class="code">۵۰۰</div>
    <h1>مشکلی از <span class="g">سمت ما</span> پیش آمد</h1>
    <p>خطا ثبت شد و در حال بررسی است. لطفاً کمی بعد دوباره تلاش کنید.</p>
    <div class="actions">
      <a class="btn light" href="/">صفحهٔ اصلی</a>
      <button class="btn line" onclick="location.reload()">تلاش دوباره</button>
    </div>
    {ref_html}
    {_FOOT}"""
    return _shell("سورین‌فلو — خطای سرور", body, _YEAR_JS)


def render_maintenance(message: str, config_json: str) -> str:
    """The closed-site page, in the same family as the two above.

    `config_json` is injected as data the script reads, never interpolated into
    markup: the phone number and email are operator-typed, and untrusted like
    any other input.
    """
    body = f"""
    <div class="eyebrow">در حال بروزرسانی</div>
    <svg class="art" viewBox="0 0 120 120" fill="none" aria-hidden="true">
      <defs><linearGradient id="gm" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#a78bfa"/><stop offset=".5" stop-color="#f0a6ff"/>
        <stop offset="1" stop-color="#67e8f9"/></linearGradient></defs>
      <circle cx="60" cy="60" r="46" stroke="url(#gm)" stroke-opacity=".18" stroke-width="2"/>
      <g class="spin" stroke="url(#gm)" stroke-width="5" stroke-linecap="round">
        <circle cx="60" cy="60" r="17" fill="none"/>
        <path d="M60 33v-9M60 96v-9M33 60h-9M96 60h-9M41 41l-6-6M85 85l-6-6M79 41l6-6M35 85l6-6"/>
      </g>
      <g class="pulse" fill="url(#gm)"><circle cx="95" cy="33" r="3"/><circle cx="25" cy="88" r="2.5"/></g>
    </svg>
    <h1>{escape(message)}</h1>
    <p>در حال بهبود سامانه هستیم و به‌زودی برمی‌گردیم.</p>

    <div id="cd" style="display:none">
      <div class="count">
        <div class="unit"><b id="d">۰</b><span>روز</span></div>
        <div class="unit"><b id="h">۰</b><span>ساعت</span></div>
        <div class="unit"><b id="m">۰</b><span>دقیقه</span></div>
        <div class="unit"><b id="s">۰</b><span>ثانیه</span></div>
      </div>
      <div class="bar"><i id="pb"></i></div>
      <p id="eta" style="font-size:.76rem"></p>
    </div>

    <div class="contact" id="contact" style="display:none">
      <div class="lead">در موارد فوری با ما تماس بگیرید</div>
      <div class="links" id="links"></div>
    </div>
    {_FOOT}"""

    script = """<script>
const FA=n=>String(n).padStart(2,'0').replace(/[0-9]/g,d=>'۰۱۲۳۴۵۶۷۸۹'[+d]);
try{document.getElementById('yr').textContent=
 new Intl.DateTimeFormat('fa-IR-u-ca-persian',{year:'numeric'})
   .format(new Date()).replace(/[^۰-۹]/g,'');}catch(e){}

const CFG=__CONFIG__;

if(CFG.phone||CFG.email){
  const box=document.getElementById('links');
  const add=(href,label,value)=>{
    const a=document.createElement('a');a.href=href;
    const b=document.createElement('b');b.textContent=value;   // textContent: cannot inject
    a.append(document.createTextNode(label+' '),b);box.appendChild(a);
  };
  if(CFG.phone)add('tel:'+CFG.phone.replace(/[^\\d+]/g,''),'☎',CFG.phone);
  if(CFG.email)add('mailto:'+encodeURIComponent(CFG.email),'✉',CFG.email);
  document.getElementById('contact').style.display='';
}

if(CFG.seconds_left&&CFG.seconds_left>0){
  document.getElementById('cd').style.display='';
  const total=CFG.seconds_left,end=Date.now()+total*1000;
  const eta=document.getElementById('eta');
  try{eta.textContent='زمان تخمینی بازگشت: '+new Intl.DateTimeFormat(
    'fa-IR-u-ca-persian',{dateStyle:'full',timeStyle:'short'}).format(new Date(end));}catch(e){}
  const tick=()=>{
    const left=Math.max(Math.floor((end-Date.now())/1000),0);
    document.getElementById('d').textContent=FA(Math.floor(left/86400));
    document.getElementById('h').textContent=FA(Math.floor(left%86400/3600));
    document.getElementById('m').textContent=FA(Math.floor(left%3600/60));
    document.getElementById('s').textContent=FA(left%60);
    document.getElementById('pb').style.width=(100-(left/total)*100).toFixed(1)+'%';
    if(left<=0){clearInterval(t);
      // the deadline passing reopens nothing — only a person does
      eta.textContent='در حال نهایی‌سازی — به‌زودی باز می‌شویم';
      setTimeout(()=>location.reload(),60000);}
  };
  tick();const t=setInterval(tick,1000);
}
</script>""".replace("__CONFIG__", config_json)

    return _shell("سورین‌فلو — در حال بروزرسانی", body, script)
