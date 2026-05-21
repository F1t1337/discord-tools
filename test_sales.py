import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from modules.sales import SalesManager


def test_create_submission_never_writes_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        manager = SalesManager(
            {
                "enabled": True,
                "mode": "local_queue",
                "provider": "tskupka",
                "outbox_path": str(outbox),
                "allow_external_token_submit": True,
            }
        )

        result = manager.create_submission(
            [
                {
                    "id": 1,
                    "token": "SECRET_DISCORD_TOKEN",
                    "username": "user",
                    "seller_username": "seller",
                    "price": 12.5,
                }
            ],
            source="test",
        )

        assert result["ok"] is True
        assert result["provider"] == "tskupka"
        assert result["accepted_count"] == 1

        raw = outbox.read_text(encoding="utf-8")
        assert "SECRET_DISCORD_TOKEN" not in raw

        submission = json.loads(raw)
        assert submission["external_submit_blocked"] is True
        assert submission["items"][0]["db_id"] == 1
        assert submission["items"][0]["username"] == "user"
        assert "token" not in submission["items"][0]


def test_disabled_sales_does_not_create_outbox():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        manager = SalesManager({"enabled": False, "outbox_path": str(outbox)})

        result = manager.create_submission([{"token": "SECRET"}])

        assert result["ok"] is False
        assert result["accepted_count"] == 0
        assert not outbox.exists()


def test_summary_lists_recent_submissions():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        manager = SalesManager({"enabled": True, "outbox_path": str(outbox)})

        manager.create_submission([{"id": 1}], source="test")
        manager.create_submission([{"id": 2}, {"id": 3}], source="test")

        summary = manager.get_summary(limit=1)

        assert summary["enabled"] is True
        assert summary["external_submit_enabled"] is False
        assert len(summary["recent_submissions"]) == 1
        assert summary["recent_submissions"][0]["accepted_count"] == 2


def test_submission_can_be_sold_once():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        manager = SalesManager({"enabled": True, "outbox_path": str(outbox)})

        result = manager.create_submission([{"id": 1, "price": 10}], source="test")

        assert result["ok"] is True
        assert result["status"] == "PENDING"

        sold = manager.mark_sold(result["submission_id"])

        assert sold["ok"] is True
        assert sold["submission"]["status"] == "SOLD"

        canceled = manager.cancel_submission(result["submission_id"])

        assert canceled["ok"] is False
        assert "already closed" in canceled["error"]

        sold_again = manager.mark_sold(result["submission_id"])

        assert sold_again["ok"] is True
        assert sold_again["submission"]["status"] == "SOLD"


def test_external_submit_sends_db_ids_as_txt_file():
    class FakeResponse:
        status_code = 201
        content = b'{"id": "provider-1"}'

        def json(self):
            return {"id": "provider-1"}

    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        with patch.dict("os.environ", {"TEST_SALES_API_KEY": "api-secret"}):
            manager = SalesManager(
                {
                    "enabled": True,
                    "mode": "external_submit",
                    "provider": "tokenbuyrobot",
                    "outbox_path": str(outbox),
                    "api_key_env": "TEST_SALES_API_KEY",
                    "base_url": "https://example.test",
                    "submit_endpoint": "/submit",
                }
            )

            with patch("modules.sales.requests.post", return_value=FakeResponse()) as post:
                result = manager.create_submission(
                    [{"id": 1, "token": "SECRET_DISCORD_TOKEN", "price": 10}],
                    source="test",
                )

            assert result["ok"] is True
            assert result["status"] == "SUBMITTED"
            assert result["external_submit"]["ok"] is True

            files = post.call_args.kwargs["files"]
            headers = post.call_args.kwargs["headers"]

            assert post.call_args.args[0] == "https://example.test/submit"
            assert headers["X-API-Key"] == "api-secret"
            assert "Content-Type" not in headers
            assert "data" not in post.call_args.kwargs
            assert files["submit"][0].endswith(".txt")
            assert files["submit"][1].decode("utf-8") == "1"
            assert "SECRET_DISCORD_TOKEN" not in files["submit"][1].decode("utf-8")

            submission = manager.get_submission(result["submission_id"])
            assert submission["status"] == "SUBMITTED"
            assert submission["external_submit"]["provider_submission_id"] == "provider-1"
            assert submission["external_submit"]["request_format"] == "multipart_txt_db_id"
            assert submission["external_submit"]["request_line_count"] == 1


def test_price_workflow_runs_provider_sequence():
    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self.payload = payload
            self.status_code = status_code
            self.content = json.dumps(payload).encode("utf-8")

        def json(self):
            return self.payload

    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "submissions.jsonl"
        with patch.dict(
            "os.environ",
            {
                "TSKUPKA_API_KEY": "tsk-key",
                "TOKENBUYROBOT_API_KEY": "tbr-key",
            },
        ):
            manager = SalesManager(
                {
                    "enabled": True,
                    "mode": "price_workflow",
                    "outbox_path": str(outbox),
                    "providers": {
                        "tskupka": {
                            "base_url": "https://tsk.test",
                            "api_key_env": "TSKUPKA_API_KEY",
                        },
                        "tokenbuyrobot": {
                            "base_url": "https://tbr.test",
                            "api_key_env": "TOKENBUYROBOT_API_KEY",
                        },
                    },
                    "workflow": {
                        "price_initial_delay": 0,
                        "price_poll_interval": 0,
                        "completion_poll_interval": 0,
                    },
                }
            )

            result = manager.create_submission([{"id": 1, "price": 10}], source="test")

            post_responses = [
                FakeResponse({"id": "tsk-1"}),
                FakeResponse({"id": "tbr-1"}),
                FakeResponse({"ok": True}),
                FakeResponse({"ok": True}),
            ]
            get_responses = [
                FakeResponse({"status": "priced", "price": 100}),
                FakeResponse({"status": "priced", "price": 120}),
                FakeResponse({"status": "completed"}),
                FakeResponse({"status": "completed"}),
            ]

            with patch("modules.sales.requests.post", side_effect=post_responses) as post, \
                 patch("modules.sales.requests.get", side_effect=get_responses) as get:
                workflow = manager.run_price_workflow(result["submission_id"])

        assert workflow["ok"] is True
        submission = workflow["submission"]
        assert submission["status"] == "COMPLETED"
        assert submission["workflow"]["tskupka_price"] == 100
        assert submission["workflow"]["tokenbuyrobot_price"] == 120
        assert post.call_count == 4
        assert get.call_count == 4
        assert post.call_args_list[0].kwargs["files"]["submit"][1].decode("utf-8") == "1"
        assert post.call_args_list[1].kwargs["files"]["submit"][1].decode("utf-8") == "1"
        assert "json" not in post.call_args_list[0].kwargs
        assert post.call_args_list[2].kwargs["json"]["accepted_price"] == 120
