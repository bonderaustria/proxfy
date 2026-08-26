"""Verbindung zum PVE-Host. Alles laeuft ueber genau diesen Kanal."""
from __future__ import annotations

import dataclasses
import shlex
import subprocess


@dataclasses.dataclass
class Result:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def check(self, what: str) -> "Result":
        if not self.ok:
            raise RuntimeError(f"{what} fehlgeschlagen (rc={self.rc}): {self.err.strip() or self.out.strip()}")
        return self


class Host:
    """Zugriff auf den PVE-Host.

    Zwei Transportwege, damit dasselbe Werkzeug in beiden Betriebsarten laeuft:
      host: "local"  -> direkt auf dem PVE-Host (Appliance-Betrieb)
      sonst          -> ueber SSH (Betrieb aus einem separaten Container)

    Bewusst subprocess statt paramiko: nutzt die vorhandene SSH-Konfiguration
    des Nutzers inklusive known_hosts.
    """

    def __init__(self, host: str, user: str = "root", key_file: str | None = None, port: int = 22):
        self.host, self.user, self.key_file, self.port = host, user, key_file, port
        self.node: str | None = None
        self._node_name: str | None = None

    @property
    def is_local(self) -> bool:
        return self.host in ("local", "")

    def _base(self) -> list[str]:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if self.key_file:
            cmd += ["-i", self.key_file]
        if self.port != 22:
            cmd += ["-p", str(self.port)]
        return cmd + [f"{self.user}@{self.host}"]

    def _exec(self, argv: list[str], timeout: int, label: str) -> Result:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(124, "", f"Zeitueberschreitung nach {timeout}s: {label}")
        return Result(p.returncode, p.stdout, p.stderr)

    def run(self, *args: str, timeout: int = 120) -> Result:
        """Fuehrt argv aus. Argumente werden gequotet - kein Shell-Injection-Pfad."""
        if self.is_local:
            return self._exec(list(args), timeout, args[0] if args else "")
        remote = " ".join(shlex.quote(a) for a in args)
        return self._exec(self._base() + [remote], timeout, remote)

    def sh(self, script: str, timeout: int = 120) -> Result:
        """Fuehrt ein Shell-Snippet aus. Nur fuer intern gebaute Kommandos."""
        if self.is_local:
            return self._exec(["/bin/sh", "-c", script], timeout, script)
        return self._exec(self._base() + [script], timeout, script)

    def ping(self) -> str:
        return self.run("pveversion").check("Verbindung zum PVE-Host").out.strip()

    def node_name(self) -> str:
        if self._node_name is None:
            self._node_name = self.run("hostname").out.strip()
        return self._node_name

    def for_node(self, node: str | None) -> "Host":
        """Liefert einen Zugang, der Kommandos auf dem gewuenschten Cluster-Knoten
        ausfuehrt.

        Restore und Start muessen auf dem Knoten laufen, der den Gast am Ende
        traegt. Innerhalb eines PVE-Clusters duerfen sich die Knoten gegenseitig
        per SSH erreichen - genau das nutzen wir hier.
        """
        if not node or node == self.node_name():
            return self
        return _NodeHost(self, node)


class _NodeHost(Host):
    """Leitet jedes Kommando ueber SSH an einen anderen Cluster-Knoten weiter."""

    def __init__(self, base: Host, node: str):
        super().__init__(base.host, base.user, base.key_file, base.port)
        self._base_host = base
        self.node = node
        self._node_name = node

    def run(self, *args: str, timeout: int = 120) -> Result:
        remote = " ".join(shlex.quote(a) for a in args)
        return self._base_host.run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                   self.node, remote, timeout=timeout)

    def sh(self, script: str, timeout: int = 120) -> Result:
        return self._base_host.run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                   self.node, script, timeout=timeout)
