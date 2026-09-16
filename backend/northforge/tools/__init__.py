"""Deterministic, read-only tool registry for the NorthForge workflow runtime.

See ``docs/WORKFLOW_SPEC.md`` (tool policy) and ``docs/SECURITY_AND_SAFETY.md``
(tool safety). A tool is registered with a name, typed input and output
models, and a safety classification (``northforge.tools.spec.ToolSpec``). A
step can only invoke a tool that is both registered and declared in its
workflow's ``allowed_tools``; arguments and outputs are always validated
(``northforge.tools.invoke.invoke_tool``). Phase 2 ships three read-only
tools backed by a deterministic synthetic fixture corpus
(``northforge.tools.fixtures.corpus``); no tool performs a side effect.
"""

from __future__ import annotations
