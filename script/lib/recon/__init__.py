"""Bundle -> ``input/<task>/`` reconstruction, split by what it recovers.

``script/reconstruct_input_from_bundle.py`` is the orchestrator; each module
here owns one recoverable surface so no single file has to hold the whole
inverse of the bundle writer:

  ``prompts``      the prompt text, its header block and the turn schedule
  ``sources``      verbatim carry-over: data/, persona/, rubric, TRUTH, inject
  ``metadata``     task.yaml / task.json rebuilt from data/task.toml
  ``environment``  the mock_data overlay and the mock-module drift scan
  ``gates``        the acceptance checks a reconstruction has to clear
"""
