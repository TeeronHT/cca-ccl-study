#!/usr/bin/env python3
"""Write the 7-to-1 incast Chakra traces.

Hosts 1-7 each send MESSAGE_BYTES to host 0. Host 0 posts the matching
receives. Every node is dependency-free, so ASTRA-sim issues them together
at time 0.
"""

import os

MESSAGE_BYTES = 10 * 1024 * 1024
NHOSTS = 8


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


def _key(field_number, wire_type):
    return _varint((field_number << 3) | wire_type)


def _bytes_field(field_number, payload):
    return _key(field_number, 2) + _varint(len(payload)) + payload


def _varint_field(field_number, value):
    return _key(field_number, 0) + _varint(value)


def _attr(name, value_field, value):
    body = _bytes_field(1, name.encode()) + _varint_field(value_field, value)
    return _bytes_field(10, body)


def _frame(message):
    return _varint(len(message)) + message


def _metadata():
    return _frame(_bytes_field(1, b"0.0.4"))


def _node(node_id, name, node_type, attrs):
    body = _varint_field(1, node_id) + _bytes_field(2, name.encode())
    body += _varint_field(3, node_type)
    for name_str, value_field, value in attrs:
        body += _attr(name_str, value_field, value)
    return _frame(body)


def _comm_attrs(src, dst, size, tag):
    # AttributeProto: bool_val=27, uint32_val=11, uint64_val=13.
    return [
        ("is_cpu_op", 27, 0),
        ("comm_src", 11, src),
        ("comm_dst", 11, dst),
        ("comm_size", 13, size),
        ("comm_tag", 11, tag),
    ]


def write_traces(directory, prefix="incast"):
    os.makedirs(directory, exist_ok=True)
    # COMM_SEND_NODE = 5, COMM_RECV_NODE = 6.
    receiver = [_metadata()]
    for src in range(1, NHOSTS):
        receiver.append(
            _node(src - 1, "incast_recv", 6, _comm_attrs(src, 0, MESSAGE_BYTES, src))
        )
    recv_path = os.path.join(directory, f"{prefix}.0.et")
    with open(recv_path, "wb") as handle:
        handle.write(b"".join(receiver))

    for src in range(1, NHOSTS):
        payload = _metadata() + _node(
            0, "incast_send", 5, _comm_attrs(src, 0, MESSAGE_BYTES, src)
        )
        path = os.path.join(directory, f"{prefix}.{src}.et")
        with open(path, "wb") as handle:
            handle.write(payload)
    return MESSAGE_BYTES


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "workload")
    nbytes = write_traces(out)
    print(f"wrote 8 traces, {nbytes} bytes per flow, under {os.path.abspath(out)}")
