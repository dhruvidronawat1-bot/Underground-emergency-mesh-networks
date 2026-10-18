# Instructor Demo Guide

## Before the demo

1. Open three PowerShell windows in the folder containing `node.py`.
2. In each window, start one node using the commands below. Leave each process running at its `mesh>` prompt.

```powershell
# Window 1: rescue gateway (start first)
python node.py --id 3 --port 5003 --peer 2=5002

# Window 2: relay in the middle of the tunnel
python node.py --id 2 --port 5002 --peer 1=5004 --peer 3=5003 --battery 65

# Window 3: trapped team / message source
python node.py --id 1 --port 5004 --peer 2=5002
```

## Demo 1: emergency text and routing

Type these at the `mesh>` prompt in **Node 1**. Node 3 is the destination.

```text
send 3 Water needed at the east tunnel
sos 3 Survivor located beside the collapsed shaft
send 3 Team of four is safe in chamber B; battery at 40 percent
send 3 Need medical kit at junction 7; one miner has an injured leg
```

Node 1 should report that a route is ready and delivery is confirmed. Node 3 should print each received message. On Node 1, type `routes` to display the learned path and `buffer` to show the current queued-message count.

## Demo 2: store, carry, and forward through a partition

1. Stop Node 2 with `Ctrl+C`. Keep Node 1 and Node 3 running.
2. At Node 1, send `sos 3 Need evacuation; the east shaft is blocked`.
3. Node 1 should report that the message is queued and route discovery is retrying. Type `buffer` to show the queue.
4. Restart Node 2 with its original command. Wait for Node 1 to rediscover the path. Node 3 should receive the queued SOS and Node 1 should confirm delivery.

For a persistence extension, stop Node 1 while it has an undelivered message, restart it with the same command, and let discovery resume. The queue file is `node_1_dtn.json` in the terminal's current directory. Do not delete the JSON file until after the demo.

## Demo 3: audio distress clip

Use a short, uncompressed 16-bit PCM WAV file of at most five seconds. At Node 1, type:

```text
audio 3 C:\full\path\to\distress.wav
```

Node 3 decodes the received clip and saves `received_audio_node1_seq<number>.wav` in its current working folder. Open that WAV file to play the recovered clip. The implementation downsamples audio above 8 kHz and uses a small IMA ADPCM encoder for this simulation.

## Demo 4: packet evidence in Wireshark

1. Open Wireshark and select the loopback adapter. Start capture before sending a message.
2. Use display filter `udp.port == 5002 || udp.port == 5003 || udp.port == 5004`.
3. Send one text message from Node 1 to Node 3.
4. Select a UDP packet and expand its payload or inspect the hex pane. The UDP payload begins with the 16-byte custom header; a DATA packet has type byte `03`. The following fields include source/destination IDs, sequence, TTL, battery, RSSI metric, payload length, and CRC32.
5. Save the capture as `mesh_demo.pcapng` and take screenshots of the capture plus the node consoles showing receipt and ACK.

Wireshark may show the payload as raw data because this project does not ship a Wireshark dissector. The loopback simulation's RSSI is a placeholder metric, not a measured radio value.

## What to explain

- Nodes discover paths with route request (RREQ) and route reply (RREP) packets.
- The route score combines hop count, advertised battery level, and the simulated RSSI metric.
- TTL limits repeated forwarding; CRC32 and payload length checks reject corrupted frames.
- Messages wait in a persistent DTN queue during disconnection and are retried after connectivity returns.
- End-to-end ACKs confirm delivery. Audio uses compact ADPCM and is reconstructed as a WAV file at the destination.
- The demo is a software simulation over local UDP. It does not test underground radio coverage or real sensor hardware.

## If something goes wrong

- Confirm each terminal is in the project folder and all three commands use the same ports shown above.
- Keep Node 2 running for the normal route demo; Node 1 and Node 3 are not direct neighbors.
- If you see `Address already in use`, stop the older node using that port or choose a fresh port set and update every peer mapping.
- If a message is queued, check `routes` and `buffer`; allow a few seconds for route discovery after starting a missing relay.
