# Unified reCAPTCHA v2 Solver

A comprehensive reCAPTCHA v2 solver supporting **Normal**, **Invisible**, and **Enterprise** variants.

## Features

- ✅ **Normal reCAPTCHA v2** - Standard checkbox captcha
- ✅ **Invisible reCAPTCHA v2** - Programmatically triggered
- ✅ **Enterprise reCAPTCHA v2** - With action parameters
- ✅ **Audio Solving** - Whisper/Google/Azure transcription
- ✅ **Image Solving** - Custom YOLOv8 model (~85% mAP50)
- ✅ **Browser Pool** - Patchright stealth automation
- ✅ **2Captcha Compatible API** - Drop-in replacement

## Installation

```bash
cd unified_solver
pip install -r requirements.txt
```

## Quick Start

```bash
# Start the server
python main.py

# Server runs on http://localhost:8080
```

## API Usage

### Create Task

```bash
curl -X POST http://localhost:8080/api/v1/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "test_key_67890",
    "task": {
      "type": "RecaptchaV2TaskProxyless",
      "websiteURL": "https://example.com",
      "websiteKey": "6Le-xxxxx",
      "recaptchaType": "normal"
    }
  }'
```

### Get Result

```bash
curl -X POST http://localhost:8080/api/v1/getTaskResult \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "test_key_67890",
    "taskId": "task-uuid-here"
  }'
```

### Direct Solve

```bash
curl -X POST http://localhost:8080/api/v1/solve \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "test_key_67890",
    "url": "https://example.com",
    "sitekey": "6Le-xxxxx",
    "type": "normal"
  }'
```

## Configuration

Edit `config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

browser:
  pool_size: 20
  headless: true

solver:
  primary_method: "audio"  # or "image"
  image:
    model_path: "models/recaptcha_yolov8m_best.pt"
```

## Custom YOLO Model

Place your trained model at:
```
unified_solver/models/recaptcha_yolov8m_best.pt
```

## Project Structure

```
unified_solver/
├── api/                  # Flask API
│   ├── routes/           # Endpoints
│   └── middleware/       # Auth, rate limiting
├── core/                 # Core components
│   ├── browser_pool.py   # Browser management
│   ├── task_manager.py   # Task tracking
│   └── config.py         # Configuration
├── solvers/              # Solver implementations
│   ├── normal_solver.py
│   ├── invisible_solver.py
│   └── enterprise_solver.py
├── challenges/           # Challenge handlers
│   ├── audio_solver.py
│   └── image_solver.py
├── utils/                # Utilities
├── models/               # ML models (YOLO, Whisper)
├── data/                 # API keys, storage
└── main.py               # Entry point
```

## License

Private - All rights reserved
