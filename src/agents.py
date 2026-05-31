"""ADK agents.

Phase 0: ProductionAgent — a deterministic custom agent that wraps the render
pipeline so the LLM never "chooses" render steps (safety for a publishing system).
Later phases add DirectorAgent (LlmAgent), SummarizerAgent, FeedbackClassifierAgent.
"""
from __future__ import annotations

import asyncio

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

from . import pipeline
from .topic_picker import Topic
from .style import StyleProfile


class ProductionAgent(BaseAgent):
    """Reads topic/style/context from session.state, renders a video (off-thread
    so the event loop isn't blocked), writes video_id back to state."""

    async def _run_async_impl(self, ctx):
        st = ctx.session.state
        t = st["topic"]
        topic = Topic(t["topic"], t["angle"], t["wikipedia_title"])
        style = StyleProfile.from_dict(st.get("style"))

        video_id = await asyncio.to_thread(
            pipeline.generate, topic,
            style=style,
            story_so_far=st.get("story_so_far", ""),
            feedback=st.get("feedback", ""),
            iso_now=st["iso_now"],
            video_id=st.get("video_id"),
            series_id=st.get("series_id"),
            part_no=st.get("part_no"),
            reuse_images=st.get("reuse_images", False),
        )
        yield Event(author=self.name,
                    actions=EventActions(state_delta={"video_id": video_id}))
