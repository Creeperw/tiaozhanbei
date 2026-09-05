import json
from pathlib import Path

from competition_app.tools.video_segment_index import VideoSegmentIndex


def test_release_signature_is_checked_once_per_process(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "video-results"
    result = source / "BV_TEST" / "classification_result.json"
    result.parent.mkdir(parents=True)
    result.write_text(
        json.dumps(
            {
                "bvid": "BV_TEST",
                "video_title": "方剂学",
                "pages": [
                    {
                        "page": 1,
                        "segments": [
                            {
                                "start_seconds": 1,
                                "end_seconds": 10,
                                "transcript": "四君子汤。",
                                "kp_matches": [{"kp_id": "KP_1", "confidence": 0.9}],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    index = VideoSegmentIndex(source, tmp_path / "runtime" / "video.sqlite3")
    calls = 0
    original = index._result_paths

    def counted_result_paths():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(index, "_result_paths", counted_result_paths)

    assert len(index.videos_for_kp("KP_1")) == 1
    assert len(index.videos_for_kp("KP_1")) == 1
    assert calls == 1
