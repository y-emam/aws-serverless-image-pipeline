import json

import boto3
import pytest

from conftest import REGION, SOURCE, TABLE, load_function, s3_event

DEFINITION = json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Succeed"}}})


@pytest.fixture
def consumer(monkeypatch, aws):
    sfn = boto3.client("stepfunctions", region_name=REGION)
    arn = sfn.create_state_machine(
        name="pipeline", definition=DEFINITION, roleArn="arn:aws:iam::123456789012:role/sfn"
    )["stateMachineArn"]
    monkeypatch.setenv("STATE_MACHINE_ARN", arn)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("UPLOAD_PREFIX", "uploads/")
    return load_function("start_workflow"), sfn, arn


def sqs_event(*bodies):
    return {"Records": [{"messageId": f"m{i}", "body": b if isinstance(b, str) else json.dumps(b)}
                        for i, b in enumerate(bodies)]}


def with_sequencer(event, seq):
    event["Records"][0]["s3"]["object"]["sequencer"] = seq
    return event


def executions(sfn, arn):
    return sfn.list_executions(stateMachineArn=arn)["executions"]


def test_starts_one_execution_and_writes_received(consumer, aws):
    fn, sfn, arn = consumer
    out = fn.handler(sqs_event(with_sequencer(s3_event(SOURCE, "uploads/abc.jpg"), "0055AED6DCD90281E5")), None)

    assert out == {"batchItemFailures": []}
    runs = executions(sfn, arn)
    assert len(runs) == 1 and runs[0]["name"] == "abc-0055AED6DCD90281E5"
    run_input = json.loads(sfn.describe_execution(executionArn=runs[0]["executionArn"])["input"])
    assert run_input == {"image_id": "abc", "bucket": SOURCE, "key": "uploads/abc.jpg", "uploaded_at": "2026-11-15T10:00:00.000Z"}
    item = aws["table"].get_item(Key={"image_id": "abc"})["Item"]
    assert item["status"] == "RECEIVED"


def test_duplicate_event_starts_only_one_execution(consumer):
    fn, sfn, arn = consumer
    body = with_sequencer(s3_event(SOURCE, "uploads/dup.png"), "AAA111")
    assert fn.handler(sqs_event(body), None) == {"batchItemFailures": []}
    assert fn.handler(sqs_event(body), None) == {"batchItemFailures": []}
    assert len(executions(sfn, arn)) == 1


def test_reupload_of_same_key_gets_new_execution(consumer):
    fn, sfn, arn = consumer
    fn.handler(sqs_event(with_sequencer(s3_event(SOURCE, "uploads/same.jpg"), "AAA1")), None)
    fn.handler(sqs_event(with_sequencer(s3_event(SOURCE, "uploads/same.jpg"), "BBB2")), None)
    assert len(executions(sfn, arn)) == 2


def test_received_row_never_overwrites_final_status(consumer, aws):
    fn, _, _ = consumer
    aws["table"].put_item(Item={"image_id": "done", "status": "COMPLETED"})
    fn.handler(sqs_event(with_sequencer(s3_event(SOURCE, "uploads/done.jpg"), "C3")), None)
    assert aws["table"].get_item(Key={"image_id": "done"})["Item"]["status"] == "COMPLETED"


@pytest.mark.parametrize("body", [
    {"Service": "Amazon S3", "Event": "s3:TestEvent", "Bucket": SOURCE},
    s3_event(SOURCE, "uploads/readme.txt"),
    s3_event(SOURCE, "other/photo.jpg"),
    s3_event(SOURCE, "uploads/x.jpg", event_name="ObjectRemoved:Delete"),
], ids=["test-event", "non-image", "outside-prefix", "not-created"])
def test_skips_noise_without_failing(consumer, body):
    fn, sfn, arn = consumer
    assert fn.handler(sqs_event(body), None) == {"batchItemFailures": []}
    assert executions(sfn, arn) == []


def test_url_encoded_key(consumer):
    fn, sfn, arn = consumer
    fn.handler(sqs_event(with_sequencer(s3_event(SOURCE, "uploads/my+photo.jpg"), "D4")), None)
    run = executions(sfn, arn)[0]
    assert run["name"] == "my-photo-D4"
    assert json.loads(sfn.describe_execution(executionArn=run["executionArn"])["input"])["key"] == "uploads/my photo.jpg"


def test_partial_batch_failure_reports_only_bad_message(consumer):
    fn, sfn, arn = consumer
    good = with_sequencer(s3_event(SOURCE, "uploads/good.jpg"), "E5")
    out = fn.handler(sqs_event("this is not json", good), None)
    assert out == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
    assert len(executions(sfn, arn)) == 1


def test_execution_name_is_valid_for_long_ids(consumer):
    fn, _, _ = consumer
    name = fn.execution_name("a" * 64, {"s3": {"object": {"key": "k", "sequencer": "F" * 30}}})
    assert len(name) <= 80


def test_image_id_rule_matches_validate_step():
    validate = load_function("validate")
    consumer = load_function("start_workflow")
    for key in ["uploads/abc.jpg", "uploads/my photo.jpeg", "uploads/x.y.png", "uploads/.webp"]:
        assert consumer.image_id_from_key(key) == validate.image_id_from_key(key)


def test_execution_name_uses_only_allowed_characters(consumer):
    import re

    fn, _, _ = consumer
    record = {"s3": {"object": {"key": "uploads/a b+c.jpg"}}, "eventTime": "2026-11-15T10:00:00Z"}
    name = fn.execution_name(fn.image_id_from_key("uploads/a b+c.jpg"), record)  # no sequencer: hash fallback
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name)
