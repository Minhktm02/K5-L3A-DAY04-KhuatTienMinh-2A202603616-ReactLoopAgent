"""Các provider cùng một interface. Không chứa agent loop."""
import json
import os
import re
import urllib.error
import urllib.request
import uuid

CONFIG = {
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/chat/completions'),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions'),
    'opencode-go': ('OPENCODE_GO_API_KEY', 'https://opencode.ai/inference/go/openai/v1/chat/completions'),
    'anthropic': ('ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1/messages'),
}

_QUOTA_HEADERS = {
    'retry-after',
    'ratelimit-limit', 'ratelimit-remaining', 'ratelimit-reset',
    'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset',
    'x-ratelimit-resource',
}
_SECRET_PATTERNS = (
    re.compile(r'(?i)\bAIza[0-9A-Za-z_-]{20,}\b'),
    re.compile(r'(?i)\bBearer\s+\S+'),
    re.compile(
        r'(?i)\b(api[_ -]?key|key|access[_ -]?token|refresh[_ -]?token|'
        r'token|secret|password|credential|authorization)\b'
        r'(\s*[:=]\s*|\s+)(["\']?)[^\s,;}]+'
    ),
)


def _safe_diagnostic_value(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text[:240]


def _error_diagnostic(exc, request_headers):
    headers = exc.headers
    content_type = ''
    if headers:
        content_type = _safe_diagnostic_value(headers.get('Content-Type', ''))[:100]

    secrets = []
    for name, value in request_headers.items():
        if name.lower() in {'authorization', 'x-api-key', 'x-goog-api-key'}:
            value = str(value)
            secrets.append(value)
            if value.lower().startswith('bearer '):
                secrets.append(value[7:])

    try:
        body = exc.read(65537)
        if len(body) > 65536:
            raise ValueError('response body exceeds diagnostic limit')
        data = json.loads(body.decode('utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {'status': exc.code, 'contentType': content_type}

    error = data.get('error') if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return {'status': exc.code, 'contentType': content_type}

    diagnostic = {'status': exc.code}
    for source, target in (('code', 'apiCode'), ('status', 'apiStatus'),
                           ('message', 'message')):
        value = error.get(source)
        if isinstance(value, (str, int, float)):
            diagnostic[target] = _safe_diagnostic_value(value, secrets)

    allowed_fields = {
        'quotaMetric': 'quotaMetric', 'quota_metric': 'quotaMetric',
        'quotaId': 'quotaId', 'quota_id': 'quotaId',
        'quotaValue': 'quotaValue', 'quota_value': 'quotaValue',
        'quotaLimit': 'quotaValue', 'quota_limit': 'quotaValue',
        'retryDelay': 'retryDelay', 'retry_delay': 'retryDelay',
        'model': 'model', 'reason': 'reason',
        'location': 'quotaLocation',
    }

    def collect(mapping, values):
        for key, target in allowed_fields.items():
            value = mapping.get(key)
            if isinstance(value, (str, int, float)):
                safe_value = _safe_diagnostic_value(value, secrets)
                if safe_value:
                    values.setdefault(target, safe_value)

    values = {}
    details = error.get('details')
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            collect(detail, values)
            for nested_key in ('metadata', 'quotaDimensions', 'quota_dimensions'):
                nested = detail.get(nested_key)
                if isinstance(nested, dict):
                    collect(nested, values)
            violations = detail.get('violations')
            if isinstance(violations, list):
                for violation in violations:
                    if isinstance(violation, dict):
                        collect(violation, values)
                        nested = violation.get('quotaDimensions')
                        if isinstance(nested, dict):
                            collect(nested, values)
    diagnostic.update(values)

    safe_headers = {}
    if headers:
        for name, value in headers.items():
            normalized = name.lower()
            if normalized in _QUOTA_HEADERS:
                safe_headers[normalized] = _safe_diagnostic_value(value, secrets)
    if safe_headers:
        diagnostic['headers'] = safe_headers
    return diagnostic


def post_json(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not print response bodies: they may contain account or request data.
        message = f'Provider trả HTTP {exc.code}; kiểm tra model, key và quota.'
        if (exc.code == 429 and url == CONFIG['gemini'][1]
                and os.getenv('GEMINI_HTTP_DIAGNOSTICS') == '1'):
            message += ' Diagnostic: ' + json.dumps(
                _error_diagnostic(exc, headers),
                ensure_ascii=True,
                separators=(',', ':'),
            )
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Không kết nối được provider: {exc.reason}') from exc


class Provider:
    """complete(messages, schemas) -> {message, calls, finish, usage}.

    messages: system/user/assistant/tool theo format Chat Completions. message trả về
    cần được append nguyên vẹn; tool result có role, tool_call_id, content (JSON string).
    """
    def __init__(self, name, model):
        if name not in CONFIG:
            raise ValueError(f'Provider không hợp lệ: {name}')
        if not model:
            raise ValueError('Cần đặt MODEL là model có hỗ trợ tool calling.')
        self.name, self.model = name, model
        # One Provider instance represents one conversation/run.
        self.session_id = str(uuid.uuid4()) if name == 'opencode-go' else None
        key_name, self.url = CONFIG[name]
        self.key = os.getenv(key_name)
        if not self.key:
            raise ValueError(f'Thiếu {key_name}; xem README.md.')

    def complete(self, messages, schemas):
        if self.name == 'anthropic':
            return self._anthropic(messages, schemas)
        payload = {'model': self.model, 'messages': messages}
        if schemas:
            payload['tools'] = schemas
        headers = {'Authorization': f'Bearer {self.key}'}
        if self.name == 'opencode-go':
            headers.update({'User-Agent': 'StatusAgentLab/1.0', 'x-opencode-session': self.session_id})
        data = post_json(self.url, headers, payload)
        choice = data['choices'][0]
        message = choice['message']
        calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in message.get('tool_calls', [])]
        return {'message': message, 'calls': calls, 'finish': choice.get('finish_reason'), 'usage': data.get('usage', {})}

    def _anthropic(self, messages, schemas):
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        native = []
        for m in messages:
            if m['role'] == 'system':
                continue
            if m['role'] == 'assistant':
                blocks = []
                if m.get('content'):
                    blocks.append({'type': 'text', 'text': m['content']})
                for call in m.get('tool_calls', []):
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': json.loads(call['function']['arguments'])})
                native.append({'role': 'assistant', 'content': blocks})
            elif m['role'] == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
                if native and native[-1]['role'] == 'user' and isinstance(native[-1]['content'], list):
                    native[-1]['content'].append(block)
                else:
                    native.append({'role': 'user', 'content': [block]})
            else:
                native.append({'role': 'user', 'content': m['content']})
        tools = [{'name': x['function']['name'], 'description': x['function']['description'], 'input_schema': x['function']['parameters']} for x in schemas]
        payload = {'model': self.model, 'max_tokens': 1024, 'system': system, 'messages': native}
        if tools:
            payload['tools'] = tools
        data = post_json(self.url, {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, payload)
        blocks = data['content']
        calls = [{'id': b['id'], 'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)} for b in blocks if b['type'] == 'tool_use']
        text = '\n'.join(b['text'] for b in blocks if b['type'] == 'text')
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in calls]
        reason = {'end_turn': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        return {'message': message, 'calls': calls, 'finish': reason, 'usage': data.get('usage', {})}
