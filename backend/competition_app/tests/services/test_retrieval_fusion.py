from competition_app.services.retrieval_fusion import reciprocal_rank_fusion


def test_rrf_rewards_cross_channel_consensus_over_bridge_only_hit() -> None:
    result = reciprocal_rank_fusion(
        {
            "bridge": [("BRIDGE_ONLY", 1.0), ("CONSENSUS", 1.0)],
            "bm25": [("CONSENSUS", 0.7)],
            "vector": [("CONSENSUS", 0.8)],
        }
    )

    assert result["CONSENSUS"].score > result["BRIDGE_ONLY"].score
    assert result["CONSENSUS"].channel_ranks == {
        "bridge": 2,
        "bm25": 1,
        "vector": 1,
    }
    assert result["BRIDGE_ONLY"].legacy_max_score == 1.0


def test_rrf_deduplicates_channel_rows_without_rank_gaps() -> None:
    result = reciprocal_rank_fusion(
        {"bm25": [("Q1", 1.0), ("Q1", 0.8), ("Q2", 0.7)]}
    )

    assert result["Q1"].channel_ranks["bm25"] == 1
    assert result["Q2"].channel_ranks["bm25"] == 2
