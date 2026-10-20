"""Arena-owned offline TCP/UDP echo and bounded client, Python stdlib only."""
import json
import socket
import sys
import threading
import time

PORT = 18080


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


def probe(protocol, host, nonce):
    started = time.monotonic()
    # Called in a separately deadline-bounded exec; DNS cannot extend its 5 s limit.
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, PORT, socket.AF_INET)})
    if len(addresses) != 1:
        raise ValueError("expected exactly one IPv4 Service address")
    payload = f"arena-f1:{protocol}:{nonce}".encode()
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as client:
        client.settimeout(max(0.001, 5 - (time.monotonic() - started)))
        client.connect((addresses[0], PORT))
        client.sendall(payload)
        received = client.recv(512)
    if received != payload:
        raise ValueError("echo payload mismatch")
    print(json.dumps({"protocol": protocol, "host": host, "addresses": addresses,
                      "payload": payload.decode(), "received": received.decode(), "count": 1}), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["server"]:
        serve()
    elif sys.argv[1:] == ["idle"]:
        threading.Event().wait()
    elif len(sys.argv) == 5 and sys.argv[1] == "probe" and sys.argv[2] in {"tcp", "udp"}:
        probe(*sys.argv[2:])
    else:
        raise SystemExit("usage: fixture.py server|idle|probe tcp|udp HOST NONCE")
