"""Safe real-API diagnostic for CloudTwin's Gemini intent parser.

Reads GEMINI_API_KEY from the process environment and never prints it.
This script performs inference only; it has no AWS/Kubernetes mutation code.
"""

from __future__ import annotations

import os
import sys

from google import genai
from google.genai import types

from gemini import GeminiClient, InterpretationError, interpret_change


def _resource(resource_type: str, name: str, **properties):
    slug = {"DEPLOYMENT": "deployment", "SERVICE": "service"}[resource_type]
    namespace = properties.setdefault("namespace", "default")
    return {"id": f"k8s:{slug}:{namespace}:{name}", "name": name, "type": resource_type,
            "provider": "kubernetes", "properties": properties}


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    timeout = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "15"))
    if not key:
        print("FAIL CLIENT_INITIALIZATION: GEMINI_API_KEY is not configured")
        return 2

    print("google-genai import: OK")
    print(f"model: {model}")
    try:
        sdk = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        print("client initialization: OK")
        response = sdk.models.generate_content(model=model, contents="Reply with exactly OK")
        if not response.text:
            raise RuntimeError("model returned no text")
        print("simple text request: OK")
    except Exception as exc:
        print(f"FAIL API_REQUEST: {type(exc).__name__}: {' '.join(str(exc).split())[:300]}")
        return 3

    twin = {"resources": [
        _resource("DEPLOYMENT", "ui", replicas=3),
        _resource("SERVICE", "ui", service_type="ClusterIP"),
        _resource("DEPLOYMENT", "catalog", replicas=2),
        _resource("SERVICE", "checkout", service_type="ClusterIP"),
        _resource("DEPLOYMENT", "orders", replicas=2),
        _resource("DEPLOYMENT", "carts", replicas=2),
    ], "relationships": []}
    prompts = [
        "Expose the ui service publicly",
        "Make the ui accessible from the internet",
        "Put the ui behind a public load balancer",
        "Run only one replica of the ui deployment",
        "Scale ui to 4 replicas",
        "Increase catalog to 5 replicas",
        "Make checkout publicly accessible",
        "Scale the application down",
    ]
    cloudtwin = GeminiClient(api_key=key, model=model, timeout_seconds=timeout, sdk_client=sdk)
    failed = False
    for prompt in prompts:
        try:
            result = interpret_change(prompt, twin, cloudtwin)
            print(f"OK | {result.provider} | {prompt} | {result.scenario['target_resource']} | "
                  f"{result.scenario['property']}={result.scenario['after']}")
            if result.provider != "Gemini":
                failed = True
        except InterpretationError as exc:
            expected = prompt == "Scale the application down" and exc.stage == "RESOURCE_RESOLUTION"
            print(f"{'OK' if expected else 'FAIL'} | {exc.stage} | {prompt} | {exc.safe_message}")
            failed = failed or not expected
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
