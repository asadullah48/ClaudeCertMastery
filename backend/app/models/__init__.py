"""SQLAlchemy ORM models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogenerate and `Base.metadata.create_all()` both rely on. Import the package, not the
individual modules, or migrations will silently miss tables.
"""

from app.models.attempt import (
    AttemptDomainScore,
    AttemptItem,
    AttemptMode,
    AttemptStatus,
    ExamAttempt,
)
from app.models.catalog import AnswerOption, Domain, Question, QuestionType, Track
from app.models.explanation import Explanation
from app.models.flashcard import Flashcard
from app.models.user import User

__all__ = [
    "AnswerOption",
    "AttemptDomainScore",
    "AttemptItem",
    "AttemptMode",
    "AttemptStatus",
    "Domain",
    "ExamAttempt",
    "Explanation",
    "Flashcard",
    "Question",
    "QuestionType",
    "Track",
    "User",
]
