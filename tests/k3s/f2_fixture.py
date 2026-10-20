"""Arena-owned offline TCP/UDP echo and bounded client, Python stdlib only."""
import errno
import json
import socket
import sys
import threading
import time

PORT = 18080


class ProbeMismatch(ValueError):
    """The fixed fixture ran but its traffic expectation did not hold."""


def serve():
    def tcp():
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", PORT))
            listener.listen(4)
            while True:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(5)
                    payload = conn.recv(512)
                    conn.sendall(payload)
                    print(json.dumps({"protocol": "tcp", "payload": payload.decode()}), flush=True)
    threading.Thread(target=tcp, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("0.0.0.0", PORT))
        while True:
            payload, peer = listener.recvfrom(512)
            listener.sendto(payload, peer)
            print(json.dumps({"protocol": "udp", "payload": payload.decode()}), flush=True)


def probe(protocol, host, nonce, deny=False):
    started = time.monotonic()
    # Called in a separately deadline-bounded exec; DNS cannot extend its 5 s limit.
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, PORT, socket.AF_INET)})
    if len(addresses) != 1:
        raise ProbeMismatch("expected exactly one IPv4 Service address")
    payload = f"arena-f2:{protocol}:{nonce}".encode()
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as client:
        client.settimeout(max(0.001, 3 - (time.monotonic() - started)))
        try:
            client.connect((addresses[0], PORT))
            client.sendall(payload)
            received = client.recv(512)
        except OSError as error:
            code = errno.ETIMEDOUT if isinstance(error, TimeoutError) else error.errno
            if deny and code in {errno.ETIMEDOUT, errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.EACCES}:
                print(json.dumps({"protocol": protocol, "host": host, "addresses": addresses,
                                  "payload": payload.decode(), "outcome": "denied", "errno": code}), flush=True)
                return
            if not deny and code in {errno.ETIMEDOUT, errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.EACCES}:
                raise ProbeMismatch("expected echo was refused or timed out") from None
            raise
        if deny:
            raise ProbeMismatch("target TCP unexpectedly passed")
    if received != payload:
        raise ProbeMismatch("echo payload mismatch")
    print(json.dumps({"protocol": protocol, "host": host, "addresses": addresses,
                      "payload": payload.decode(), "received": received.decode(), "count": 1}), flush=True)


def probe_main(action, protocol, host, nonce):
    try:
        probe(protocol, host, nonce, deny=action == "deny")
    except ProbeMismatch as error:
        print(json.dumps({"schema_version": 1, "kind": "arena-f2-probe-mismatch",
                          "action": action, "protocol": protocol, "host": host,
                          "nonce": nonce, "detail": str(error)}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["server"]:
        serve()
    elif sys.argv[1:] == ["idle"]:
        threading.Event().wait()
    elif len(sys.argv) == 5 and sys.argv[1] in {"probe", "deny"} and sys.argv[2] in {"tcp", "udp"}:
        sys.exit(probe_main(*sys.argv[1:]))
    else:
        raise SystemExit("usage: fixture.py server|idle|probe tcp|udp HOST NONCE")
