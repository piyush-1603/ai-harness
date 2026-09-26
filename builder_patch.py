import copy
from typing import Optional, Union, Any

from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    TaskState,
    TokenUsage,
    VerificationResult,
)
from src.context.budget import estimate_tokens
from src.context.relevance import ContextCandidate, RelevanceScorer
from src.context.packing import ContextPacker

# copy original formatting functions here for brevity or just import?
# I'll just rewrite the whole ContextBuilder class since it's cleaner.
