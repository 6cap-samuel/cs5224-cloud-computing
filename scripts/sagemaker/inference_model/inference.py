#!/usr/bin/env python3
"""
VapeWatch SageMaker Inference Script
Handles model loading and inference for SageMaker endpoint
Supports both pretrained (yolov8n.pt) and trained models (best.pt)
"""

import os
import json
import base64
import io
import logging
from pathlib import Path

import torch
import numpy as np
from PIL import Image
from ultralytics import YOLO

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variable to store the model
model = None


def model_fn(model_dir):
    """
    Load the model from the model directory.
    SageMaker will call this function when the endpoint starts.
    
    Args:
        model_dir: Path to the model directory (typically /opt/ml/model/)
        
    Returns:
        Loaded YOLO model
    """
    global model
    
    logger.info(f"Loading model from: {model_dir}")
    
    # SageMaker model directory
    model_dir = Path(model_dir)
    
    # Try to find the best trained model first, then fall back to pretrained
    best_model_path = model_dir / "best.pt"
    pretrained_model_path = model_dir / "yolov8n.pt"
    
    if best_model_path.exists():
        logger.info(f"Found trained model: {best_model_path}")
        model_path = str(best_model_path)
    elif pretrained_model_path.exists():
        logger.info(f"Found pretrained model: {pretrained_model_path}")
        model_path = str(pretrained_model_path)
    else:
        # Check for any .pt file in the directory
        pt_files = list(model_dir.glob("*.pt"))
        if pt_files:
            model_path = str(pt_files[0])
            logger.info(f"Using model file: {model_path}")
        else:
            raise FileNotFoundError(
                f"No model file found in {model_dir}. "
                f"Expected 'best.pt' or 'yolov8n.pt'"
            )
    
    # Load the YOLO model
    try:
        model = YOLO(model_path)
        logger.info(f"Model loaded successfully: {model_path}")
        logger.info(f"Model classes: {model.names}")
        return model
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise


def input_fn(request_body, content_type):
    """
    Deserialize and prepare the prediction input.
    
    Args:
        request_body: The body of the request
        content_type: The content type of the request
        
    Returns:
        Dictionary with 'image' (PIL Image) and 'confidence_threshold' (float)
    """
    logger.info(f"Received request with content_type: {content_type}")
    
    if content_type == 'application/json':
        # Parse JSON input
        input_data = json.loads(request_body)
        
        # Extract image data (base64 encoded)
        image_b64 = input_data.get('image', '')
        if not image_b64:
            raise ValueError("No 'image' field found in request")
        
        # Decode base64 image
        try:
            image_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(image_bytes))
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
        except Exception as e:
            logger.error(f"Failed to decode image: {e}")
            raise ValueError(f"Invalid image data: {e}")
        
        # Get confidence threshold (default: 0.5)
        confidence_threshold = float(input_data.get('confidence_threshold', 0.5))
        
        logger.info(f"Image decoded: {image.size}, confidence_threshold: {confidence_threshold}")
        
        return {
            'image': image,
            'confidence_threshold': confidence_threshold
        }
    else:
        raise ValueError(f"Unsupported content type: {content_type}")


def predict_fn(input_data, model):
    """
    Perform prediction on the deserialized input.
    
    Args:
        input_data: Dictionary from input_fn containing 'image' and 'confidence_threshold'
        model: The loaded YOLO model from model_fn
        
    Returns:
        Dictionary with detection results
    """
    logger.info("Running inference...")
    
    image = input_data['image']
    confidence_threshold = input_data['confidence_threshold']
    
    try:
        # Run YOLO inference
        results = model(
            image,
            conf=confidence_threshold,
            verbose=False
        )
        
        # Process results
        detections = []
        vape_detected = False
        cigarette_detected = False
        
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            
            if boxes is not None and len(boxes) > 0:
                for box in boxes:
                    # Get box coordinates
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    
                    # Get class and confidence
                    cls = int(box.cls.item())
                    confidence = float(box.conf.item())
                    class_name = model.names[cls]
                    
                    # Create detection dictionary
                    detection = {
                        'bbox': {
                            'x1': float(x1),
                            'y1': float(y1),
                            'x2': float(x2),
                            'y2': float(y2)
                        },
                        'class_name': class_name,
                        'class_id': cls,
                        'confidence': confidence
                    }
                    
                    detections.append(detection)
                    
                    # Check for vape or cigarette
                    if 'vape' in class_name.lower():
                        vape_detected = True
                    elif 'cigarette' in class_name.lower():
                        cigarette_detected = True
        
        # Build response
        response = {
            'detections': detections,
            'total_detections': len(detections),
            'vape_detected': vape_detected,
            'cigarette_detected': cigarette_detected,
            'faces_blurred': 0  # Face blurring can be done client-side
        }
        
        logger.info(f"Inference complete: {len(detections)} detections found")
        
        return response
        
    except Exception as e:
        logger.error(f"Inference failed: {e}")
        raise


def output_fn(prediction, accept):
    """
    Serialize the prediction result.
    
    Args:
        prediction: Dictionary from predict_fn
        accept: The accept header from the request
        
    Returns:
        JSON string of the prediction
    """
    logger.info(f"Serializing output, accept type: {accept}")
    
    if accept == 'application/json':
        return json.dumps(prediction)
    else:
        raise ValueError(f"Unsupported accept type: {accept}")

