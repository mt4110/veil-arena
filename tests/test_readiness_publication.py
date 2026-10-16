"""Coordinate the exact pre-write window; do not rely on scheduler luck."""
import contextlib
import os
import pathlib
import select
import signal
import subprocess
import tempfile
import time
import unittest


FIXTURE = pathlib.Path(__file__).parents[1] / "src/readiness-publication.sh"


@contextlib.contextmanager
def child(args):
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        yield process
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


def receive(process, deadline):
    readable, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
    if not readable:
        raise AssertionError("publication checkpoint deadline")
    # Each checkpoint is one short newline-terminated printf to this pipe.
    return process.stdout.readline()


def send(process, line):
    process.stdin.write(line + b"\n")
    process.stdin.flush()


class ReadinessPublicationTests(unittest.TestCase):
    def directory(self):
        # Preserve test evidence, including on failure.
        return pathlib.Path(tempfile.mkdtemp(prefix="arena-readiness-"))

    def test_direct_redirection_exposes_empty_marker_before_payload(self):
        marker = self.directory() / "ready"
        script = ': > "$1"; printf "opened\\n"; read -r publish; printf ready > "$1"; printf "written\\n"; read -r stop'
        deadline = time.monotonic() + 8
        with child(["sh", "-ec", script, "arena-legacy", str(marker)]) as process:
            self.assertEqual(receive(process, deadline), b"opened\n")
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), b"")
            send(process, b"publish")
            self.assertEqual(receive(process, deadline), b"written\n")
            self.assertEqual(marker.read_bytes(), b"ready")
            send(process, b"stop")
            self.assertEqual(process.wait(timeout=max(0, deadline - time.monotonic())), 0)

    def test_shared_fixture_hides_marker_until_complete_publication(self):
        marker = self.directory() / "ready"
        deadline = time.monotonic() + 8
        with child(["sh", str(FIXTURE), str(marker), "gated"]) as process:
            self.assertEqual(receive(process, deadline), b"staged\n")
            self.assertEqual(pathlib.Path(str(marker) + ".pending").read_bytes(), b"ready")
            self.assertFalse(marker.exists())
            send(process, b"publish")
            while not marker.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), b"ready")
            send(process, b"stop")
            self.assertEqual(process.wait(timeout=max(0, deadline - time.monotonic())), 0)


if __name__ == "__main__":
    unittest.main()
