"""Public product and release identity; independent of private environment state."""
NAME = '知行财富'
VERSION = '0.9.0'
APPLICATION = 'portfolio-journal'  # Stable identity for old backup/client compatibility.


def release_info():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'release.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    return {key: value.get(key) for key in ('release', 'source_id', 'commit')}
