FROM ghcr.io/open-webui/open-webui:v0.12.5@sha256:0caf3d68199bcd8b7051775778691dfa5f3c8a355d1d6484000171b2974f40f9
COPY apply.py /tmp/pl-profile-apply.py
COPY main_profile_sync.py /app/backend/open_webui/utils/main_profile_sync.py
RUN python /tmp/pl-profile-apply.py /app/backend/open_webui \
    && python -m py_compile /app/backend/open_webui/utils/oauth.py /app/backend/open_webui/utils/main_profile_sync.py
