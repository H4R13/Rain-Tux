"""Split Rainmeter action strings (``[!Bang a "b c"][!Other]``) into commands and arguments."""

from __future__ import annotations


def split_commands(action: str) -> list[str]:
    """``[!A x][!B "y]"]`` → ``['!A x', '!B "y]"']``; a bare ``!A x`` is one command.

    Brackets nest (``[!SetVariable V [Measure]]``) and are ignored inside quotes,
    including Rainmeter's ``\"\"\"triple quotes\"\"\"``.
    """
    s = action.strip()
    if not s:
        return []
    if not s.startswith("["):
        return [s]
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] != "[":
            i += 1
        if i >= n:
            break
        depth = 0
        j = i
        quote: str | None = None
        while j < n:
            if quote:
                if s.startswith(quote, j):
                    j += len(quote)
                    quote = None
                    continue
            elif s.startswith('"""', j):
                quote = '"""'
                j += 3
                continue
            elif s[j] == '"':
                quote = '"'
            elif s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        cmd = s[i + 1 : j].strip()
        if cmd:
            out.append(cmd)
        i = j + 1
    return out


def split_args(command: str) -> list[str]:
    """Whitespace-separated arguments; ``"…"`` and ``\"\"\"…\"\"\"`` keep spaces (quotes removed)."""
    args: list[str] = []
    i, n = 0, len(command)
    while i < n:
        while i < n and command[i].isspace():
            i += 1
        if i >= n:
            break
        if command.startswith('"""', i):
            end = command.find('"""', i + 3)
            end = n if end < 0 else end
            args.append(command[i + 3 : end])
            i = end + 3
        elif command[i] == '"':
            end = command.find('"', i + 1)
            end = n if end < 0 else end
            args.append(command[i + 1 : end])
            i = end + 1
        else:
            j = i
            depth = 0
            while j < n and (depth > 0 or not command[j].isspace()):
                if command[j] == "[":
                    depth += 1
                elif command[j] == "]":
                    depth = max(0, depth - 1)
                j += 1
            args.append(command[i:j])
            i = j
    return args
