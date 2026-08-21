"""The view layer — code owns the frame, the model writes one markdown body.

See docs/view-layer/spec.md. Appearance is normative in docs/view-layer/mockup.html.
"""

from src.view.contracts import Affordance, Frame, FrameKind, FrameStatus, Quote, Unit

__all__ = ["Affordance", "Frame", "FrameKind", "FrameStatus", "Quote", "Unit"]
