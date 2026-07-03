"""Video (frame-sequence) encoder and physical-carrier chunk format."""

import unittest

from balzar.interpreter import render
from balzar.payload import (PayloadError, assemble_chunks, chunk_payload,
                            decode_payload, encode_payload)
from balzar.video import encode_video


def _moving_square_frames(w=64, h=64, n=10):
    """A 8x8 square crossing a flat background: the canonical video case."""
    frames = []
    for k in range(n):
        buf = bytearray([20, 20, 40] * (w * h))
        for y in range(20, 28):
            for x in range(4 + k * 5, 12 + k * 5):
                p = (y * w + x) * 3
                buf[p:p + 3] = bytes((250, 200, 30))
        frames.append(bytes(buf))
    return frames


class TestVideoEncoder(unittest.TestCase):
    def test_lossless_roundtrip_all_frames(self):
        frames = _moving_square_frames()
        result = encode_video(64, 64, frames)
        self.assertTrue(result.lossless)
        rendered = render(decode_payload(result.payload))
        self.assertEqual(len(rendered.frames), len(frames))
        for k in range(len(frames)):
            self.assertEqual(rendered.frame_rgb(k), frames[k],
                             f"frame {k} differs")

    def test_delta_beats_independent_frames(self):
        """The whole point: deltas must crush per-frame independent encoding."""
        from balzar.encoder import encode_image
        frames = _moving_square_frames(n=10)
        video = encode_video(64, 64, frames)
        flipbook = sum(len(encode_image(64, 64, f).payload) for f in frames)
        self.assertLess(len(video.payload), flipbook * 0.5)

    def test_identical_frames_cost_one_instruction_each(self):
        frames = _moving_square_frames(n=1) * 5  # 5 identical frames
        result = encode_video(64, 64, frames)
        rendered = render(result.program_text)
        self.assertEqual(len(rendered.frames), 5)
        # payload must be barely larger than the single-frame encoding
        single = encode_video(64, 64, frames[:1])
        self.assertLess(len(result.payload) - len(single.payload), 40)

    def test_single_frame_video(self):
        frames = _moving_square_frames(n=1)
        result = encode_video(64, 64, frames)
        self.assertEqual(result.frame_count, 1)


class TestChunks(unittest.TestCase):
    def _payload(self, size_hint=10000):
        # build a payload comfortably larger than one chunk
        prog = "CANVAS w=64 h=64 bg=0\n" + "\n".join(
            f"SETPIX x={i % 64} y={i // 64 % 64} color={i % 7}"
            for i in range(size_hint // 20)
        )
        return encode_payload(prog)

    def test_roundtrip_in_order(self):
        payload = self._payload()
        chunks = chunk_payload(payload, chunk_size=500)
        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 500)
        self.assertEqual(assemble_chunks(chunks), payload)

    def test_roundtrip_shuffled(self):
        payload = self._payload()
        chunks = chunk_payload(payload, chunk_size=500)
        shuffled = chunks[::-1]
        self.assertEqual(assemble_chunks(shuffled), payload)

    def test_missing_chunk_detected(self):
        chunks = chunk_payload(self._payload(), chunk_size=500)
        with self.assertRaises(PayloadError):
            assemble_chunks(chunks[:-1])

    def test_corrupt_chunk_detected(self):
        chunks = chunk_payload(self._payload(), chunk_size=500)
        bad = bytearray(chunks[1])
        bad[-1] ^= 0xFF
        chunks[1] = bytes(bad)
        with self.assertRaises(PayloadError):
            assemble_chunks(chunks)

    def test_small_payload_single_chunk(self):
        payload = encode_payload("CANVAS w=8 h=8 bg=0")
        chunks = chunk_payload(payload)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(assemble_chunks(chunks), payload)


if __name__ == "__main__":
    unittest.main()
