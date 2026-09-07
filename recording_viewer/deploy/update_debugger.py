"""Update the existing Oracle viewer from built files; preserve auth and recordings.

Run from the repository root with the web Python environment. Uses existing
RecordingUploader SSH configuration, verified known hosts and no new credentials.
"""
from __future__ import annotations
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from web_driver_station.backend.recording_uploader import RecordingUploader, UploadSettings, _mkdirs

ROOT = "/opt/ftc-recording-viewer"
FILES = ["recording_viewer/backend/main.py", "web_driver_station/backend/ftclog.py",
         "web_driver_station/backend/protocol_codec.py", "web_driver_station/backend/protocol/robot_data_pb2.py",
         "web_driver_station/backend/debugger_results.py", "web_driver_station/backend/debugger_analysis.py",
         "web_driver_station/backend/debugger_api.py", "recording_viewer/deploy/reload_nginx_after_renewal.sh",
         "recording_viewer/deploy/enable_ip_https.sh"]


def main():
    if not (REPO/"recording_viewer/frontend/dist/index.html").is_file():
        raise SystemExit("Build the viewer frontend first")
    files = FILES + [p.relative_to(REPO).as_posix() for p in (REPO/"recording_viewer/frontend/dist").rglob("*") if p.is_file()]
    settings = UploadSettings.from_environment()
    if settings is None: raise SystemExit("Configure the existing recording uploader first")
    release = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stage, backup = f"{ROOT}/.deploy-stage/{release}", f"{ROOT}/.deploy-backups/{release}"
    client = RecordingUploader(settings)._connect()
    def command(value):
        _, stdout, stderr = client.exec_command(value, timeout=60)
        output, error = stdout.read().decode(), stderr.read().decode()
        if stdout.channel.recv_exit_status() != 0: raise RuntimeError(error or output)
        if output: print(output.strip())
    try:
        with client.open_sftp() as sftp:
            for relative in files:
                target=f"{stage}/{relative}"
                _mkdirs(sftp, target.rsplit("/",1)[0]);sftp.put(str(REPO/relative),target)
        check="from recording_viewer.backend.main import create_library_app; from pathlib import Path; a=create_library_app(Path('/srv/ftc-recordings')); assert any(r.path=='/api/debug/results' for r in a.routes); print('Staged backend imports and routes passed')"
        command(f"cd {shlex.quote(stage)} && {ROOT}/.venv/bin/python -B -c {shlex.quote(check)}")
        publish="""import json,os,shutil,sys
from pathlib import Path
root,stage,backup=map(Path,sys.argv[1:4]);files=json.loads(sys.argv[4])
for relative in files:
    dest=root/relative; saved=backup/relative
    if dest.is_file():
        saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,saved)
    dest.parent.mkdir(parents=True,exist_ok=True)
    temporary=dest.with_name(dest.name+'.deploy-new')
    shutil.copy2(stage/relative,temporary);os.replace(temporary,dest)
print('Published '+str(len(files))+' files; backup '+str(backup))
"""
        # Index is replaced last; old hashed assets remain available to open browsers.
        files.sort(key=lambda p:p.endswith("/index.html"))
        command(f"{ROOT}/.venv/bin/python -B -c {shlex.quote(publish)} {shlex.quote(ROOT)} {shlex.quote(stage)} {shlex.quote(backup)} {shlex.quote(json.dumps(files))}")
        command("sudo systemctl restart ftc-recording-viewer")
        command(f"sudo install -m 0755 {ROOT}/recording_viewer/deploy/reload_nginx_after_renewal.sh /etc/letsencrypt/renewal-hooks/deploy/ftc-recording-viewer-nginx")
        command("sudo /etc/letsencrypt/renewal-hooks/deploy/ftc-recording-viewer-nginx")
        command("curl --retry 8 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8002/api/debug/results")
        command("systemctl is-active ftc-recording-viewer")
        print("Rollback: copy the backed-up files over their matching paths and restart ftc-recording-viewer.")
    finally: client.close()


if __name__ == "__main__": main()
