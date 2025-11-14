#!/usr/bin/env python3
"""
VapeWatch - AWS Deployment Script (Simplified & Clean)
Deploy YOLOv8 model to SageMaker using managed PyTorch container
"""

import boto3
import sagemaker
from sagemaker.pytorch import PyTorchModel
import json
import base64
from pathlib import Path
import sys

# =========================
# CONFIGURATION
# =========================
REGION = "ap-southeast-1"
ROLE_ARN = "arn:aws:iam::156999051350:role/SageMakerExecutionRole"

# Path to model weights
MODEL_DATA = "s3://vapewatch-inference/model.tar.gz"



# Endpoint/model names
ENDPOINT_NAME = "vapewatch-endpoint"
MODEL_NAME = "vapewatch-model"

# Local source directory
SOURCE_DIR = "inference_model"

# =========================
# DEPLOY MODEL
# =========================
def deploy_to_sagemaker():
    print("🚀 Deploying VapeWatch YOLO model to SageMaker...")
    print("=" * 60)

    # Initialize session
    session = sagemaker.Session()
    print(f"📦 Model artifact: {MODEL_DATA}")
    print(f"🌐 Endpoint: {ENDPOINT_NAME}")
    print(f"🔑 Role: {ROLE_ARN}")

    # Create PyTorch model using managed container
    model = PyTorchModel(
        model_data=MODEL_DATA,
        role=ROLE_ARN,
        entry_point="inference.py",  # inside inference_model/
        source_dir=SOURCE_DIR,
        framework_version="2.0.0",
        py_version="py310",
        sagemaker_session=session,
    )

    # Deploy endpoint
    try:
        predictor = model.deploy(
            initial_instance_count=1,
            instance_type="ml.m5.large",
            endpoint_name=ENDPOINT_NAME,
            wait=True,
        )
        print(f"\n✅ Deployment successful!")
        print(f"🌐 Endpoint Name: {ENDPOINT_NAME}")
        print(f"🔗 Model ARN: {predictor.endpoint_name}")
    except Exception as e:
        print(f"\n❌ Deployment failed: {e}")

# =========================
# TEST ENDPOINT
# =========================
def test_endpoint():
    print("\n🧪 Testing deployed endpoint...")
    client = boto3.client("sagemaker-runtime", region_name=REGION)

    test_image = r"D:\NUS MCOMP\cloud computing\cloud_computing\Project\VapeDataSet\test\-_136_jpg.rf.4ceaffe5c31965c75a7ec6f13b0d45c3.jpg"
    if not Path(test_image).exists():
        print(f"⚠️ Test image not found: {test_image}")
        return

    # Encode image
    with open(test_image, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {"image": image_b64, "confidence_threshold": 0.5}

    try:
        response = client.invoke_endpoint(
            EndpointName=ENDPOINT_NAME,
            ContentType="application/json",
            Body=json.dumps(payload),
        )
        result = json.loads(response["Body"].read().decode("utf-8"))

        print("✅ Inference successful:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"❌ Endpoint test failed: {e}")

# =========================
# LIST ENDPOINTS
# =========================
def list_endpoints():
    print("\n📋 Listing SageMaker endpoints...")
    client = boto3.client("sagemaker", region_name=REGION)
    try:
        endpoints = client.list_endpoints()["Endpoints"]
        if not endpoints:
            print("No active endpoints found.")
        else:
            for e in endpoints:
                print(f"  • {e['EndpointName']} ({e['EndpointStatus']})")
    except Exception as e:
        print(f"❌ Failed to list endpoints: {e}")

# =========================
# DELETE ENDPOINT
# =========================
def delete_endpoint():
    print("\n🗑️ Deleting endpoint...")
    client = boto3.client("sagemaker", region_name=REGION)
    try:
        client.delete_endpoint(EndpointName=ENDPOINT_NAME)
        print(f"✅ Endpoint '{ENDPOINT_NAME}' deleted successfully.")
    except Exception as e:
        print(f"❌ Failed to delete endpoint: {e}")

# =========================
# MAIN ENTRY POINT
# =========================
def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python deploy.py deploy    - Deploy model to SageMaker")
        print("  python deploy.py test      - Test deployed endpoint")
        print("  python deploy.py list      - List available endpoints")
        print("  python deploy.py delete    - Delete deployed endpoint")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "deploy":
        deploy_to_sagemaker()
    elif action == "test":
        test_endpoint()
    elif action == "list":
        list_endpoints()
    elif action == "delete":
        delete_endpoint()
    else:
        print(f"❌ Unknown command: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
