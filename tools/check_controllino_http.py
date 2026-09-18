#!/usr/bin/env python3
"""Reproducible, no-motion Controllino HTTP timing test (stdlib only).

Keep the normal dashboard running to include its polling traffic. All requests
are GET /status unless --stop-check is explicitly supplied; that adds only
V1 R0 (local Stop), never a start, enable, mode, speed, or move command.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import statistics
import time


def request(host, timeout, stop=False):
    start = time.monotonic()
    phase = "connect"
    try:
        with socket.create_connection((host, 80), timeout=timeout) as sock:
            connected = time.monotonic()
            phase = "response"
            path = "/command?value=V1%20R0" if stop else "/status"
            method = "POST" if stop else "GET"
            sock.sendall((f"{method} {path} HTTP/1.1\r\nHost: {host}\r\n"
                          "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
            body = bytearray()
            reads = 0
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body.extend(chunk)
                reads += 1
                if len(body) > 4096:
                    raise ValueError("oversized response")
            header, payload = bytes(body).split(b"\r\n\r\n", 1)
            if not header.startswith(b"HTTP/1.1 200 "):
                raise ValueError("HTTP request failed")
            status = json.loads(payload)
            if stop:
                if status.get("t") != "a" or status.get("ok") != 1:
                    raise ValueError(f"Stop not acknowledged: {status}")
            elif status.get("mv") != 0 or status.get("en") != 0:
                raise RuntimeError("Controller not stopped/disabled: abort diagnostic")
            return {"connect_ms": round((connected - start) * 1000, 2),
                    "total_ms": round((time.monotonic() - start) * 1000, 2),
                    "reads": reads, "kind": "stop" if stop else "status"}
    except (OSError, ValueError) as exc:
        return {"error": str(exc), "phase": phase, "kind": "stop" if stop else "status"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="10.77.0.10")
    parser.add_argument("--timeout", type=float, default=.75)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--batches", type=int, default=20)
    parser.add_argument("--stop-check", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.batches <= 100 or not 0 < args.timeout <= 5:
        parser.error("batches must be 1..100 and timeout must be >0..5 seconds")
    initial = request(args.host, args.timeout)
    if "error" in initial:
        raise SystemExit(f"Initial status check failed: {initial}")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for batch in range(args.batches):
            # A Stop acknowledgement competes with status traffic when enabled.
            calls = [pool.submit(request, args.host, args.timeout,
                                 args.stop_check and i == 0)
                     for i in range(args.workers)]
            for call in calls:
                result = call.result()
                results.append(result)
                print(json.dumps({"batch": batch, **result}), flush=True)
            time.sleep(.35)
    final = request(args.host, args.timeout)
    good = [r["total_ms"] for r in results if "error" not in r]
    summary = {"requests": len(results), "failures": len(results) - len(good),
               "median_ms": statistics.median(good) if good else None,
               "max_ms": max(good) if good else None, "final_status": final}
    print("SUMMARY", json.dumps(summary), flush=True)
    return int(summary["failures"] != 0 or "error" in final)


if __name__ == "__main__":
    raise SystemExit(main())
