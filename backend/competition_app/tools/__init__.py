from competition_app.tools.knowledge_assets import KnowledgeAssetRepository

__all__ = ["KnowledgeAssetRepository", "KnowledgeRetrievalTool"]


def __getattr__(name: str):
    if name == "KnowledgeRetrievalTool":
        from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool

        return KnowledgeRetrievalTool
    raise AttributeError(name)
