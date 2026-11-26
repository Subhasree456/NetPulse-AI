"""AI Network Copilot endpoint (database-grounded, no fabricated answers)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AnalystUser, CurrentUser, DbSession
from app.intelligence.copilot import answer_question
from app.intelligence.threat_intel import threat_intel_status

router = APIRouter(prefix="/copilot", tags=["AI Copilot"])


class CopilotQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=500)


SUGGESTIONS = [
    "Give me the current operational overview.",
    "Which host is at highest risk and why?",
    "How is network latency trending in the last 24 hours?",
    "What security findings need attention?",
    "What should I fix first?",
    "Which monitoring agents have not reported recently?",
    "Summarise the newest root-cause investigations.",
    "Are there optimisation opportunities from measured data?",
]


@router.get("/suggestions")
def suggestions(_: CurrentUser) -> dict:
    return {
        "suggestions": SUGGESTIONS,
        "method": "Deterministic, database-grounded responder",
        "limitations": [
            "Answers use only telemetry stored by this deployment.",
            "No external language model is called unless an operator configures one.",
        ],
    }


@router.post("/ask")
def ask(payload: CopilotQuestion, db: DbSession, _: CurrentUser) -> dict:
    try:
        return answer_question(db, payload.question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/threat-intel")
def threat_intel(db: DbSession, _: AnalystUser) -> dict:
    return threat_intel_status(db)
