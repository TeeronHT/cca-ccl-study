"""Minimal Chakra v0.0.4 execution-trace writer (length-prefixed protobuf).

Only what the experiments need: COMP, COMM_SEND, COMM_RECV and COMM_COLL
nodes with control dependencies and scalar attributes. Field numbers follow
et_def.proto: Node{id=1,name=2,type=3,ctrl_deps=5,duration_micros=7,attr=10},
AttributeProto{name=1,uint32_val=11,uint64_val=13,bool_val=27,int64_val=12}.
"""

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


def attr_bool(name, value):
    return (name, 27, 1 if value else 0)


def attr_u32(name, value):
    return (name, 11, int(value))


def attr_u64(name, value):
    return (name, 13, int(value))


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


def node(node_id, name, node_type, deps=(), duration_us=0, attrs=()):
    body = _varint_field(1, node_id)
    body += _bytes_field(2, name.encode())
    body += _varint_field(3, node_type)
    body += _deps(5, list(deps))
    if duration_us:
        body += _varint_field(7, duration_us)
    for attr_name, field, value in attrs:
        body += _attr(attr_name, field, value)
    return body


def send_node(node_id, name, src, dst, size, tag, deps=(), extra_attrs=()):
    attrs = [
        attr_bool("is_cpu_op", False),
        attr_u32("comm_src", src),
        attr_u32("comm_dst", dst),
        attr_u64("comm_size", size),
        attr_u32("comm_tag", tag),
    ] + list(extra_attrs)
    return node(node_id, name, SEND, deps, 0, attrs)


def recv_node(node_id, name, src, dst, size, tag, deps=(), extra_attrs=()):
    attrs = [
        attr_bool("is_cpu_op", False),
        attr_u32("comm_src", src),
        attr_u32("comm_dst", dst),
        attr_u64("comm_size", size),
        attr_u32("comm_tag", tag),
    ] + list(extra_attrs)
    return node(node_id, name, RECV, deps, 0, attrs)


def comp_node(node_id, name, duration_us, deps=()):
    return node(node_id, name, COMP, deps, duration_us, [attr_bool("is_cpu_op", False)])


def allreduce_node(node_id, name, size, deps=()):
    attrs = [attr_bool("is_cpu_op", False), ("comm_type", 9, 0), attr_u64("comm_size", size)]
    return node(node_id, name, COLL, deps, 0, attrs)


def write_trace(path, nodes):
    blob = _frame(_bytes_field(1, b"0.0.4"))
    for item in nodes:
        blob += _frame(item)
    with open(path, "wb") as handle:
        handle.write(blob)
