"""Tool registry.

Top-level imports are lazy to avoid a pre-existing import cycle:
``tools.knowledge_retrieval -> tools.exa_retrieval -> runtime -> agents ->
agents.knowledge_base -> tools.knowledge_retrieval``. Nothing in the codebase
imports these names from the package top level (all consumers import the
submodule directly), so deferred resolution keeps both ``from
competition_app.tools import X`` and ``competition_app.tools.X`` working.
"""

__all__ = ["KnowledgeAssetRepository", "KnowledgeRetrievalTool", "CurrentPageReadTool"]


def __getattr__(name: str):
    if name == "KnowledgeAssetRepository":
        from competition_app.tools.knowledge_assets import KnowledgeAssetRepository

        return KnowledgeAssetRepository
    if name == "KnowledgeRetrievalTool":
        from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool

        return KnowledgeRetrievalTool
    if name == "CurrentPageReadTool":
        from competition_app.tools.current_page import CurrentPageReadTool

        return CurrentPageReadTool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
