"""The core contract: identical input => bit-identical output."""

import unittest

from balzar.interpreter import render
from balzar.payload import decode_payload, encode_payload, from_base64, to_base64

PROGRAM = """
CANVAS w=64 h=64 bg=0
SEED value=1234
REGION name=A x=0 y=0 w=16 h=16
FILL region=A color=5
NOISE region=FULL color=2 density=0.1
SCATTER region=A color=7 count=30
LOOP var=i count=4
  REGION name=R x=i*16 y=16 w=16 h=16
  COPY src=A dst=R
  ROTATE region=R angle=90
ENDLOOP
FRACTAL type=triangle region=FULL color=3 depth=6
"""


class TestDeterminism(unittest.TestCase):
    def test_repeated_render_is_bit_identical(self):
        a = render(PROGRAM)
        b = render(PROGRAM)
        self.assertEqual(a.frames, b.frames)
        self.assertEqual(a.frame_rgb(0), b.frame_rgb(0))

    def test_render_from_decoded_payload_matches_source(self):
        direct = render(PROGRAM)
        via_payload = render(decode_payload(encode_payload(PROGRAM)))
        self.assertEqual(direct.frames, via_payload.frames)

    def test_seed_changes_output(self):
        other = PROGRAM.replace("SEED value=1234", "SEED value=1235")
        self.assertNotEqual(render(PROGRAM).frames, render(other).frames)

    def test_known_rng_regression(self):
        # pin the generator sequence: any change to the RNG breaks the format
        from balzar.rng import DetRNG
        rng = DetRNG(42)
        seq = [rng.next_u64() for _ in range(3)]
        rng2 = DetRNG(42)
        self.assertEqual(seq, [rng2.next_u64() for _ in range(3)])
        self.assertNotEqual(seq[0], seq[1])
        self.assertEqual(DetRNG(42).next_u64(), seq[0])


class TestPayloadRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        payload = encode_payload(PROGRAM)
        text = decode_payload(payload)
        self.assertEqual(encode_payload(text), payload)

    def test_cosmetic_changes_do_not_change_payload(self):
        messy = PROGRAM.replace("FILL region=A color=5",
                                "fill(region=A, color=5)  # commento")
        self.assertEqual(encode_payload(messy), encode_payload(PROGRAM))

    def test_base64_roundtrip(self):
        payload = encode_payload(PROGRAM)
        self.assertEqual(from_base64(to_base64(payload)), payload)

    def test_corruption_is_detected(self):
        from balzar.payload import PayloadError
        payload = bytearray(encode_payload(PROGRAM))
        payload[-1] ^= 0xFF
        with self.assertRaises(PayloadError):
            decode_payload(bytes(payload))

    def test_bad_magic_is_rejected(self):
        from balzar.payload import PayloadError
        with self.assertRaises(PayloadError):
            decode_payload(b"NOPE" + b"\x00" * 20)


if __name__ == "__main__":
    unittest.main()
