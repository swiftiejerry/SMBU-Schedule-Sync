# TEST FIXTURE ONLY — never deploy this next to the real service.
#
# Local stand-in for the campus CAS + jwapp upstream, serving the REAL captured
# payload shapes so the containerised app can be verified end-to-end without
# touching the live portal (which would risk captcha-locking a student account).
#
# python:3.13-slim is enough — the mock uses only the stdlib http.server, so
# there is no pip install step and the image builds in seconds.
FROM python:3.13-slim
WORKDIR /app
COPY mock_upstream.py ./
COPY raw/ ./raw/
EXPOSE 8799
CMD ["python", "-u", "mock_upstream.py"]
