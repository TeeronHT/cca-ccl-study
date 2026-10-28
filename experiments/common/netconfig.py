"""Read and rewrite the ns-3 backend's key/value network config files."""

import hashlib

PATH_KEYS = (
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "QLEN_MON_FILE",
    "SWITCH_BUFFER_MAP_FILE",
    "CCA_TRACE_FILE",
    "FLOW_TRACE_FILE",
    "PATH_TRACE_FILE",
    "COUNTERS_OUTPUT_FILE",
    "SNAPSHOT_OUTPUT_FILE",
    "LINK_REPORT_FILE",
)

# Keys introduced by the backend extensions, with their legacy defaults.
EXTENSION_DEFAULTS = {
    "TRANSPORT_CONNECTION_MODE": "LEGACY",
    "ENABLE_PFC": "1",
    "PFC_THRESHOLD_VALIDATION": "LEGACY",
    "QUEUE_LOG_MIN_BYTES": "1000",
    "ENABLE_CCA_TRACE": "0",
    "ENABLE_FLOW_METADATA_TRACE": "0",
    "ENABLE_PATH_TRACE": "0",
    "TRACE_LEVEL": "2",
}


def parse(path):
    """Return an ordered list of (key, value) and a dict view (last wins)."""
    items = []
    with open(path) as handle:
        for line in handle:
            line = line.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            key, _, rest = stripped.partition(" ")
            items.append((key, rest.strip()))
    return items


def as_dict(path):
    return dict(parse(path))


def parse_map(text):
    """'n r1 v1 r2 v2 ...' -> {rate: value_string}."""
    parts = text.split()
    count = int(parts[0])
    values = parts[1:]
    if len(values) != count * 2:
        raise ValueError(f"map count {count} does not match {len(values)} values")
    return {int(values[i]): values[i + 1] for i in range(0, len(values), 2)}


def format_map(mapping):
    """{rate: value} -> 'n r1 v1 ...' sorted by rate."""
    items = sorted(mapping.items())
    return " ".join([str(len(items))] + [f"{rate} {value}" for rate, value in items])


def rewrite(src, dst, overrides):
    """Copy src to dst replacing or appending each key in overrides (dict).

    Lines that are not overridden are copied verbatim, blank lines included,
    so a diff against the source shows only the intended keys.
    """
    overrides = dict(overrides)
    out = []
    with open(src) as handle:
        for line in handle:
            line = line.rstrip("\n")
            stripped = line.strip()
            key = stripped.partition(" ")[0] if stripped else ""
            if key and key in overrides:
                out.append(f"{key} {overrides.pop(key)}")
            else:
                out.append(line)
    while out and out[-1] == "":
        out.pop()
    if overrides:
        # No comment marker: the backend parser treats every token as a key.
        out.append("")
        for key, value in overrides.items():
            out.append(f"{key} {value}")
    with open(dst, "w") as handle:
        handle.write("\n".join(out) + "\n")


def semantic_hash(path, ignore_keys=PATH_KEYS):
    """sha256 over sorted key/value pairs, ignoring output/input path keys."""
    items = sorted((k, v) for k, v in parse(path) if k not in ignore_keys)
    digest = hashlib.sha256()
    for key, value in items:
        digest.update(f"{key} {value}\n".encode())
    return digest.hexdigest()


def effective(path):
    """Config dict with extension defaults filled in for absent keys."""
    values = dict(EXTENSION_DEFAULTS)
    values.update(as_dict(path))
    return values
