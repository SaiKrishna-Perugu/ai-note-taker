"""Validated AI responses. All times are seconds relative to an audio chunk."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    speaker: str
    text: str

    @model_validator(mode="after")
    def check_order(self) -> "Segment":
        if self.end < self.start:
            raise ValueError("Segment end precedes start")
        return self


class Transcript(BaseModel):
    segments: list[Segment]


class ActionItem(BaseModel):
    task: str
    owner: str | None
    due_date: str | None


class MeetingNotes(BaseModel):
    title: str
    summary: str
    key_points: list[str]
    decisions: list[str]
    action_items: list[ActionItem]
    open_questions: list[str]
