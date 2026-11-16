"""Generate backend bindings from the canonical ftclogger release checkout.

Usage: python protocol/sync.py --library ../ftclogger --protoc /path/to/protoc
"""
import argparse
import hashlib
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--library', type=Path, required=True)
parser.add_argument('--protoc', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = args.library.resolve() / 'protocol/robot_data.proto'
version = subprocess.check_output([args.protoc, '--version'], text=True).strip()
if version != 'libprotoc 25.5':
    raise SystemExit('Use protoc 3.25.5 (libprotoc 25.5)')
schema = source.read_bytes().replace(b'\r\n', b'\n')
(root / 'protocol/robot_data.proto').write_bytes(schema)
(root / 'protocol/schema.sha256').write_bytes((hashlib.sha256(schema).hexdigest() + '\n').encode('ascii'))
subprocess.run([args.protoc, '--proto_path=' + str(source.parent),
                '--python_out=' + str(root / 'web_driver_station/backend/protocol'), str(source)], check=True)
