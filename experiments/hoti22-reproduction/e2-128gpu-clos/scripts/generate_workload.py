#!/usr/bin/env python3
"""One native AllReduce node per GPU. The system file selects 1D or 2D."""

import os

MESSAGE_BYTES = 128 * 1024 * 1024
NHOSTS = 128
COMM_COLL_NODE = 7
ALL_REDUCE = 0


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


def write_traces(directory, prefix="allreduce"):
    os.makedirs(directory, exist_ok=True)
    for rank in range(NHOSTS):
        body = _varint_field(1, 0)
        body += _bytes_field(2, b"allreduce_128gpu_128MiB")
        body += _varint_field(3, COMM_COLL_NODE)
        body += _attr("is_cpu_op", 27, 0)
        body += _attr("comm_type", 9, ALL_REDUCE)
        body += _attr("comm_size", 13, MESSAGE_BYTES)
        payload = _frame(_bytes_field(1, b"0.0.4")) + _frame(body)
        with open(os.path.join(directory, f"{prefix}.{rank}.et"), "wb") as handle:
            handle.write(payload)
    return MESSAGE_BYTES


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "workload")
    nbytes = write_traces(out)
    print(f"wrote 128 AllReduce traces, {nbytes} bytes, under {os.path.abspath(out)}")
