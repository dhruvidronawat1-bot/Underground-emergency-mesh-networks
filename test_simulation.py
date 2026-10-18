import subprocess
import sys
import time
import unittest
import socket
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class RelayRecoveryTests(unittest.TestCase):
    def test_buffered_message_is_delivered_after_relay_joins(self):
        # Some managed sandboxes block loopback sockets entirely. Keep this integration
        # check runnable on a normal desktop while still reporting that limitation cleanly.
        try:
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(0.25)
            sender.sendto(b"probe", receiver.getsockname())
            receiver.recvfrom(16)
        except OSError as exc:
            self.skipTest(f"loopback UDP is unavailable in this environment: {exc}")
        finally:
            try:
                receiver.close()
                sender.close()
            except (UnboundLocalError, OSError):
                pass
        processes = {}
        try:
            def start(name, *args):
                processes[name] = subprocess.Popen(
                    [sys.executable, "-u", "node.py", *args], cwd=ROOT,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True,
                )

            start("source", "--id", "1", "--port", "5311", "--peer", "2=5312")
            start("gateway", "--id", "3", "--port", "5313", "--peer", "2=5312")
            time.sleep(0.6)
            processes["source"].stdin.write("send 3 recovery-check\n")
            processes["source"].stdin.flush()
            time.sleep(2.5)
            start("relay", "--id", "2", "--port", "5312", "--peer", "1=5311",
                  "--peer", "3=5313", "--battery", "65")
            time.sleep(5)
            self.assertIsNone(processes["source"].poll())
            self.assertIsNone(processes["relay"].poll())
            self.assertIsNone(processes["gateway"].poll())
            for proc in processes.values():
                proc.stdin.write("quit\n")
                proc.stdin.flush()
            logs = {name: proc.communicate(timeout=3)[0] for name, proc in processes.items()}
            self.assertIn("Forwarded buffered packet to 3", logs["source"], logs)
            self.assertIn("Received text from node 1: recovery-check", logs["gateway"], logs)
        finally:
            for proc in processes.values():
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
