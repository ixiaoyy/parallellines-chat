ARG MAIN_BASE_IMAGE
FROM ${MAIN_BASE_IMAGE}
COPY baseline-sha256.json verify_baseline.py /tmp/pl-profile/
RUN python /tmp/pl-profile/verify_baseline.py /app/app /tmp/pl-profile/baseline-sha256.json
COPY app/core/chat_sso.py /app/app/core/chat_sso.py
COPY app/services/chat_sso.py /app/app/services/chat_sso.py
COPY app/schemas/chat_sso.py /app/app/schemas/chat_sso.py
COPY app/services/auth.py /app/app/services/auth.py
RUN python -m py_compile /app/app/core/chat_sso.py /app/app/services/chat_sso.py /app/app/schemas/chat_sso.py /app/app/services/auth.py
