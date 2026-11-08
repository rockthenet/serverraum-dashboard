#!/usr/bin/env python3
import json, os, time
try: import urllib.request as req
except: import urllib2 as req

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "speed.json")

def test_download():
    try:
        start = time.monotonic()
        data = req.urlopen("http://httpbin.org/bytes/500000", timeout=20).read()
        elapsed = time.monotonic() - start
        if elapsed > 0 and len(data) > 100000:
            return round((len(data) * 8 / 1_000_000) / elapsed, 1)
    except: pass
    return None

def test_upload():
    payload = b"X" * 250000
    try:
        start = time.monotonic()
        rq = req.Request("https://httpbin.org/post", data=payload, method="POST")
        rq.add_header("Content-Type", "application/octet-stream")
        resp = req.urlopen(rq, timeout=20)
        elapsed = time.monotonic() - start
        if elapsed > 0 and resp.status == 200:
            return round((250000 * 8 / 1_000_000) / elapsed, 1)
    except: pass
    return None

def main():
    now = time.time()
    dl = test_download()
    ul = test_upload()
    result = {"time": now, "download_mbps": dl, "upload_mbps": ul, "ok": dl is not None}
    with open(OUT, "w") as f:
        json.dump(result, f)
    if dl: print("OK: Dn={:.1f} Up={:.1f} Mbps".format(dl, ul or 0))
    else: print("FAILED")

if __name__ == "__main__":
    main()
