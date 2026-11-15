"""Quality assistant: answers from plant data (read-only tools) and plant documents (RAG), with sources.

Backends: any OpenAI-compatible chat endpoint (Ollama, vLLM, LM Studio, an in-house gateway) so plant data
never leaves the site, or 'offline' (rule-based planner, no LLM) for the public demo and for CI.
"""
