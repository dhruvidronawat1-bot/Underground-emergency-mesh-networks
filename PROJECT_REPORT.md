# Underground Emergency Mesh Network

## Project summary

This project simulates emergency communication when a tunnel network is disconnected from normal infrastructure. A group of independent Python nodes exchange packets over local UDP sockets. Nodes discover multi-hop paths, forward text or distress audio through relays, acknowledge delivery, and retain messages while a route is unavailable.

## Objectives and implementation

- **Compact packet protocol:** A fixed 16-byte, big-endian header contains packet type, source and destination IDs, sequence number, TTL, battery percentage, RSSI metric, payload length, and CRC32. Invalid lengths and checksums are rejected.
- **On-demand multi-hop routes:** RREQ packets discover paths and RREP packets carry the selected path back. Nodes prefer lower route costs using `hops + 2*(100/battery) + 20*(1/RSSI)`. TTL decreases as packets are relayed.
- **Cross-layer input:** Battery and signal metrics contribute to route choice. In this desktop simulation, RSSI is a configurable-style placeholder metric (default 160); localhost UDP cannot measure radio signal strength.
- **Store-carry-forward recovery:** Messages with no route are written to a per-node JSON queue. The queue reloads after restart and retries discovery. The source waits for an end-to-end ACK and retries if confirmation does not arrive.
- **Distress audio:** A short 16-bit PCM WAV clip is encoded with the included IMA ADPCM implementation, sent as a packet, decoded at the destination, and saved as a new WAV file.

## Architecture

`protocol.py` packs and validates wire packets. `node.py` runs one threaded node process with a UDP receiver, interactive console, routing state, maintenance/retry loop, and DTN queue. `audio_codec.py` handles the audio conversion. The tests cover framing, CRC checks, route score behavior, audio encode/decode, queue persistence, and a multi-process relay recovery scenario.

## Running the demonstration

See [DEMO_GUIDE.md](DEMO_GUIDE.md) for the complete instructor sequence. The tested three-node line is Node 1 → Node 2 → Node 3, using ports 5004, 5002, and 5003. Send a message from Node 1 with `send 3 <message>`. Stop Node 2 to create a partition, send an SOS, and restart Node 2 to demonstrate buffered delivery.

## Verification

Run `python -B -m unittest -v`. The completed suite reports six passing tests, including the multi-process relay recovery test when localhost UDP is available. During verification, the source sent a message while the relay was absent; after the relay started, the destination received it and the source received delivery confirmation.

For a live packet demonstration, capture loopback UDP in Wireshark using `udp.port == 5002 || udp.port == 5003 || udp.port == 5004`. The simulator currently exposes its custom header as raw UDP payload bytes; see the demo guide for evidence to save.

## Scope and limitations

This is a software simulation, not a deployed mine-safety device. It does not model physical radio propagation, actual battery discharge, real RSSI measurements, hardware sensors, or certified emergency communications. The audio codec is a compact demonstration format; audio quality depends on the input and is not guaranteed. A local Wireshark capture and screenshots should be collected on the presentation computer as described in the demo guide.
