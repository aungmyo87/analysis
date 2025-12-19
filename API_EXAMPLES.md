# API Payload Examples

## Base URL
```
http://your-server:8080
```

---

## 1. Normal reCAPTCHA v2

### 1.1 Proxyless
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2TaskProxyless",
        "websiteURL": "https://example.com/login",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    }
}
```

### 1.2 With Proxy
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2Task",
        "websiteURL": "https://example.com/login",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "proxy": {
            "type": "http",
            "address": "proxy.example.com",
            "port": 8080,
            "username": "proxyuser",
            "password": "proxypass"
        }
    }
}
```

---

## 2. Invisible reCAPTCHA v2

### 2.1 Proxyless
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2TaskProxyless",
        "websiteURL": "https://example.com/submit",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "isInvisible": true
    }
}
```

### 2.2 With Proxy
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2Task",
        "websiteURL": "https://example.com/submit",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "isInvisible": true,
        "proxy": {
            "type": "http",
            "address": "192.168.1.100",
            "port": 3128,
            "username": "user",
            "password": "pass"
        }
    }
}
```

---

## 3. Enterprise reCAPTCHA v2

### 3.1 Proxyless
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2EnterpriseTaskProxyless",
        "websiteURL": "https://enterprise-site.com/checkout",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "enterprisePayload": {
            "s": "additional-token-if-required"
        }
    }
}
```

### 3.2 With Proxy
```json
POST /createTask

{
    "clientKey": "your-api-key",
    "task": {
        "type": "RecaptchaV2EnterpriseTask",
        "websiteURL": "https://enterprise-site.com/checkout",
        "websiteKey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "enterprisePayload": {
            "s": "additional-token-if-required"
        },
        "proxy": {
            "type": "socks5",
            "address": "socks.proxy.com",
            "port": 1080,
            "username": "socksuser",
            "password": "sockspass"
        }
    }
}
```

---

## 4. Get Task Result

```json
POST /getTaskResult

{
    "clientKey": "your-api-key",
    "taskId": "uuid-returned-from-createTask"
}
```

### Response (Processing)
```json
{
    "errorId": 0,
    "status": "processing"
}
```

### Response (Ready)
```json
{
    "errorId": 0,
    "status": "ready",
    "solution": {
        "gRecaptchaResponse": "03AGdBq26xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx..."
    },
    "cost": "0.001",
    "createTime": 1734567890,
    "endTime": 1734567905,
    "solveCount": 1
}
```

---

## 5. Direct Solve (Simplified API)

### 5.1 Normal - Proxyless
```json
POST /solve

{
    "api_key": "your-api-key",
    "url": "https://example.com/login",
    "sitekey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "type": "normal"
}
```

### 5.2 Normal - With Proxy (String Format)
```json
POST /solve

{
    "api_key": "your-api-key",
    "url": "https://example.com/login",
    "sitekey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "type": "normal",
    "proxy": "http://user:pass@proxy.com:8080"
}
```

### 5.3 Invisible
```json
POST /solve

{
    "api_key": "your-api-key",
    "url": "https://example.com/submit",
    "sitekey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "type": "normal",
    "invisible": true
}
```

### 5.4 Enterprise
```json
POST /solve

{
    "api_key": "your-api-key",
    "url": "https://enterprise-site.com/checkout",
    "sitekey": "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "type": "enterprise",
    "enterprise_payload": {
        "s": "additional-token"
    }
}
```

### Response
```json
{
    "success": true,
    "token": "03AGdBq26xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx...",
    "elapsed_time": 12.5,
    "method": "audio",
    "cost": 0.001
}
```

---

## 6. Get Balance

```json
POST /getBalance

{
    "clientKey": "your-api-key"
}
```

### Response
```json
{
    "errorId": 0,
    "balance": 10.5
}
```

---

## 7. Add Balance (Admin Only)

```json
POST /addBalance

{
    "clientKey": "owner-admin-key",
    "targetKey": "user-api-key",
    "amount": 50.0
}
```

### Response
```json
{
    "errorId": 0,
    "balance": 60.5
}
```

---

## Proxy Formats

### Object Format (for /createTask)
```json
{
    "type": "http",           // http | https | socks4 | socks5
    "address": "proxy.com",
    "port": 8080,
    "username": "user",       // optional
    "password": "pass"        // optional
}
```

### String Format (for /solve)
```
http://user:pass@proxy.com:8080
socks5://user:pass@proxy.com:1080
proxy.com:8080:user:pass
```

---

## Error Codes

| Code | Name | Description |
|------|------|-------------|
| 0 | SUCCESS | No error |
| 1 | ERROR_KEY_DOES_NOT_EXIST | Invalid API key |
| 2 | ERROR_NO_SLOT_AVAILABLE | Thread limit reached |
| 3 | ERROR_ZERO_BALANCE | Insufficient balance |
| 10 | ERROR_WRONG_CAPTCHA_ID | Task not found |
| 11 | ERROR_TIMEOUT | Solving timeout |
| 12 | ERROR_RECAPTCHA_BLOCKED | reCAPTCHA blocked |
| 13 | ERROR_PROXY_CONNECT_REFUSED | Proxy connection failed |
| 14 | ERROR_CAPTCHA_UNSOLVABLE | Failed to solve |
| 15 | ERROR_BAD_PARAMETERS | Invalid parameters |

---

## Task Types Summary

| Type | Description |
|------|-------------|
| `RecaptchaV2Task` | Normal v2 with proxy |
| `RecaptchaV2TaskProxyless` | Normal v2 without proxy |
| `RecaptchaV2EnterpriseTask` | Enterprise v2 with proxy |
| `RecaptchaV2EnterpriseTaskProxyless` | Enterprise v2 without proxy |

---

## Python Example

```python
import requests
import time

API_URL = "http://localhost:8080"
API_KEY = "your-api-key"

def solve_recaptcha(site_url, site_key, invisible=False, proxy=None):
    # Create task
    payload = {
        "clientKey": API_KEY,
        "task": {
            "type": "RecaptchaV2Task" if proxy else "RecaptchaV2TaskProxyless",
            "websiteURL": site_url,
            "websiteKey": site_key,
            "isInvisible": invisible
        }
    }
    
    if proxy:
        payload["task"]["proxy"] = proxy
    
    response = requests.post(f"{API_URL}/createTask", json=payload)
    result = response.json()
    
    if result.get("errorId") != 0:
        raise Exception(result.get("errorMessage"))
    
    task_id = result["taskId"]
    
    # Poll for result
    for _ in range(60):  # 60 attempts, ~2 minutes
        response = requests.post(f"{API_URL}/getTaskResult", json={
            "clientKey": API_KEY,
            "taskId": task_id
        })
        result = response.json()
        
        if result.get("status") == "ready":
            return result["solution"]["gRecaptchaResponse"]
        
        if result.get("errorId") != 0:
            raise Exception(result.get("errorMessage"))
        
        time.sleep(2)
    
    raise Exception("Timeout waiting for solution")

# Usage
token = solve_recaptcha(
    "https://example.com/login",
    "6Le-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
)
print(f"Token: {token[:50]}...")
```
