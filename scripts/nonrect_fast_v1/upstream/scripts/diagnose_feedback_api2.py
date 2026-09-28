#!/usr/bin/env python3
"""One minimal direct API2 diagnostic; no proxy, renderer, evaluator or experiment.

Default is offline. --prompt-api2 explicitly authorizes a single HTTP POST.
Only a bounded, credential-redacted error summary is saved; never raw exchanges.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "Support/outputs/feedback_first3_api2_gpt41_20260915_prepared_v3/deployment.json"
MAX_ERROR_BYTES = 16384


def load_route():
    deployment = json.loads(DEPLOYMENT.read_text())
    config_path = Path(deployment["proxy_config"])
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != deployment["proxy_config_sha256"]:
        raise ValueError("prepared proxy configuration changed")
    models = json.loads(config_path.read_text())["model_list"]
    model = next(row for row in models if row["model_name"] == "gpt-4.1")["litellm_params"]["model"]
    if model != "openai/api_azure_openai_gpt-4.1":
        raise ValueError("unexpected prepared model route")
    base = deployment["api2_base_url_v1"]
    if base != "http://llm-api.model-eval.woa.com/v1":
        raise ValueError("unexpected prepared gateway; review before sending credentials")
    return base + "/chat/completions", model.removeprefix("openai/")


def redact(value, credential):
    text = html.unescape(str(value))
    pieces = {credential, *credential.split(":", 1)} - {""}
    variants = set(pieces)
    for secret in pieces:
        variants.update((urllib.parse.quote(secret, safe=""), urllib.parse.quote_plus(secret),
                         base64.b64encode(secret.encode()).decode(), json.dumps(secret)[1:-1]))
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+[^\s\"'<>]+", "Bearer <redacted>", text)
    text = re.sub(r"(?i)(?:authorization|api[_-]?key|app[_-]?key|access[_-]?token|password|secret)\s*[\"']?\s*[:=]\s*[^\n,;}]+", "credential=<redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "<redacted>", text)
    text = re.sub(r"https?://[^\s<>\"']+", "<url omitted>", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text[:2000]


def error_summary(raw, credential):
    decoded = raw[:MAX_ERROR_BYTES].decode("utf-8", errors="replace")
    try:
        parsed = json.loads(decoded)
    except ValueError:
        # Common HTTP gateways return a short plain-text or HTML policy error.
        return {"message": redact(re.sub(r"<[^>]*>", " ", decoded), credential)}
    candidate = parsed.get("error", parsed) if isinstance(parsed, dict) else parsed
    if isinstance(candidate, str):
        return {"message": redact(candidate, credential)}
    if not isinstance(candidate, dict):
        return {"message": "No textual error description"}
    result = {key: redact(candidate[key], credential) for key in ("message", "type", "code", "detail")
              if isinstance(candidate.get(key), (str, int))}
    return result or {"message": "No recognized error fields"}


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect not followed", headers, fp)


def run_probe(endpoint, model, credential, *, opener=None):
    """Exactly one POST, with no retries, redirects or alternative model calls."""
    payload = {"model": model, "messages": [{"role": "user", "content": 'Return exactly {"ok":true} as JSON.'}],
               "max_tokens": 16, "temperature": 0, "response_format": {"type": "json_object"}}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"}, method="POST")
    opener = opener or urllib.request.build_opener(RejectRedirect())
    result = {"diagnostic_only": True, "experiment_started": False, "model": model,
              "http_post_attempts": 1, "retries": 0, "bypasses_local_litellm": True,
              "minimal_prompt_not_context_qualification": True}
    try:
        with opener.open(request, timeout=120) as response:
            raw = response.read(MAX_ERROR_BYTES + 1)
            result.update(status="request_accepted", http_status=response.status)
            try:
                value = json.loads(raw)
                result["expected_json_received"] = json.loads(value["choices"][0]["message"]["content"]) == {"ok": True}
            except (ValueError, KeyError, IndexError, TypeError):
                result["expected_json_received"] = False
    except urllib.error.HTTPError as exc:
        result.update(status="http_error", http_status=exc.code,
                      error=error_summary(exc.read(MAX_ERROR_BYTES + 1), credential))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result.update(status="transport_error", error_type=type(exc).__name__,
                      error={"message": redact(str(exc), credential)})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-api2", action="store_true", help="Hidden input, then one diagnostic request only.")
    args = parser.parse_args(argv)
    endpoint, model = load_route()
    if not args.prompt_api2:
        print(json.dumps({"status": "offline_ready", "model": model, "api_calls": 0, "experiment_started": False}))
        return 0
    if not sys.stdin.isatty():
        raise ValueError("Use an interactive Terminal; refusing echoed/piped credential input")
    print("DIAGNOSTIC ONLY: one minimal API2 request, no retries, no experiment. Ctrl-C cancels.", flush=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        credential = getpass.getpass("API2 (APP_ID:APP_KEY, hidden): ").split("?", 1)[0]
    app_id, separator, app_key = credential.partition(":")
    if not separator or not app_id or not app_key or any(c.isspace() for c in credential):
        raise ValueError("Invalid APP_ID:APP_KEY format")
    os.umask(0o077)
    parent = ROOT / "Support/outputs"
    output = Path(tempfile.mkdtemp(prefix="feedback_api2_diagnostic_", dir=parent))
    result = run_probe(endpoint, model, credential)
    del credential, app_id, app_key
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["system_proxy_configured"] = bool(urllib.request.getproxies())
    result["gateway_proxy_bypassed"] = urllib.request.proxy_bypass(urllib.parse.urlsplit(endpoint).hostname)
    with (output / "diagnostic.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Diagnostic saved: {output / 'diagnostic.json'}")
    return 0 if result["status"] == "request_accepted" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Diagnostic cancelled.")
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({"status": "diagnostic_failed", "error_type": type(exc).__name__, "experiment_started": False}))
        raise SystemExit(2)
