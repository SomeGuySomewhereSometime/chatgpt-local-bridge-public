"""Best-effort removal of common credential formats from diagnostic output."""
import re


def redact(text):
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1 [redacted]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})', '[redacted]', text)
    text = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@', r'\1[redacted]@', text)
    pattern = r'''(?ix)(["']?(?:[a-z0-9_]*api[_-]?key|[a-z0-9_]*token|[a-z0-9_]*secret|password|passwd|authorization|cookie)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)'''
    text = re.sub(pattern, r'\1[redacted]', text)
    text = re.sub(r'(?i)(--(?:api-key|token|password|secret)\s+)(\S+)', r'\1[redacted]', text)
    return text
