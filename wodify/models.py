"""Typed models returned by the Wodify client."""

from __future__ import annotations

from pydantic import BaseModel, computed_field


class ClassInfo(BaseModel):
    """Class schedule entry returned by the classes endpoint.

    Note: ``program_id`` does NOT distinguish CrossFit from Open Gym — both come
    back under the same gym program id. Use ``name`` as the discriminator.
    ``start_time`` is the reliable local gym clock (e.g. ``"07:00:00"``); the
    ``start`` field is Wodify's ``StartDateTime`` whose ``Z`` suffix is a
    formatting artifact, not real UTC.
    """

    id: int
    name: str
    start: str
    start_time: str = ""
    program_id: int
    available: int = 0
    class_limit: int = 0
    reserved: int = 0
    is_full: bool = False
    is_cancelled: bool = False
    allow_waitlist: bool = False
    waitlisted: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bookable(self) -> bool:
        """True when the class can be reserved right now."""

        return (not self.is_cancelled) and (not self.is_full) and self.available > 0
