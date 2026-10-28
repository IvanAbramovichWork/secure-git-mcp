"""Build through this cloud's egress proxy without storing proxy values or CA files in the image."""

import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def main() -> None:
    env = os.environ.copy()
    env.setdefault("DOCKER_CONFIG", "/workspace/.cache/docker")
    args = ["docker", "build", "--network=host"]
    hosts = set()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        if name not in env:
            continue
        args += ["--build-arg", name]
        if name == "NO_PROXY":
            continue
        env[name.lower()] = env[name]
        args += ["--build-arg", name.lower()]
        host = urlsplit(env[name]).hostname
        if host and host not in hosts:
            address = socket.getaddrinfo(
                host, None, socket.AF_INET, socket.SOCK_STREAM
            )[0][4][0]
            args += ["--add-host", f"{host}:{address}"]
            hosts.add(host)
    if env.get("SSL_CERT_FILE"):
        args += ["--secret", "id=ca_bundle,src=" + env["SSL_CERT_FILE"]]
    args += ["-t", "secure-git-mcp:local", "."]
    subprocess.run(
        args, cwd=Path(__file__).resolve().parent.parent, env=env, check=True
    )


if __name__ == "__main__":
    main()
