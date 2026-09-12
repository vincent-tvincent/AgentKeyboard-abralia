# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read the existing VIA switch matrix through a borrowed Raw HID transport."""

from .errors import ProtocolError


class ViaMatrixReader:
    def __init__(self, transport, rows: int, columns: int):
        if not 1 <= rows <= 255 or not 1 <= columns <= 32:
            raise ProtocolError("Unsupported VIA matrix dimensions")
        self.transport, self.rows, self.columns = transport, rows, columns
        self.row_bytes = (columns + 7) // 8

    def read(self) -> tuple[int, ...]:
        values = []
        per_packet = 28 // self.row_bytes
        for offset in range(0, self.rows, per_packet):
            reply = self.transport.transact([0x02, 0x03, offset],
                lambda r: len(r) == 32 and (r[0] == 0xFF or r[:3] == bytes((0x02, 0x03, offset))),
                timeout_ms=150)
            if reply[:3] != bytes((0x02, 0x03, offset)):
                raise ProtocolError("VIA matrix-state readback is unavailable")
            count = min(per_packet, self.rows - offset)
            for i in range(count):
                start = 3 + i * self.row_bytes
                values.append(int.from_bytes(reply[start:start + self.row_bytes], 'big')
                              & ((1 << self.columns) - 1))
        return tuple(values)
