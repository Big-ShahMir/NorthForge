"""Workflow planner: natural-language request to a validated draft workflow (Phase 5).

The planner is a LangGraph state machine (``graph.py``) whose nodes call the
``ModelRouter`` directly. The model only *proposes* (``schema.PlannerProposal``);
code decides what becomes a workflow: ``screen.py`` records unsafe or
out-of-scope asks before the model sees the request, ``compile.py`` turns the
proposal into a ``WorkflowDefinition`` under fixed safety rules and runs the
same semantic validation the API's validate endpoint uses, and ``service.py``
persists the result as a draft version. See ``DECISIONS.md`` ADR-028.
"""
