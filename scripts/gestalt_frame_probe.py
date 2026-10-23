#!/usr/bin/env python3
"""Read existing Gestalt v3 frame metadata on Windows, without controlling the game."""
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True, help='Game process owning the WS listener')
    parser.add_argument('--seconds', type=float, default=2)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-takeover', action='store_true', help='Also require the current camera/identity contract')
    parser.add_argument('--player', type=int, help='Expected shooter player id')
    parser.add_argument('--fov', type=float, default=25)
    args = parser.parse_args()
    if (os.name != 'nt' or args.pid <= 0 or not math.isfinite(args.seconds) or not 1 <= args.seconds <= 10 or
            not math.isfinite(args.fov) or not 0 < args.fov < 180):
        parser.error('Requires Windows, a positive game PID, and --seconds in [1,10]')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenFileMappingW.restype = wintypes.HANDLE
    kernel.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = '{47534652-414D-4501-0000-%012X}' % args.pid
    mapping = kernel.OpenFileMappingW(4, False, name)  # FILE_MAP_READ; never creates a mapping
    if not mapping:
        raise ctypes.WinError(ctypes.get_last_error())
    address = None
    try:
        address = kernel.MapViewOfFile(mapping, 4, 0, 0, 0)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        header = ctypes.string_at(address, 64)
        magic, version, header_size, count, slot_header, stride, max_w, max_h = struct.unpack_from('<Q7I', header)
        region_bytes, writer_pid, qpc_hz = struct.unpack_from('<3Q', header, 40)
        if (magic != 0x314D52464E565347 or version != 3 or header_size != 4096 or
                slot_header != 256 or not 1 <= count <= 16 or stride <= 256 or
                header_size + count * stride > region_bytes or writer_pid != args.pid or qpc_hz <= 0):
            raise RuntimeError('Shared frame layout/writer does not match the current v3 bridge')
        frames = []
        last_commit = 0
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            candidates = []
            for index in range(count):
                slot_address = address + header_size + index * stride
                packet = ctypes.string_at(slot_address, slot_header)
                commit, frame_id, qpc, world_s = struct.unpack_from('<qQqd', packet)
                after = struct.unpack('<q', ctypes.string_at(slot_address, 8))[0]
                if commit <= last_commit or commit & 1 or commit != after:
                    continue
                width, height, row_bytes, pixel_bytes, pixel_format = struct.unpack_from('<5I', packet, 76)
                if not (0 < width <= max_w and 0 < height <= max_h and row_bytes == width * 4 and
                        pixel_bytes == row_bytes * height and pixel_bytes <= stride - slot_header):
                    continue
                candidates.append({'commit': commit, 'frame_id': frame_id, 'qpc': qpc,
                    'world_s': world_s, 'width': width, 'height': height,
                    'fov_deg': struct.unpack_from('<f', packet, 64)[0],
                    'arm_cm': struct.unpack_from('<f', packet, 96)[0],
                    'camera_position_cm': list(struct.unpack_from('<3f', packet, 36)),
                    'camera_quaternion_xyzw': list(struct.unpack_from('<4f', packet, 48)),
                    'view_id': struct.unpack_from('<I', packet, 100)[0],
                    'target_id': struct.unpack_from('<I', packet, 104)[0],
                    'player_id': struct.unpack_from('<i', packet, 108)[0],
                    'map_id': struct.unpack_from('<i', packet, 112)[0],
                    'epoch': struct.unpack_from('<I', packet, 116)[0],
                    'identity_flags': struct.unpack_from('<I', packet, 120)[0]})
            if candidates:
                frame = max(candidates, key=lambda value: value['commit'])
                frames.append(frame)
                last_commit = frame['commit']
            time.sleep(.01)
        advancing = len(frames) >= 3 and all(b['frame_id'] > a['frame_id'] and b['qpc'] > a['qpc']
            for a, b in zip(frames, frames[1:]))
        identity = bool(frames) and all(f['identity_flags'] & 7 == 7 and
            f['view_id'] == f['target_id'] != 0 and f['epoch'] > 0 and f['map_id'] > 0 and
            (args.player is None or f['player_id'] == args.player) for f in frames)
        identity = identity and len({(f['player_id'], f['map_id'], f['epoch']) for f in frames}) == 1
        camera_contract = bool(frames) and all(f['width'] == 1280 and f['height'] == 720 and
            math.isfinite(f['fov_deg']) and abs(f['fov_deg'] - args.fov) <= .05 and
            math.isfinite(f['arm_cm']) and abs(f['arm_cm']) <= .01 for f in frames)
        result = {'writer_pid': writer_pid, 'protocol': version, 'samples': len(frames),
            'frames_advancing': advancing, 'takeover_identity_valid': identity,
            'camera_contract_valid': camera_contract, 'usable_for_aim': advancing and identity and camera_contract,
            'first': frames[0] if frames else None, 'last': frames[-1] if frames else None}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'summary': result, 'frames': frames}, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))
        return 0 if advancing and (not args.require_takeover or result['usable_for_aim']) else 2
    finally:
        if address:
            kernel.UnmapViewOfFile(address)
        kernel.CloseHandle(mapping)


if __name__ == '__main__':
    raise SystemExit(main())
