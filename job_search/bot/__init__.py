"""Telegram control bot: trigger the pipeline on demand from behind NAT.

The home network has no dedicated IP, so no webhook/port-forward is possible.
This package long-polls Telegram's ``getUpdates`` (all outbound HTTPS) and, from
the one authorized chat, runs the pipeline via ``scripts/run_pipeline.sh`` — the
same flock'd wrapper the daily timer uses, so runs are serialized on the single
core. See ``python -m job_search.bot`` (``__main__``) for the composition root.
"""
