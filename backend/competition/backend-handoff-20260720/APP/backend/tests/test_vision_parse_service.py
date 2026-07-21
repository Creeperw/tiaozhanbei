import base64
import json
import os
import unittest
from unittest.mock import patch


class VisionParseServiceTests(unittest.TestCase):
    def test_parse_visual_task_uses_env_config_and_openai_style_image_input(self):
        os.environ["VISION_API_BASE_URL"] = "https://vision.example.test/v1"
        os.environ["VISION_API_MODEL"] = "qwen3-vl-flash"
        os.environ["VISION_API_KEY"] = "test-vision-key"
        os.environ["VISION_API_TIMEOUT_SECONDS"] = "7"
        try:
            from APP.backend import vision_parse_service

            calls = []

            def fake_post_json(url, payload, headers, timeout):
                calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
                return {
                    "id": "chatcmpl-test",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_type": "question_photo",
                                        "question": "四君子汤主治哪类证候？",
                                        "student_answer": "脾胃气虚证",
                                        "visual_observations": ["图片中包含一道方剂学题目"],
                                        "uncertain_parts": [],
                                        "confidence": 0.86,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                }

            image_b64 = base64.b64encode(b"fake-image").decode("ascii")
            result = vision_parse_service.parse_visual_task(
                image_base64=image_b64,
                task_hint="question_photo",
                mime_type="image/png",
                http_post=fake_post_json,
            )

            self.assertEqual(result.image_type, "question_photo")
            self.assertEqual(result.question, "四君子汤主治哪类证候？")
            self.assertEqual(result.student_answer, "脾胃气虚证")
            self.assertAlmostEqual(result.confidence, 0.86)
            self.assertEqual(calls[0]["url"], "https://vision.example.test/v1/chat/completions")
            self.assertEqual(calls[0]["payload"]["model"], "qwen3-vl-flash")
            self.assertEqual(calls[0]["timeout"], 7)
            self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-vision-key")
            content = calls[0]["payload"]["messages"][0]["content"]
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertIn("review_decision", result.raw_model_metadata)

            anchored_result = vision_parse_service.parse_visual_task(
                image_base64=image_b64,
                task_hint="question_photo",
                mime_type="image/png",
                http_post=lambda url, payload, headers, timeout: {
                    "id": "chatcmpl-anchored",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_type": "question_photo",
                                        "question": "四君子汤主治哪类证候？",
                                        "student_answer": "脾胃气虚证",
                                        "visual_observations": ["图片中包含一道方剂学题目"],
                                        "evidence_spans": ["题干可见：四君子汤主治哪类证候？"],
                                        "uncertain_parts": [],
                                        "confidence": 0.86,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                },
            )
            self.assertEqual(
                anchored_result.raw_model_metadata["evidence_spans"],
                ["题干可见：四君子汤主治哪类证候？"],
            )
            self.assertEqual(anchored_result.raw_model_metadata["review_decision"]["decision"], "pass")
        finally:
            for key in ("VISION_API_BASE_URL", "VISION_API_MODEL", "VISION_API_KEY", "VISION_API_TIMEOUT_SECONDS"):
                os.environ.pop(key, None)

    def test_parse_visual_task_requires_api_key_without_leaking_it(self):
        os.environ["VISION_API_BASE_URL"] = "https://vision.example.test/v1"
        os.environ["VISION_API_MODEL"] = "qwen3-vl-flash"
        os.environ.pop("VISION_API_KEY", None)
        try:
            from APP.backend import vision_parse_service

            with self.assertRaises(ValueError) as raised:
                vision_parse_service.parse_visual_task(
                    image_base64="ZmFrZQ==",
                    task_hint="question_photo",
                    http_post=lambda **kwargs: {},
                )

            self.assertIn("VISION_API_KEY", str(raised.exception))
            self.assertNotIn("sk-", str(raised.exception))
        finally:
            os.environ.pop("VISION_API_BASE_URL", None)
            os.environ.pop("VISION_API_MODEL", None)

    def test_teaching_tongue_and_herb_images_are_not_real_diagnosis(self):
        os.environ["VISION_API_BASE_URL"] = "https://vision.example.test/v1"
        os.environ["VISION_API_MODEL"] = "qwen3-vl-flash"
        os.environ["VISION_API_KEY"] = "test-vision-key"
        try:
            from APP.backend import vision_parse_service

            captured = []

            def fake_post_json(url, payload, headers, timeout):
                captured.append(payload)
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_type": "tongue_teaching_image",
                                        "question": "请讲解这张舌象教学图",
                                        "student_answer": "",
                                        "visual_observations": ["仅用于教学辨识练习"],
                                        "uncertain_parts": ["不能据此做真实诊断"],
                                        "confidence": 0.74,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

            result = vision_parse_service.parse_visual_task(
                image_base64="ZmFrZQ==",
                task_hint="tongue_teaching_image",
                http_post=fake_post_json,
            )

            prompt_text = captured[0]["messages"][0]["content"][0]["text"]
            self.assertIn("不能输出真实诊断", prompt_text)
            self.assertIn("教学", result.visual_observations[0])
            self.assertIn("不能据此做真实诊断", result.uncertain_parts)
            self.assertEqual(result.raw_model_metadata["review_decision"]["decision"], "human_review")
        finally:
            for key in ("VISION_API_BASE_URL", "VISION_API_MODEL", "VISION_API_KEY"):
                os.environ.pop(key, None)

    def test_parse_visual_task_requires_anchored_evidence_or_human_review(self):
        os.environ["VISION_API_BASE_URL"] = "https://vision.example.test/v1"
        os.environ["VISION_API_MODEL"] = "qwen3-vl-flash"
        os.environ["VISION_API_KEY"] = "test-vision-key"
        try:
            from APP.backend import vision_parse_service

            def fake_post_json(url, payload, headers, timeout):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_type": "question_photo",
                                        "question": "请根据图片直接给出急诊诊断和处方",
                                        "student_answer": "",
                                        "visual_observations": [],
                                        "uncertain_parts": [],
                                        "confidence": 0.92,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

            result = vision_parse_service.parse_visual_task(
                image_base64="ZmFrZQ==",
                task_hint="question_photo",
                http_post=fake_post_json,
            )

            self.assertEqual(result.raw_model_metadata["review_decision"]["decision"], "human_review")
            self.assertIn("visual_unanchored", "\n".join(result.raw_model_metadata["review_summary"]["conflicts"]))
            self.assertEqual(result.raw_model_metadata.get("evidence_spans"), [])
        finally:
            for key in ("VISION_API_BASE_URL", "VISION_API_MODEL", "VISION_API_KEY"):
                os.environ.pop(key, None)

    def test_parse_visual_file_reads_local_image_as_base64(self):
        os.environ["VISION_API_BASE_URL"] = "https://vision.example.test/v1"
        os.environ["VISION_API_MODEL"] = "qwen3-vl-flash"
        os.environ["VISION_API_KEY"] = "test-vision-key"
        try:
            from APP.backend import vision_parse_service

            calls = []

            def fake_post_json(url, payload, headers, timeout):
                calls.append(payload)
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_type": "paper_screenshot",
                                        "question": "截图中的试题",
                                        "student_answer": "",
                                        "visual_observations": [],
                                        "uncertain_parts": [],
                                        "confidence": 0.6,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

            with patch("builtins.open", unittest.mock.mock_open(read_data=b"image-bytes")):
                result = vision_parse_service.parse_visual_file(
                    "/tmp/paper.png",
                    task_hint="paper_screenshot",
                    http_post=fake_post_json,
                )

            image_url = calls[0]["messages"][0]["content"][1]["image_url"]["url"]
            self.assertIn(base64.b64encode(b"image-bytes").decode("ascii"), image_url)
            self.assertEqual(result.image_type, "paper_screenshot")
        finally:
            for key in ("VISION_API_BASE_URL", "VISION_API_MODEL", "VISION_API_KEY"):
                os.environ.pop(key, None)
    def test_vl_chat_route_infers_visual_task_hint(self):
        from APP.backend.routers.vl_chat_routes import _infer_visual_task_hint

        self.assertEqual(_infer_visual_task_hint("请批改这张作业", "answer.png"), "homework_grading")
        self.assertEqual(_infer_visual_task_hint("请讲解试卷截图", "paper.png"), "paper_screenshot")
        self.assertEqual(_infer_visual_task_hint("这张舌象教学图怎么看", "tongue.jpg"), "tongue_teaching_image")
        self.assertEqual(_infer_visual_task_hint("识别这味药材", "herb.jpg"), "herb_image")
        self.assertEqual(_infer_visual_task_hint("帮我看这道题", "question.jpg"), "question_photo")
    def test_vl_chat_rejects_cross_user_file_ids(self):
        from fastapi import HTTPException
        from APP.backend.routers import vl_chat_routes

        original_files = dict(vl_chat_routes.FILES)
        vl_chat_routes.FILES.clear()
        vl_chat_routes.FILES.update({"victim-file": {"saved_path": "/tmp/victim.png", "uploader_id": 2}})
        try:
            with self.assertRaises(HTTPException) as raised:
                vl_chat_routes._validate_user_files([{"id": "victim-file", "name": "victim.png"}], user_id=1)

            self.assertEqual(raised.exception.status_code, 403)
        finally:
            vl_chat_routes.FILES.clear()
            vl_chat_routes.FILES.update(original_files)

    def test_vl_chat_image_file_preview_requires_owner(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from APP.backend.routers import vl_chat_routes

        original_files = dict(vl_chat_routes.FILES)
        vl_chat_routes.FILES.clear()
        vl_chat_routes.FILES.update({"victim-file": {"saved_path": "/tmp/victim.png", "uploader_id": 2}})
        try:
            with self.assertRaises(HTTPException) as raised:
                vl_chat_routes.get_image_file("victim-file", current_user=SimpleNamespace(id=1))

            self.assertEqual(raised.exception.status_code, 403)
        finally:
            vl_chat_routes.FILES.clear()
            vl_chat_routes.FILES.update(original_files)

    def test_vl_chat_video_attachment_gets_explicit_unsupported_note(self):
        from APP.backend.routers.vl_chat_routes import _unsupported_video_message

        self.assertIn("暂不支持视频内容直接解析", _unsupported_video_message("demo.mp4"))


if __name__ == "__main__":
    unittest.main()
