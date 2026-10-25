# Development

[← README](../README.md)

Run the tests in a container:

```bash
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src -w /src python:3.12-slim \
  sh -c "pip install -q --user -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
```

or locally with Python 3.12: `pip install -r requirements-dev.txt && pytest`.

## Layout

```
noty/
  config.py       settings (.env) and users.yaml
  jotty.py        Jotty API client, merging both endpoints into BoardState
  comments.py     comment files reader
  diff.py         snapshot comparison and notification rules
  render.py       Telegram HTML messages
  telegram.py     sendMessage with retries
  state.py        per-user snapshot storage
  service.py      poll loop, missing-board retention
  healthcheck.py  container health check
tests/
```

Behaviour is described in [how it works](how-it-works.md) and [notifications](notifications.md); keep them in
sync with the code.
