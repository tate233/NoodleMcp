from typing import List, Optional

from pydantic import BaseModel, Field


class AnalysisSchema(BaseModel):
    is_interview_experience: bool = Field(default=False)
    company: Optional[str] = None
    job_role: Optional[str] = None
    job_direction: Optional[str] = None
    interview_rounds: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    question_points: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    difficulty: Optional[str] = None
