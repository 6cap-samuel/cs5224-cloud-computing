import os, json, hashlib, boto3, time
s3=boto3.client("s3"); BUCKET=os.environ.get("AUDIT_BUCKET","")
def lambda_handler(event, ctx):
    key=f"ddb-stream/{time.strftime('%Y/%m/%d/%H/%M')}.jsonl"
    lines=[]
    for r in event.get("Records",[]):
        img=r.get("dynamodb",{}).get("NewImage",{})
        line=json.dumps(img, separators=(",",":"))
        lines.append(line)
    if BUCKET and lines:
        s3.put_object(Bucket=BUCKET, Key=key, Body=("\n".join(lines)+"\n").encode())
    return {"written":len(lines),"key":key}
