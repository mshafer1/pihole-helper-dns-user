FROM python:3.14.7-alpine3.23@sha256:218761489de417a6eb0808e264cbdd7043ec6659fe5a61898815e9848536541d AS poetry_installed

RUN apk update && apk add --no-cache curl

RUN python3 -m pip install poetry poetry-plugin-export

FROM poetry_installed AS requirements_listed

COPY ./pyproject.toml ./poetry.lock ./

RUN poetry export -o requirements.txt

FROM python:3.14.7-alpine3.23@sha256:218761489de417a6eb0808e264cbdd7043ec6659fe5a61898815e9848536541d AS COMPILE_LAYER

RUN apk update && apk add --no-cache curl

WORKDIR /app

COPY --from=requirements_listed requirements.txt ./

RUN python3 -m pip install -r requirements.txt

RUN pip wheel -w /wheels -r requirements.txt

FROM python:3.14.7-alpine3.23@sha256:218761489de417a6eb0808e264cbdd7043ec6659fe5a61898815e9848536541d AS app

RUN apk update && apk add --no-cache curl

COPY ./image/health-check.sh /usr/local/bin/health-check.sh

RUN chmod +x /usr/local/bin/health-check.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD /usr/local/bin/health-check.sh

COPY --from=COMPILE_LAYER /wheels /wheels
RUN python3 -m pip install --no-index --find-links=/wheels /wheels/*.whl

WORKDIR /app

COPY ./src /app/

COPY ./pyproject.toml ./poetry.lock ./README.md ./

RUN python3 -m pip install -e .

RUN install -d -o 1000 -g 1000 -m 770 /var/log/pihole-helper-dns-user

# set the user to non-root
USER 1000:1000

# running inside a container, the server will listen on all interfaces, not just localhost
ENV SERVER_HOST=0.0.0.0

WORKDIR /var/log/pihole-helper-dns-user

CMD ["server"]
