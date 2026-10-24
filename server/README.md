# Existing-server integration

Install `research_runner.py` and its public verification key beside the existing server's `store.py`. Apply `integration.patch` to that existing server. This is an additive extension; it reuses its database, model gateway, signed helper releases and helper verification. Do not run it as a separate server.

The existing local manager has a `helper-policy` operation (`required` or `skip`, optional PC ID). It writes `research_runner_policy`; saved `manager_rules` are read for every model call. Existing clients keep their current routes and behavior.

Focused regression checks against the existing source:

```sh
RESEARCH_CONTROL_SOURCE=/path/to/existing/server python3 -m pytest tests/test_sessions.py -q
```

The server validates the deployed signed installation document with the public key before giving instructions to the runner. Provider secrets remain on the server. No secret is included here.
