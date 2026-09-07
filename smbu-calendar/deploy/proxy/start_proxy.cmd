@echo off
rem ===========================================================================
rem  SMBU Schedule Sync - host egress proxy
rem
rem  The Docker container cannot reach jw.smbu.edu.cn directly (the campus only
rem  completes TLS with this host's authenticated wired interface, and the WSL2
rem  NAT address is refused). This proxy runs ON THE HOST so the container can
rem  tunnel out through a source address the campus trusts.
rem
rem  Double-click to run. Keep the window (it can be minimised) while the
rem  container is in use. Destinations are restricted to smbu.edu.cn, so this
rem  is not an open relay.
rem
rem  Container side (already configured):  SMBU_UPSTREAM_PROXY=http://host.docker.internal:8899
rem ===========================================================================
setlocal
set PYW=%~dp0..\..\backend\.venv\Scripts\pythonw.exe
set SCRIPT=%~dp0host_proxy.py
set LOG=%~dp0proxy.log

if not exist "%PYW%" (
  echo [!] pythonw.exe not found at %PYW%
  echo     Install the backend venv first ^(see deploy/README.md^).
  pause
  exit /b 1
)

echo Starting host proxy on 0.0.0.0:8899 (allowlist: smbu.edu.cn)
echo Log: %LOG%
"%PYW%" "%SCRIPT%" --bind 0.0.0.0 --port 8899 --log-file "%LOG%"
endlocal
