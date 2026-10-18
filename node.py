"""A single independently runnable UDP mesh node."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import threading
import time
from dataclasses import dataclass

from protocol import BROADCAST_ID, PacketError, PacketType, create_packet, parse_packet
from audio_codec import decode_adpcm, encode_wav


@dataclass
class Route:
    next_hop: int
    next_hop_port: int
    cost: float
    hops: int
    updated: float


class MeshNode:
    def __init__(self, node_id: int, port: int, peers: dict[int, int],
                 battery: int = 100, bind: str = "127.0.0.1",
                 retry_interval: float = 2.0, route_timeout: float = 30.0,
                 ack_timeout: float = 5.0, buffer_path: str | None = None):
        if not 0 <= node_id < BROADCAST_ID:
            raise ValueError("node ID must be between 0 and 65534")
        if not 0 <= battery <= 100:
            raise ValueError("battery must be between 0 and 100")
        self.node_id, self.port, self.peers = node_id, port, dict(peers)
        self.battery, self.bind = battery, bind
        self.retry_interval, self.route_timeout = retry_interval, route_timeout
        self.ack_timeout = ack_timeout
        # Avoid reusing sequence numbers when a process restarts while old DTN
        # packets may still be waiting in its on-disk queue.
        self.sequence = int(time.time_ns() & 0xFFFF)
        self.routes: dict[int, Route] = {}
        self.route_request_sequences: dict[int, int] = {}
        self.reverse_routes: dict[int, tuple[int, int, float]] = {}
        self.seen_rreq: dict[tuple[int, int], tuple[float, float]] = {}
        self.neighbors: dict[int, tuple[int, float]] = {}
        self.buffer_path = Path(buffer_path or f"node_{node_id}_dtn.json")
        self.dtn_buffer = self._load_buffer()
        self.pending_acks: dict[tuple[int, int], dict] = {}
        self.delivered_packets: dict[tuple[int, int], float] = {}
        self.lock = threading.RLock()
        self.running = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.sock.settimeout(0.5)

    @staticmethod
    def path_cost(hops: int, battery: int, rssi: int,
                  weights: tuple[float, float, float] = (1.0, 2.0, 20.0)) -> float:
        """Lower is better; RSSI is encoded as 0..255, where larger is stronger."""
        w1, w2, w3 = weights
        return w1 * hops + w2 * (100 / max(1, battery)) + w3 * (1 / max(1, rssi))

    def _next_seq(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFFFF
        return self.sequence

    def _load_buffer(self) -> list[dict]:
        """Restore packets queued by an earlier run of this node."""
        if not self.buffer_path.exists():
            return []
        try:
            rows = json.loads(self.buffer_path.read_text(encoding="utf-8"))
            result = []
            for row in rows:
                packet = base64.b64decode(row["packet"], validate=True)
                parsed = parse_packet(packet)
                if parsed["dst"] != int(row["destination"]):
                    continue
                result.append({"destination": parsed["dst"], "packet": packet,
                               "queued": float(row.get("queued", time.time())),
                               "discovery": 0.0})
            return result[:500]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"Could not restore DTN queue {self.buffer_path}: {exc}", flush=True)
            return []

    def _persist_buffer(self):
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [{"destination": item["destination"],
                     "packet": base64.b64encode(item["packet"]).decode("ascii"),
                     "queued": item["queued"]} for item in self.dtn_buffer]
            temporary = self.buffer_path.with_suffix(self.buffer_path.suffix + ".tmp")
            temporary.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.buffer_path)
        except OSError as exc:
            print(f"Could not save DTN queue: {exc}", flush=True)

    def _queue_packet(self, destination: int, packet: bytes, discovery: float = 0.0):
        parsed = parse_packet(packet)
        key = (parsed["src"], parsed["seq"], parsed["dst"])
        with self.lock:
            for item in self.dtn_buffer:
                old = parse_packet(item["packet"])
                if (old["src"], old["seq"], old["dst"]) == key:
                    item["discovery"] = min(item.get("discovery", 0.0), discovery)
                    return
            if len(self.dtn_buffer) >= 500:
                self.dtn_buffer.pop(0)
            self.dtn_buffer.append({"destination": destination, "packet": packet,
                                    "queued": time.time(), "discovery": discovery})
            self._persist_buffer()

    def _track_delivery(self, packet: bytes):
        parsed = parse_packet(packet)
        if parsed["src"] == self.node_id:
            self.pending_acks[(parsed["src"], parsed["seq"])] = {
                "destination": parsed["dst"], "packet": packet,
                "last_sent": time.time(), "retries": 0}

    def _rssi_metric(self, peer_id: int | None = None) -> int:
        # UDP loopback has no radio RSSI. CLI peers can be treated as virtual radio links;
        # this value is a documented simulation input, not a measured signal.
        return 160

    def _frame(self, p_type: PacketType, dst: int, payload: bytes = b"", ttl: int = 16):
        return create_packet(p_type, self.node_id, dst, self._next_seq(), ttl,
                             self.battery, self._rssi_metric(), payload)

    def _send_raw(self, peer_id: int, packet: bytes) -> bool:
        port = self.peers.get(peer_id)
        if port is None:
            return False
        try:
            self.sock.sendto(packet, (self.bind, port))
            # The send only confirms local OS acceptance; end-to-end delivery is separate.
            return True
        except OSError:
            return False

    def _broadcast(self, packet: bytes, except_id: int | None = None):
        sent = False
        for peer_id in self.peers:
            if peer_id != except_id:
                sent = self._send_raw(peer_id, packet) or sent
        return sent

    def start(self):
        self.running.set()
        threading.Thread(target=self._receiver, name=f"node-{self.node_id}-rx", daemon=True).start()
        threading.Thread(target=self._maintenance, name=f"node-{self.node_id}-maintenance", daemon=True).start()
        print(f"Node {self.node_id} listening on {self.bind}:{self.port}; peers={self.peers}", flush=True)
        try:
            while self.running.is_set():
                line = input("mesh> ").strip()
                if not line:
                    continue
                self._command(line)
        except (EOFError, KeyboardInterrupt):
            print("\nStopping node...", flush=True)
        finally:
            self.stop()

    def stop(self):
        self.running.clear()
        self.sock.close()

    def _command(self, line: str):
        parts = line.split(maxsplit=2)
        command = parts[0].lower()
        if command in {"send", "sos"} and len(parts) >= 3:
            try:
                self.send_text(int(parts[1]), parts[2], distress=(command == "sos"))
            except ValueError as exc:
                print(f"Invalid destination: {exc}")
        elif command == "audio" and len(parts) == 3:
            try:
                encoded, rate, duration = encode_wav(Path(parts[2]).read_bytes())
                self.send_audio(int(parts[1]), encoded, rate, duration_ms=duration)
                print(f"Queued {duration} ms distress clip ({len(encoded)} ADPCM bytes)", flush=True)
            except (OSError, ValueError) as exc:
                print(f"Could not encode audio: {exc}", flush=True)
        elif command == "routes":
            with self.lock:
                print({dest: vars(route) for dest, route in self.routes.items()})
        elif command == "buffer":
            with self.lock:
                print(f"{len(self.dtn_buffer)} message(s) queued for retry")
        elif command in {"help", "?"}:
            print("send <node_id> <message> | sos <node_id> <message> | audio <node_id> <wav_path> | routes | buffer | quit")
        elif command in {"quit", "exit"}:
            self.stop()
        else:
            print("Unknown command. Type help.")

    def send_text(self, destination: int, message: str, distress: bool = False):
        body = {"kind": "sos" if distress else "text", "message": message,
                "created": time.time()}
        packet = self._frame(PacketType.DATA, destination,
                             json.dumps(body, separators=(",", ":")).encode())
        self._deliver_or_buffer(destination, packet)

    def send_audio(self, destination: int, encoded_audio: bytes, sample_rate: int,
                   codec: str = "ima-adpcm", duration_ms: int | None = None):
        body = {"kind": "audio", "codec": codec, "sample_rate": sample_rate,
                "audio": encoded_audio.hex(), "created": time.time()}
        if duration_ms is not None:
            body["duration_ms"] = duration_ms
        packet = self._frame(PacketType.DATA, destination,
                             json.dumps(body, separators=(",", ":")).encode())
        self._deliver_or_buffer(destination, packet)

    def _deliver_or_buffer(self, destination: int, packet: bytes):
        with self.lock:
            route = self.routes.get(destination)
            if route and time.time() - route.updated < self.route_timeout:
                if self._send_raw(route.next_hop, packet):
                    self._track_delivery(packet)
                    print(f"Sent to node {destination} via {route.next_hop}", flush=True)
                    return
            self._queue_packet(destination, packet)
            print(f"No route to {destination}; stored in DTN buffer", flush=True)
            self._discover(destination)

    def _discover(self, destination: int):
        with self.lock:
            seq = self._next_seq()
            self.seen_rreq[(self.node_id, seq)] = (0.0, time.time())
        payload = json.dumps({"origin": self.node_id, "destination": destination,
                              "request_seq": seq, "hops": 0,
                              "cost": 0.0, "path": [self.node_id]}, separators=(",", ":")).encode()
        self._broadcast(self._frame(PacketType.RREQ, BROADCAST_ID, payload))
        print(f"Route discovery started for node {destination}", flush=True)

    def _receiver(self):
        while self.running.is_set():
            try:
                raw, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                # Windows may surface an ICMP Port Unreachable for an earlier UDP
                # send as WSAECONNRESET (10054). It is not a fatal listener error.
                if getattr(exc, "winerror", None) == 10054 or getattr(exc, "errno", None) == 10054:
                    continue
                print(f"UDP receiver stopped unexpectedly: {exc}", flush=True)
                break
            try:
                packet = parse_packet(raw)
            except PacketError as exc:
                print(f"Dropped malformed packet from {address}: {exc}", flush=True)
                continue
            if packet["src"] == self.node_id or packet["ttl"] == 0:
                continue
            # The 16-byte header preserves the originating source ID across relays.
            # On localhost, map the UDP sender port to the immediate neighbor instead.
            peer = next((peer_id for peer_id, peer_port in self.peers.items()
                         if peer_port == address[1]), None)
            if peer is None:
                continue
            with self.lock:
                self.neighbors[peer] = (packet["battery"], time.time())
            try:
                self._handle(packet, peer)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                print(f"Dropped invalid {packet['type']} payload from {peer}: {exc}", flush=True)

    def _handle(self, packet: dict, peer: int):
        ptype = packet["type_code"]
        if ptype == PacketType.HELLO:
            return
        if ptype == PacketType.RREQ:
            self._handle_rreq(packet, peer)
        elif ptype == PacketType.RREP:
            self._handle_rrep(packet, peer)
        elif ptype == PacketType.DATA:
            self._handle_data(packet, peer)
        elif ptype == PacketType.ACK:
            self._handle_ack(packet, peer)

    def _handle_rreq(self, packet: dict, peer: int):
        msg = json.loads(packet["payload"])
        origin, destination, request_seq = msg["origin"], msg["destination"], msg["request_seq"]
        path = msg["path"]
        if self.node_id in path:
            return
        self.reverse_routes[origin] = (peer, self.peers[peer], time.time())
        hop_count = int(msg["hops"]) + 1
        local_cost = self.path_cost(hop_count, packet["battery"], packet["rssi"])
        total_cost = float(msg["cost"]) + local_cost
        key = (origin, request_seq)
        now = time.time()
        previous = self.seen_rreq.get(key)
        if previous is not None and now - previous[1] < 60 and total_cost >= previous[0]:
            return
        self.seen_rreq[key] = (total_cost, now)
        old_origin_route = self.routes.get(origin)
        previous_seq = self.route_request_sequences.get(origin)
        newer_seq = (previous_seq is None or
                     (0 < ((request_seq - previous_seq) & 0xFFFF) < 0x8000))
        if newer_seq or old_origin_route is None or total_cost < old_origin_route.cost:
            self.routes[origin] = Route(peer, self.peers[peer], total_cost, hop_count, now)
            if newer_seq:
                self.route_request_sequences[origin] = request_seq
        path = path + [self.node_id]
        if destination == self.node_id:
            reply = json.dumps({"origin": origin, "destination": destination,
                                "request_seq": request_seq, "hops": hop_count,
                                "cost": total_cost, "path": path}, separators=(",", ":")).encode()
            self._send_raw(peer, self._frame(PacketType.RREP, origin, reply))
            return
        forwarded = dict(msg, hops=hop_count, cost=total_cost, path=path)
        self._broadcast(self._frame(PacketType.RREQ, BROADCAST_ID,
                                    json.dumps(forwarded, separators=(",", ":")).encode(),
                                    ttl=max(0, packet["ttl"] - 1)), except_id=peer)

    def _handle_rrep(self, packet: dict, peer: int):
        msg = json.loads(packet["payload"])
        origin, destination = msg["origin"], msg["destination"]
        path = msg["path"]
        if self.node_id not in path:
            return
        pos = path.index(self.node_id)
        if pos + 1 >= len(path):
            # This node is the destination for the discovered route.
            return
        next_hop = path[pos + 1]
        hops = len(path) - pos - 1
        cost = float(msg["cost"])
        old = self.routes.get(destination)
        if old is None or cost < old.cost or time.time() - old.updated > self.route_timeout:
            self.routes[destination] = Route(next_hop, self.peers[next_hop], cost, hops, time.time())
        if self.node_id == origin:
            print(f"Route ready to {destination}: next hop {next_hop}, cost {cost:.3f}", flush=True)
            self._flush_buffer(destination)
            return
        previous_hop = path[pos - 1]
        forwarded = create_packet(PacketType.RREP, packet["src"], packet["dst"], packet["seq"],
                                  packet["ttl"] - 1, packet["battery"], packet["rssi"],
                                  packet["payload"])
        self._send_raw(previous_hop, forwarded)

    def _handle_data(self, packet: dict, peer: int):
        if packet["dst"] == self.node_id:
            delivery_key = (packet["src"], packet["seq"])
            duplicate = delivery_key in self.delivered_packets
            self.delivered_packets[delivery_key] = time.time()
            try:
                body = json.loads(packet["payload"])
                if body.get("kind") == "audio":
                    encoded = bytes.fromhex(body["audio"])
                    wav_bytes = decode_adpcm(encoded, int(body["sample_rate"]))
                    output = Path.cwd() / f"received_audio_node{packet['src']}_seq{packet['seq']}.wav"
                    output.write_bytes(wav_bytes)
                    print(f"Received audio SOS from {packet['src']}; saved {output.name} "
                          f"({body.get('duration_ms', '?')} ms; duplicate={duplicate})", flush=True)
                else:
                    print(f"Received {body.get('kind', 'message')} from node {packet['src']}: "
                          f"{body.get('message', '')}" + (" (duplicate)" if duplicate else ""), flush=True)
            except json.JSONDecodeError:
                print(f"Received binary payload from node {packet['src']} ({len(packet['payload'])} bytes)", flush=True)
            ack = json.dumps({"original_src": packet["src"], "original_seq": packet["seq"]}).encode()
            route = self.routes.get(packet["src"])
            if route:
                self._send_raw(route.next_hop, self._frame(PacketType.ACK, packet["src"], ack))
            return
        route = self.routes.get(packet["dst"])
        if route and packet["ttl"] > 1 and self._send_raw(route.next_hop,
                create_packet(PacketType.DATA, packet["src"], packet["dst"], packet["seq"],
                              packet["ttl"] - 1, packet["battery"], self._rssi_metric(), packet["payload"])):
            return
        forwarded = create_packet(PacketType.DATA, packet["src"], packet["dst"], packet["seq"],
                                  packet["ttl"] - 1, packet["battery"], packet["rssi"], packet["payload"])
        self._queue_packet(packet["dst"], forwarded)
        print(f"No onward route for {packet['dst']}; stored in DTN buffer", flush=True)
        self._discover(packet["dst"])

    def _handle_ack(self, packet: dict, peer: int):
        if packet["dst"] == self.node_id:
            try:
                body = json.loads(packet["payload"])
                key = (int(body["original_src"]), int(body["original_seq"]))
                self.pending_acks.pop(key, None)
                self.dtn_buffer = [item for item in self.dtn_buffer
                                   if not ((p := parse_packet(item["packet"]))["src"] == key[0]
                                           and p["seq"] == key[1])]
                self._persist_buffer()
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                body = packet["payload"].decode(errors="replace")
            print(f"Delivery confirmed: {body}", flush=True)
            return
        route = self.routes.get(packet["dst"])
        if route and packet["ttl"] > 1:
            self._send_raw(route.next_hop, create_packet(PacketType.ACK, packet["src"], packet["dst"],
                            packet["seq"], packet["ttl"] - 1, packet["battery"], packet["rssi"], packet["payload"]))

    def _flush_buffer(self, destination: int):
        route = self.routes.get(destination)
        if not route:
            return
        remaining = []
        with self.lock:
            queued = self.dtn_buffer
            self.dtn_buffer = []
        for item in queued:
            if item["destination"] == destination and self._send_raw(route.next_hop, item["packet"]):
                self._track_delivery(item["packet"])
                print(f"Forwarded buffered packet to {destination}", flush=True)
            else:
                remaining.append(item)
        with self.lock:
            self.dtn_buffer.extend(remaining)
            self._persist_buffer()

    def _maintenance(self):
        next_hello = 0.0
        while self.running.is_set():
            time.sleep(self.retry_interval)
            if not self.running.is_set():
                break
            now = time.time()
            if now >= next_hello:
                self._broadcast(self._frame(PacketType.HELLO, BROADCAST_ID,
                                            json.dumps({"node": self.node_id}).encode(), ttl=1))
                next_hello = now + self.retry_interval
            with self.lock:
                for peer, (_, seen) in list(self.neighbors.items()):
                    if now - seen > self.route_timeout / 3:
                        self.neighbors.pop(peer, None)
                        for dest, route in list(self.routes.items()):
                            if route.next_hop == peer:
                                self.routes.pop(dest, None)
                for dest, route in list(self.routes.items()):
                    if now - route.updated > self.route_timeout:
                        self.routes.pop(dest, None)
                retry_destinations = set()
                for key, item in list(self.pending_acks.items()):
                    if now - item["last_sent"] >= self.ack_timeout:
                        self.pending_acks.pop(key, None)
                        self.routes.pop(item["destination"], None)
                        self._queue_packet(item["destination"], item["packet"])
                        retry_destinations.add(item["destination"])
                pending = [(item["destination"], item) for item in self.dtn_buffer
                           if now - item["discovery"] >= self.retry_interval]
                for _, item in pending:
                    item["discovery"] = now
            for destination in retry_destinations:
                self._discover(destination)
            for destination in set(dest for dest, _ in pending) - retry_destinations:
                self._discover(destination)
                self._flush_buffer(destination)


def parse_peers(values: list[str]) -> dict[int, int]:
    peers = {}
    for value in values:
        try:
            peer_id, peer_port = value.split("=", 1)
            peers[int(peer_id)] = int(peer_port)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"peer must be ID=PORT, got {value!r}") from exc
    return peers


def main():
    parser = argparse.ArgumentParser(description="Run one underground mesh UDP node")
    parser.add_argument("--id", type=int, required=True, help="16-bit node ID")
    parser.add_argument("--port", type=int, required=True, help="local UDP port")
    parser.add_argument("--peer", action="append", default=[], metavar="ID=PORT",
                        help="directly reachable neighbor (repeat for each peer)")
    parser.add_argument("--battery", type=int, default=100, help="battery percentage 0..100")
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    try:
        MeshNode(args.id, args.port, parse_peers(args.peer), args.battery, args.bind).start()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
