"""Length-prefixed Chakra v0.0.4 nodes. Repeated deps are packed, as in proto3."""

COMP = 4
SEND = 5
RECV = 6
COLL = 7


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(field, wire):
    return _varint((field << 3) | wire)


def _bytes_field(field, payload):
    return _key(field, 2) + _varint(len(payload)) + payload


def _varint_field(field, value):
    return _key(field, 0) + _varint(value)


def _attr(name, value_field, value):
    body = _bytes_field(1, name.encode()) + _varint_field(value_field, value)
    return _bytes_field(10, body)


def _frame(message):
    return _varint(len(message)) + message


def _deps(field, ids):
    if not ids:
        return b""
    packed = b"".join(_varint(item) for item in ids)
    return _bytes_field(field, packed)


def node(node_id, name, node_type, deps, duration_us=0, attrs=()):
    body = _varint_field(1, node_id)
    body += _bytes_field(2, name.encode())
    body += _varint_field(3, node_type)
    body += _deps(5, deps)
    if duration_us:
        body += _varint_field(7, duration_us)
    for name, field, value in attrs:
        body += _attr(name, field, value)
    return body


def write_trace(path, nodes):
    blob = _frame(_bytes_field(1, b"0.0.4"))
    for item in nodes:
        blob += _frame(item)
    with open(path, "wb") as handle:
        handle.write(blob)


def cpu_off():
    return ("is_cpu_op", 27, 0)
