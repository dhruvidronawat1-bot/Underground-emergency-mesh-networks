# Underground Emergency Mesh Network

A dependency-free Python simulation of emergency messaging across an underground tunnel network. Each node runs as a separate process and communicates over UDP loopback. The simulation demonstrates a custom 16-byte packet header with CRC32, on-demand route discovery, battery/signal-aware route scoring, TTL-limited forwarding, end-to-end acknowledgements, persistent store-carry-forward buffering, and short ADPCM audio messages.

## Quick start

Open three PowerShell windows in this project folder. Start the rescue gateway first, then the relay, then the source. Use the port plan you have already tested:

```powershell
python node.py --id 3 --port 5003 --peer 2=5002
python node.py --id 2 --port 5002 --peer 1=5004 --peer 3=5003 --battery 65
python node.py --id 1 --port 5004 --peer 2=5002
```

At the `mesh>` prompt in **Node 1's** window, enter:

```text
send 3 Water needed at the east tunnel
```

Node 3 should print the received message. Node 1 should show route discovery, the selected next hop, and a delivery confirmation. Commands: `help`, `routes`, `buffer`, `send <node_id> <message>`, `sos <node_id> <message>`, `audio <node_id> <wav_path>`, and `quit`.

## Demo scenarios

See [DEMO_GUIDE.md](DEMO_GUIDE.md) for the presentation sequence, sample emergency messages, a partition/recovery demonstration, audio instructions, and Wireshark capture steps.

## Packet format and routing

The big-endian network header is exactly 16 bytes: type (1), source ID (2), destination ID (2), sequence (2), TTL (1), battery percentage (1), RSSI metric (1), payload length (2), and CRC32 (4). CRC32 covers the first 12 header bytes plus the payload. Packet types are HELLO `0x00`, RREQ `0x01`, RREP `0x02`, DATA `0x03`, and ACK `0x04`.

Route discovery accumulates a cost based on hop count, advertised battery, and a unitless RSSI score: `hops + 2*(100/battery) + 20*(1/RSSI)`. Lower scores are preferred. TTL is decremented as packets traverse relays. Since localhost UDP provides no radio signal measurement, RSSI is a simulated metric (default 160), not a real measurement.

Each node stores its DTN queue as `node_<id>_dtn.json` in its current working directory. Queued packets are restored after a restart, retried when routes become available, and removed after end-to-end ACK confirmation. A source retries if an ACK does not arrive. The queue is bounded to 500 packets per node.

## Audio

Use a short uncompressed PCM 16-bit WAV clip (up to five seconds). Example at Node 1:

```text
audio 3 C:\Users\Dhruvi\Downloads\distress.wav
```

The sender encodes it using the project's compact IMA ADPCM format. The destination decodes it and saves `received_audio_node<source>_seq<sequence>.wav` in the destination node's working folder. The codec is a lightweight simulation codec and does not promise radio-grade speech quality.

## Run verification

```powershell
python -B -m unittest -v
```

The packet and audio unit tests run without network access. The relay recovery integration test uses local UDP and may be skipped in restricted environments that block loopback sockets; run it on the same Windows machine where the multi-node demo works.

## Wireshark

Capture the loopback adapter and filter for `udp.port == 5002 || udp.port == 5003 || udp.port == 5004`. The UDP payload starts with the custom 16-byte header. Unless a dissector is added, Wireshark shows it as raw bytes. This simulator verifies software behavior over localhost; it does not model real tunnel radio propagation, physical battery drain, or measured RSSI.
