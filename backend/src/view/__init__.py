"""The view layer — code owns the frame, the model writes one markdown body.

External text is quoted by code and enters neither the frame nor the body.
"""

from src.view.contracts import Affordance, Frame, FrameKind, FrameStatus, Quote, Unit

__all__ = ["Affordance", "Frame", "FrameKind", "FrameStatus", "Quote", "Unit"]
