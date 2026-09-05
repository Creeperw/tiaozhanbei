from competition_app.contracts.knowledge import EvidencePack


def test_legacy_evidence_pack_json_defaults_to_no_separate_focus_request() -> None:
    pack = EvidencePack.model_validate_json(
        '{"evidence_pack_id":"EP_LEGACY","query":"四君子汤",'
        '"evidence_items":[],"retrieval_summary":"历史证据摘要"}'
    )

    assert pack.learning_focus_status == "not_requested"
    assert pack.learning_focus_items == []
