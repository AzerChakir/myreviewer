"""One-shot PR question answering for slash commands (e.g. `/ask`).

A reviewer can ask a question about a PR and get a grounded answer from the
diff, using the same configured LLM as the analysis pipeline. Kept deliberately
small: no multi-stage pipeline, just a single chat completion with the
structured `ASK_USER` prompt.
"""

from __future__ import annotations

from pr_decomposer.config import ConfigError
from pr_decomposer.llm_client import MockLLMClient, NitroClient
from pr_decomposer.prompts import ASK_SYSTEM, ASK_USER


def answer_question(diff, question: str, *, config, mock: bool = False) -> str:
    """Answer `question` about `diff` using the configured LLM."""
    question = question.strip()
    if not question:
        raise ValueError("empty question")
    if mock:
        llm = MockLLMClient()
    else:
        if not config.has_api_key:
            raise ConfigError(
                "No NIM_API_KEY found — set NIM_API_KEY in prototype/.env to "
                "answer questions. --mock is a dev/test option only."
            )
        llm = NitroClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
    user = ASK_USER.format(
        title=diff.title or f"{diff.base or ''} -> {diff.head or ''}".strip(" ->"),
        description=diff.description or "",
        commit_messages="\n".join(diff.commit_messages) or "(none)",
        diff=diff.to_compact(),
        question=question,
    )
    return llm.complete(ASK_SYSTEM, user).strip() or "*(empty answer — retry?)*"