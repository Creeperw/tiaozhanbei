"""Persist one verified proxy node; run on the server with its JSON on stdin."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
import yaml


def main():
    node = json.load(sys.stdin)
    if node.get("type") != "tuic":
        raise ValueError("Expected the verified TUIC node")
    path = Path("/etc/mihomo/config.yaml")
    original_stat = path.stat()
    original = yaml.safe_load(path.read_text())
    config = yaml.safe_load(path.read_text())
    name = "node-28"
    if any(item["name"] == name for item in config["proxies"]):
        raise ValueError("Target node already exists; inspect before retrying")
    node["name"] = name
    config["proxies"].append(node)
    group = next(g for g in config["proxy-groups"] if g["name"] == "MODEL_PROXY")
    group["proxies"] = [name, *group["proxies"]]
    config.setdefault("profile", {})["store-selected"] = True
    backup = Path("/srv/tiaozhanbei-backups") / time.strftime("vpn-repair-%Y%m%d-%H%M%S")
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copy2(path, backup / "config.yaml")
    os.chmod(backup / "config.yaml", 0o600)
    headers = {
        "Authorization": "Bearer " + str(original.get("secret", "")),
        "Content-Type": "application/json",
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def select(selected):
        request = urllib.request.Request(
            "http://127.0.0.1:9090/proxies/MODEL_PROXY",
            data=json.dumps({"name": selected}).encode(),
            headers=headers,
            method="PUT",
        )
        with opener.open(request, timeout=10) as response:
            assert response.status == 204

    fd, temporary = tempfile.mkstemp(prefix="config-validated-", suffix=".yaml", dir=path.parent)
    changed = False
    try:
        with os.fdopen(fd, "w") as stream:
            yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
        validation = subprocess.run(
            ["/usr/local/bin/mihomo", "-t", "-d", "/var/lib/mihomo", "-f", temporary],
            capture_output=True,
            timeout=30,
        )
        if validation.returncode:
            raise RuntimeError("Proxy configuration validation failed")
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        os.chmod(temporary, original_stat.st_mode & 0o777)
        os.replace(temporary, path)
        changed = True
        request = urllib.request.Request(
            "http://127.0.0.1:9090/configs?force=true",
            data=json.dumps({"payload": yaml.safe_dump(config)}).encode(),
            headers=headers,
            method="PUT",
        )
        with opener.open(request, timeout=15) as response:
            assert response.status == 204
        select(name)
        subprocess.run(["systemctl", "restart", "mihomo.service"], check=True, timeout=30)
        # The authenticated controller confirms the selected node after restart.
        with httpx.Client(trust_env=False, timeout=5, transport=httpx.HTTPTransport(retries=4)) as client:
            response = client.get("http://127.0.0.1:9090/proxies/MODEL_PROXY", headers=headers)
            response.raise_for_status()
            assert response.json()["now"] == name
        for attempt in range(3):
            start = time.monotonic()
            with httpx.Client(proxy="http://127.0.0.1:7890", trust_env=False, timeout=20) as client:
                response = client.get("https://opencode.ai/zen/v1/models")
                response.raise_for_status()
                assert response.status_code == 200
            print(json.dumps({"probe": attempt + 1, "status": response.status_code, "seconds": round(time.monotonic() - start, 2)}), flush=True)
        print(json.dumps({"selected": name, "backup": str(backup), "persisted": True, "restart_verified": True}))
    except Exception as exc:
        if changed:
            shutil.copy2(backup / "config.yaml", path)
            os.chown(path, original_stat.st_uid, original_stat.st_gid)
            os.chmod(path, original_stat.st_mode & 0o777)
            subprocess.run(["systemctl", "restart", "mihomo.service"], check=True, timeout=30)
        print(json.dumps({"error": type(exc).__name__, "rolled_back": changed}), flush=True)
        raise SystemExit(1) from None
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()