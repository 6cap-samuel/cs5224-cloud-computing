import json, os, uuid, boto3
sfn = boto3.client("stepfunctions"); ARN=os.environ.get("STATE_MACHINE_ARN","")
def lambda_handler(event, ctx):
    body = event.get("body")
    try: body = json.loads(body) if isinstance(body, str) else (body or {})
    except: body = {"raw": body}
    body["request_id"] = str(uuid.uuid4())
    if ARN: sfn.start_execution(stateMachineArn=ARN, input=json.dumps(body))
    return {"statusCode": 202, "body": json.dumps({"ok":True})}
