"""balzar — deterministic content generation from minimal descriptions.

A content (image, frame sequence) is not stored: it is regenerated
deterministically from a compact payload (seed + rule program), following
the program-based generation model described in the project README.
"""

__version__ = "0.1.0"

from .interpreter import Interpreter, RenderResult
from .payload import encode_payload, decode_payload

__all__ = ["Interpreter", "RenderResult", "encode_payload", "decode_payload"]
