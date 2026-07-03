"""Deterministic pseudo-random number generator.

Pure-integer xorshift64* seeded through splitmix64: the sequence depends
only on the seed and on the order of the calls, so it is bit-identical on
every platform and Python version. Never use random.Random here — its
algorithms are not part of the format contract.
"""

_MASK64 = (1 << 64) - 1


def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


class DetRNG:
    """xorshift64* generator with a 64-bit state."""

    def __init__(self, seed: int) -> None:
        state = _splitmix64(seed & _MASK64)
        # xorshift must never hold a zero state
        self._state = state if state != 0 else 0x1234567887654321

    def next_u64(self) -> int:
        x = self._state
        x ^= (x >> 12)
        x &= _MASK64
        x ^= (x << 25) & _MASK64
        x ^= (x >> 27)
        self._state = x
        return (x * 0x2545F4914F6CDD1D) & _MASK64

    def next_float(self) -> float:
        """Uniform float in [0, 1) with 53 bits of entropy."""
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def randint(self, n: int) -> int:
        """Integer in [0, n). Modulo bias is irrelevant for pixel use."""
        if n <= 0:
            raise ValueError("randint requires n > 0")
        return self.next_u64() % n
