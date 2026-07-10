"""Base class for one DIS pipeline agent/node.

Each pipeline step is implemented as a separate agent class. LangGraph uses
these agents as graph nodes. The agent reads/writes the shared PipelineState.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import logging
from services.pipeline.common import PipelineContext, PipelineState

log = logging.getLogger(__name__)

class BasePipelineAgent(ABC):
    step_name: str = "base"
    purpose: str = ""

    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx

    def __call__(self, state: PipelineState) -> PipelineState:
        return self.run(state)

    @abstractmethod
    def run(self, state: PipelineState) -> PipelineState:
        raise NotImplementedError

    def done(self, state: PipelineState) -> PipelineState:
        return self.ctx.step_done(state, self.step_name)
