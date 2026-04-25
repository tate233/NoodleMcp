from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class AnalysisSchema(BaseModel):
    content_type: str = Field(default="interview_note")
    is_interview_experience: bool = Field(default=False)
    company: Optional[str] = None
    job_role: Optional[str] = None
    job_direction: Optional[str] = None
    interview_rounds: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    interview_questions: List[str] = Field(default_factory=list)
    question_points: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    difficulty: Optional[str] = None

    @field_validator("interview_rounds", "tags", "interview_questions", "question_points", mode="before")
    @classmethod
    def ensure_list(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            separators = ["\n", "；", ";", "，", ",", "、", "|", "/"]
            items = [text]
            for separator in separators:
                if separator in text:
                    items = [part.strip() for part in text.split(separator) if part.strip()]
                    break
            return items
        return [str(value).strip()]
