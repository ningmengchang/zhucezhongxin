"""Start the real HTTP server and verify API plus built frontend."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
import os


def get(url: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.headers.get_content_type(), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode("utf-8")


def main() -> None:
    environment = os.environ.copy()
    environment["LEGAL_ASSISTANT_HOST"] = "127.0.0.1"
    environment["LEGAL_ASSISTANT_PORT"] = "8090"
    process = subprocess.Popen(
        [sys.executable, "-m", "app"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    try:
        for _ in range(40):
            try:
                health = get("http://127.0.0.1:8090/api/health")
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("HTTP server did not become ready")
        frontend = get("http://127.0.0.1:8090/")
        payload = json.loads(health[2])
        assert health[0] == 401 and payload["detail"]["code"] == "authentication_required"
        assert frontend[0] == 200 and "text/html" in frontend[1]
        assert "法规助手" in frontend[2]
        print(json.dumps({"health": payload, "frontend": frontend[:2]}, ensure_ascii=False))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
