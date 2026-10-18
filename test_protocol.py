import unittest
import io
import wave
import array
import tempfile

from protocol import HEADER_SIZE, PacketError, PacketType, create_packet, parse_packet
from node import MeshNode
from audio_codec import encode_wav, decode_adpcm


class PacketTests(unittest.TestCase):
    def test_header_is_exactly_16_bytes_and_round_trips(self):
        self.assertEqual(HEADER_SIZE, 16)
        raw = create_packet(PacketType.DATA, 7, 42, 65535, 8, 73, 144, b"distress")
        self.assertEqual(len(raw[:HEADER_SIZE]), 16)
        packet = parse_packet(raw)
        self.assertEqual((packet["src"], packet["dst"], packet["seq"]), (7, 42, 65535))
        self.assertEqual(packet["payload"], b"distress")

    def test_checksum_and_length_are_checked(self):
        raw = bytearray(create_packet(PacketType.DATA, 1, 2, 3, 4, 90, 120, b"SOS"))
        raw[-1] ^= 0x01
        with self.assertRaises(PacketError):
            parse_packet(raw)
        raw = create_packet(PacketType.DATA, 1, 2, 3, 4, 90, 120, b"SOS")[:-1]
        with self.assertRaises(PacketError):
            parse_packet(raw)

    def test_metric_rewards_battery_and_signal(self):
        baseline = MeshNode.path_cost(2, 25, 50)
        better_link = MeshNode.path_cost(2, 90, 200)
        self.assertLess(better_link, baseline)

    def test_short_audio_compresses_and_decodes_as_wav(self):
        samples = array.array("h", [int(12000 * ((i % 40) - 20) / 20) for i in range(8000)])
        source = io.BytesIO()
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(samples.tobytes())
        encoded, rate, duration = encode_wav(source.getvalue())
        self.assertEqual((rate, duration), (8000, 500))
        self.assertLess(len(encoded), len(samples.tobytes()))
        decoded = decode_adpcm(encoded, rate)
        with wave.open(io.BytesIO(decoded), "rb") as wav:
            self.assertEqual(wav.getframerate(), 8000)
            self.assertEqual(wav.getnchannels(), 1)

    def test_dtn_queue_survives_node_restart(self):
        with tempfile.TemporaryDirectory(dir=__import__("pathlib").Path(__file__).parent) as folder:
            queue_path = f"{folder}/node.json"
            node = MeshNode(1, 0, {}, buffer_path=queue_path)
            packet = node._frame(PacketType.DATA, 3, b"saved for later")
            node._queue_packet(3, packet)
            node.sock.close()

            restarted = MeshNode(1, 0, {}, buffer_path=queue_path)
            try:
                self.assertEqual(len(restarted.dtn_buffer), 1)
                self.assertEqual(parse_packet(restarted.dtn_buffer[0]["packet"])["payload"],
                                 b"saved for later")
            finally:
                restarted.sock.close()


if __name__ == "__main__":
    unittest.main()
